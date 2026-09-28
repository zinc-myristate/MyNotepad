# -*- coding: utf-8 -*-
"""从备份恢复笔记库 —— 把「我们有备份」变成「我们验证过能恢复」。

为什么单独做一个命令行工具而不是界面按钮：
  · 恢复是**破坏性**操作（覆盖当前库），放在随手可点的界面上风险太高；
  · 真要用到它的时候，往往是界面起不来的时候——那正需要一个不依赖 GUI 的入口。

安全设计（每一步都为了"别把仅剩的数据也弄丢"）：
  1. 先确认应用没在运行（复用单实例互斥量判定），运行中直接拒绝；
  2. 校验备份本身：integrity_check 必须 ok，否则拒绝恢复；
  3. 覆盖前把**当前库**另存为 `backups/pre-restore-<时间戳>.db`（sqlite backup API，页级一致）；
  4. 才真正覆盖，并核对恢复后的笔记数。

用法：
    python restore.py --list                  # 列出可用备份（不看不知道有哪些）
    python restore.py --from latest           # 用最新一份备份恢复
    python restore.py --from notes-20260920-132608.db
    python restore.py --data-dir <目录>        # 指定数据目录（默认自动挑，会打印挑了哪个）
选项：-y / --yes 跳过确认
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_PREFIX = 'notes-'
# 与 app.pyw 里 CreateMutexW 用的名字**必须一致**：app_running() 靠它判断"应用是否在运行"。
# 改名是个静默故障点（改名后这个判断恒为 False，恢复会在应用运行时照做），
# 所以 tests/test_restore.py 有一条测试专门核对两边同名。
MUTEX_NAME = 'MyNotepad_SingleInstance_Mutex'


# ====== 数据目录探测 ======
# 本项目有两处数据目录（源码运行用仓库 data/，打包版用 dist/MyNotepad/data/），
# 且两边可能各自积累笔记。默认挑「notes.db 最新修改」的那一份，并把选择理由打印出来，
# 避免用户在不知情的情况下恢复错库。
def candidate_data_dirs():
    cands = [os.path.join(ROOT, 'data'),
             os.path.join(ROOT, 'dist', 'MyNotepad', 'data')]
    if getattr(sys, 'frozen', False):
        cands.insert(0, os.path.join(os.path.dirname(sys.executable), 'data'))
    seen, out = set(), []
    for d in cands:
        rp = os.path.realpath(d)
        if rp not in seen and os.path.isdir(rp):
            seen.add(rp)
            out.append(rp)
    return out


def pick_data_dir(explicit=None):
    if explicit:
        return os.path.abspath(explicit), '命令行指定'
    dirs = candidate_data_dirs()
    with_db = [d for d in dirs if os.path.exists(os.path.join(d, 'notes.db'))]
    if not with_db:
        return (dirs[0] if dirs else os.path.join(ROOT, 'data')), '没有现成库，用默认位置'
    with_db.sort(key=lambda d: os.path.getmtime(os.path.join(d, 'notes.db')), reverse=True)
    best = with_db[0]
    if len(with_db) > 1:
        others = ', '.join('%s(%s)' % (d, _mtime(os.path.join(d, 'notes.db')))
                           for d in with_db[1:])
        return best, 'notes.db 最近修改（其他候选：%s）' % others
    return best, '唯一候选'


def _mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%m-%d %H:%M')
    except OSError:
        return '?'


# ====== 备份列表 ======
def list_backups(data_dir):
    bdir = os.path.join(data_dir, 'backups')
    if not os.path.isdir(bdir):
        return []
    items = []
    for name in sorted(os.listdir(bdir)):
        if not name.endswith('.db'):
            continue
        path = os.path.join(bdir, name)
        items.append({'name': name, 'path': path,
                      'size': os.path.getsize(path),
                      'mtime': _mtime(path),
                      'notes': count_notes(path),
                      'kind': '恢复前快照' if name.startswith('pre-restore-') else '自动备份'})
    # 按**修改时间**排序而不是文件名：名字排序下 'pre-restore-*' 永远排在 'notes-*' 之后
    # （'p' > 'n'），于是 `--from latest` 会稳定挑中"恢复前快照"—— 那可能正是一次失败恢复
    # 留下的半成品。mtime 才是"谁更新"的真实依据。
    items.sort(key=lambda x: os.path.getmtime(x['path']))
    return items


def latest_backup(data_dir):
    """`--from latest` 该用的那一份：**只从自动备份（notes- 前缀）里挑最新的**。

    刻意排除 premig- / pre-restore- 这类"某次操作前的快照"：它们是回滚凭据，不是备份，
    而且可能是 0 字节半成品。真要用它们，用户应当显式写文件名。
    """
    autos = [b for b in list_backups(data_dir) if b['name'].startswith(BACKUP_PREFIX)]
    return autos[-1] if autos else None


def count_notes(db_path):
    """只读打开数笔记数；打不开返回 None（绝不创建文件）"""
    if not os.path.exists(db_path):
        return None
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db_path.replace('?', '%3f'), uri=True)
        try:
            return c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None


def backup_verdict(db_path):
    """备份能不能用来恢复 —— 返回 (ok, 说明)。

    为什么不能只看 `PRAGMA integrity_check == 'ok'`：SQLite 把**0 字节文件**当成一个合法的
    空库，`integrity_check` 对它返回 'ok'；一个"表结构不对但自身完好"的库同样返回 'ok'。
    于是"备份"如果是 0 字节（备份过程中崩了、磁盘满了留下的半成品），旧版会判它合格、
    拿它覆盖当前库 —— 实测把 3 篇的库覆盖成 0 字节，还打印「✅ 恢复完成」。
    三道闸缺一不可：文件非空 → 完整性 ok → notes 表存在且能数出篇数。
    """
    if not os.path.exists(db_path):
        return False, '文件不存在'
    try:
        size = os.path.getsize(db_path)
    except OSError as exc:
        return False, '读不到文件大小：%s' % exc
    if size == 0:
        return False, '文件是 0 字节（备份没写成功留下的半成品）'
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db_path.replace('?', '%3f'), uri=True)
    except Exception as exc:
        return False, '打不开：%s' % exc
    try:
        # integrity_check 会返回**多行**，第一行不是 'ok' 就说明有问题；
        # 旧版只取 fetchone()，对多行结果等于只看了第一条。
        rows = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
        if not rows or rows[0] != 'ok':
            return False, '完整性校验未通过：%s' % (rows[:3] or '无结果')
        try:
            c.execute("SELECT COUNT(*) FROM notes").fetchone()
        except sqlite3.Error as exc:
            return False, '不是本应用的库（没有 notes 表）：%s' % exc
    except Exception as exc:
        return False, '校验时出错：%s' % exc
    finally:
        c.close()
    return True, 'ok'


def integrity_ok(db_path):
    return backup_verdict(db_path)[0]


# ====== 应用是否在运行 ======
def app_running():
    """查询 app.pyw 的单实例互斥量是否已被持有。

    **只查询、不创建**（OpenMutexW）：早先写成 CreateMutexW + GetLastError，
    结果同一进程第二次调用就会撞上"已存在"——因为第一次创建的句柄还被自己持有，
    于是第二次起会误报"应用正在运行"。
    """
    if not sys.platform.startswith('win'):
        return False
    try:
        import ctypes
        SYNCHRONIZE = 0x00100000
        ERROR_ACCESS_DENIED = 5
        h = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        # 失败要看 errno 再下结论：应用以管理员身份运行、而本工具是普通权限时，
        # OpenMutexW 返回 NULL + ERROR_ACCESS_DENIED —— 那**说明互斥量确实存在**（应用在跑），
        # 旧代码却把它当成"没在运行"，于是在应用运行时照做恢复。
        if ctypes.windll.kernel32.GetLastError() == ERROR_ACCESS_DENIED:
            return True
        return False
    except Exception:
        return False


# ====== 恢复 ======
def snapshot_current(db_path, data_dir):
    """把当前库另存一份（页级一致）。返回快照路径；库不存在返回 None。"""
    if not os.path.exists(db_path):
        return None
    bdir = os.path.join(data_dir, 'backups')
    os.makedirs(bdir, exist_ok=True)
    dest = os.path.join(bdir, 'pre-restore-%s.db' % datetime.now().strftime('%Y%m%d-%H%M%S'))
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)      # 用 backup API 而不是文件拷贝：避免写到一半撕裂
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _copy_over(src_db, dest_path):
    """退路：把 `src_db` 灌进已存在的 `dest_path`（不清空、不删名）。

    仅在 `os.replace` 拿不到独占句柄时使用（Windows 上目标文件被别的进程/本进程的
    残留连接打开时会报 WinError 5）。用 sqlite backup API 直接覆盖目标库内容，
    而不是 `open('wb')` 流式写 —— 后者一失败就留下半截库。
    """
    src = sqlite3.connect('file:%s?mode=ro' % src_db.replace('?', '%3f'), uri=True)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def _atomic_replace(src_db, dest_path):
    """把 `src_db` 的内容装到 `dest_path`，优先做到原子。

    为什么不用 `shutil.copy2`：它是 `open('wb')` 先**截断**再流式写 —— 写到一半失败
    （磁盘满、被杀、文件被占用）就把当前库毁成半截，而旧代码在那条分支里还打印
    "当前库未被改动"（假话），也不用快照回滚。
    这里：用 sqlite backup API 写同目录临时文件 → 校验临时文件 → `os.replace` 原子换名。
    同目录是必须的（跨盘 replace 不原子）。

    拿不到独占句柄时（Windows 常见：目标被占用）退化为 `_copy_over`：此时不再是原子的，
    但仍然是"先把新内容完整写进目标、且源已校验过"，不会产生半截库。
    """
    tmp = dest_path + '.restore-tmp'
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        src = sqlite3.connect('file:%s?mode=ro' % src_db.replace('?', '%3f'), uri=True)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        ok, why = backup_verdict(tmp)
        if not ok:
            raise OSError('临时库校验未通过：%s' % why)
        _replace_with_retry(tmp, dest_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)          # 失败不留半成品；dest_path 交给 _copy_over 兜住
        except OSError:
            pass
        raise


def _replace_with_retry(tmp, dest_path, attempts=5):
    """`os.replace` 撞上短暂占用时重试几次（杀毒/索引器/刚关闭的句柄），再退化为就地覆盖。"""
    import time
    last = None
    for i in range(attempts):
        try:
            os.replace(tmp, dest_path)
            return 'atomic'
        except PermissionError as exc:          # WinError 5：目标被占用
            last = exc
            time.sleep(0.15 * (i + 1))
    _copy_over(tmp, dest_path)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if last is not None:
        # 退路成功也要留痕：这次不是原子替换，出问题时要能从日志看出来
        print('注意：目标库被占用，已退化为就地覆盖（非原子）：%s' % last)
    return 'copy'



def restore(backup_path, data_dir, assume_yes=False, log=print):
    """执行恢复。返回 (成功?, 说明)。所有失败路径都保证不动当前库。"""
    db_path = os.path.join(data_dir, 'notes.db')
    if not os.path.exists(backup_path):
        return False, '备份文件不存在：%s' % backup_path
    if app_running():
        return False, '应用正在运行（含驻留托盘）。请先右键托盘图标退出，再恢复。'
    ok, why = backup_verdict(backup_path)
    if not ok:
        # 文案保留「完整性校验」这个说法：tests/test_restore.py 与用户心智都认它
        return False, '备份完整性校验失败，拒绝用它覆盖当前库（%s）：%s' % (why, backup_path)

    before = count_notes(db_path)
    backup_notes = count_notes(backup_path)
    log('数据目录  : %s' % data_dir)
    log('当前库    : %s（%s 篇）' % (db_path, before if before is not None else '不存在'))
    log('待恢复备份: %s（%s 篇，%s）' % (backup_path, backup_notes, _mtime(backup_path)))
    if not assume_yes:
        try:
            ans = input('确认覆盖当前库？(yes/N) ').strip().lower()
        except EOFError:
            ans = ''
        if ans not in ('y', 'yes'):
            return False, '已取消（没有改动任何文件）'

    # ⚠️ 复查一次"应用是否在运行"：上面那次检查之后夹着 `input()`，可能停留任意久，
    # 用户完全来得及在这期间启动应用 —— 那时 os.replace 会换掉一个正在被写的库。
    if app_running():
        return False, '应用在确认期间启动了。请退出它再恢复（当前库未被改动）。'

    snap = snapshot_current(db_path, data_dir)
    if snap:
        log('已把当前库另存为：%s（后悔药）' % snap)
    try:
        _atomic_replace(backup_path, db_path)
    except Exception as exc:
        return False, ('写入失败（应用可能仍在运行，或磁盘/权限问题）：%s\n'
                       '当前库未被改动，快照在 %s' % (exc, snap or '（无）'))
    after = count_notes(db_path)
    ok2, why2 = backup_verdict(db_path)
    if not ok2 or after != backup_notes:
        # 恢复后必须与备份**篇数一致**：旧版只校验完整性、不比对篇数，
        # 于是"备份 5 篇 → 恢复后 0 篇"也会报成功。
        if snap:
            try:
                _atomic_replace(snap, db_path)
                return False, ('恢复结果与备份不符（%s；期望 %s 篇，实际 %s 篇），'
                               '已回滚到恢复前状态' % (why2, backup_notes, after))
            except Exception as exc:
                return False, ('恢复结果与备份不符，且回滚失败：%s（快照仍在 %s）'
                               % (exc, snap))
        return False, '恢复结果与备份不符：%s（期望 %s 篇，实际 %s 篇）' % (why2, backup_notes, after)
    return True, '恢复完成：%s 篇 -> %s 篇' % (before, after)


def main(argv=None):
    ap = argparse.ArgumentParser(description='从备份恢复我的记事本笔记库')
    ap.add_argument('--list', action='store_true', help='列出可用备份')
    ap.add_argument('--from', dest='source', help='备份文件名或路径，或 latest')
    ap.add_argument('--data-dir', help='数据目录（默认自动挑选并打印理由）')
    ap.add_argument('-y', '--yes', action='store_true', help='跳过确认')
    args = ap.parse_args(argv)

    data_dir, why = pick_data_dir(args.data_dir)
    print('数据目录: %s  （%s）' % (data_dir, why))

    backups = list_backups(data_dir)
    if args.list or not args.source:
        if not backups:
            print('该目录下没有备份（%s）' % os.path.join(data_dir, 'backups'))
            return 1
        print('\n可用备份（越靠后越新）：')
        for b in backups:
            print('  %-34s %-12s %8.1f KB  %s篇  %s'
                  % (b['name'], b['mtime'], b['size'] / 1024,
                     b['notes'] if b['notes'] is not None else '?', b['kind']))
        if not args.source:
            print('\n用 --from <文件名|latest> 执行恢复。')
            return 0

    source = args.source
    if source == 'latest':
        # 只从自动备份里挑最新的（见 latest_backup 的说明：pre-restore-* 不是备份）
        best = latest_backup(data_dir)
        if not best:
            print('没有可用的自动备份（%s）' % os.path.join(data_dir, 'backups'))
            return 1
        source = best['path']
        print('latest 选中：%s（%s 篇）' % (best['name'],
                                        best['notes'] if best['notes'] is not None else '?'))
    elif not os.path.exists(source):
        cand = os.path.join(data_dir, 'backups', source)
        if os.path.exists(cand):
            source = cand
        else:
            print('找不到备份：%s' % args.source)
            return 1

    ok, msg = restore(source, data_dir, assume_yes=args.yes)
    print(('✅ ' if ok else '❌ ') + msg)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
