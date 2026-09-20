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
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_PREFIX = 'notes-'


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
    items.sort(key=lambda x: x['name'])
    return items


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


def integrity_ok(db_path):
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db_path.replace('?', '%3f'), uri=True)
        try:
            return c.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
        finally:
            c.close()
    except Exception:
        return False


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
        h = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False,
                                             "MyNotepad_SingleInstance_Mutex")
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
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


def restore(backup_path, data_dir, assume_yes=False, log=print):
    """执行恢复。返回 (成功?, 说明)。所有失败路径都保证不动当前库。"""
    db_path = os.path.join(data_dir, 'notes.db')
    if not os.path.exists(backup_path):
        return False, '备份文件不存在：%s' % backup_path
    if app_running():
        return False, '应用正在运行（含驻留托盘）。请先右键托盘图标退出，再恢复。'
    if not integrity_ok(backup_path):
        return False, '备份文件完整性校验失败，拒绝用它覆盖当前库：%s' % backup_path

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

    snap = snapshot_current(db_path, data_dir)
    if snap:
        log('已把当前库另存为：%s（后悔药）' % snap)
    try:
        shutil.copy2(backup_path, db_path)
    except OSError as exc:
        # 库被占用（应用在跑）时会撞共享冲突：此时当前库还没被改动，直接报错退出
        return False, ('写入失败（应用可能仍在运行，或文件被占用）：%s\n'
                       '当前库未被改动，快照在 %s' % (exc, snap or '（无）'))
    after = count_notes(db_path)
    if not integrity_ok(db_path):
        # 理论上不会发生（备份已校验过）；真发生就把快照放回去
        if snap:
            shutil.copy2(snap, db_path)
            return False, '恢复后完整性校验失败，已回滚到恢复前状态'
        return False, '恢复后完整性校验失败'
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
        if not backups:
            print('没有备份可用')
            return 1
        source = backups[-1]['path']
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
