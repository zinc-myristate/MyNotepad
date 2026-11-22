# -*- coding: utf-8 -*-
"""一条命令完成打包：备份运行数据 -> PyInstaller 构建 -> 还原数据 -> 修好快捷方式。

为什么需要这个脚本：`pyinstaller MyNotepad.spec` 会**清空整个 dist/MyNotepad**，
而运行数据（笔记库、附件、背景图、自定义图标）就躺在 dist/MyNotepad/data/ 里。
手工打包时只要漏掉「先备份再还原」，用户的全部笔记就没了——本仓库已经踩过一次，
靠事前快照才救回 12.6MB 笔记库。

用法：
    python build.py              # 完整流程（推荐）：关实例 → 存程序回滚快照 → 备份用户数据 → PyInstaller
                                 #   → 还原用户数据并逐字节核对 → 剪枝 → 修快捷方式 → 冒烟
    python build.py --dry-run    # 只打印将要执行的步骤，不实际构建
    python build.py --no-restore # 构建完不还原数据（调试用）
    python build.py --keep-backup  # 这次不剪枝（用户数据备份和程序回滚快照都留着）
    python build.py --rollback   # 新包有问题：把程序换回最近一份回滚快照（不动 data、resources）

两种备份，各管一半：
    dist/_predist-backup-<时间>/  用户数据（data、resources）+ 旧 exe：构建时 dist/MyNotepad 会被整个清空，靠它还原。默认留最近 1 份
    dist/_rollback-<时间>/        打包前的程序（除 data、resources 以外的全部，含 _internal）：新包有问题时靠它换回去。默认留最近 2 份

退出码：0 成功；非 0 失败（失败时会尽力把旧程序和数据都还原回来，备份仍留在 dist/ 里）。
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, 'MyNotepad.spec')
DIST = os.path.join(ROOT, 'dist', 'MyNotepad')
EXE = os.path.join(DIST, 'MyNotepad.exe')
DATA = os.path.join(DIST, 'data')
RESOURCES = os.path.join(DIST, 'resources')
BACKUP_ROOT = os.path.join(ROOT, 'dist')

# 打包必须一并保护的目录（相对 dist/MyNotepad）
PROTECTED = ('data', 'resources')

# build.py 自己存的程序回滚快照：`_rollback-<年月日>-<时分秒>`。剪枝和 --rollback 只认这个格式，
# 手工存的（比如 `_rollback-20261120-before-build`）不碰
ROLLBACK_RE = re.compile(r'_rollback-\d{8}-\d{6}')

SHORTCUT_NAME = '我的记事本.lnk'


def log(msg):
    try:
        print('[build] %s' % msg, flush=True)
    except UnicodeEncodeError:
        # Windows 中文控制台是 GBK。日志里只要有 ✅ 这类字符，print 就会抛
        # UnicodeEncodeError —— 偏偏收尾那行就是「打包成功」，于是**构建明明成功、
        # 脚本却以退出码 1 结束**（实测踩到）。这里退化成可编码版本，只丢字符不丢日志。
        enc = sys.stdout.encoding or 'utf-8'
        print('[build] %s' % msg.encode(enc, 'replace').decode(enc, 'replace'), flush=True)


def human(path):
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else \
            sum(os.path.getsize(os.path.join(r, f))
                for r, _d, fs in os.walk(path) for f in fs)
    except OSError:
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return '%.1f %s' % (size, unit)
        size /= 1024.0


def make_backup():
    """把所有 PROTECTED 目录 + 旧 exe 快照到 dist/_predist-backup-<时间戳>/"""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(BACKUP_ROOT, '_predist-backup-%s' % stamp)
    os.makedirs(dest, exist_ok=True)
    saved = []
    for name in PROTECTED:
        src = os.path.join(DIST, name)
        if os.path.exists(src):
            shutil.copytree(src, os.path.join(dest, name), dirs_exist_ok=True)
            saved.append('%s (%s)' % (name, human(src)))
    if os.path.exists(EXE):
        shutil.copy2(EXE, os.path.join(dest, 'MyNotepad.exe.bak'))
    if not saved:
        log('dist 里没有可保护的运行数据（可能是首次打包）')
        shutil.rmtree(dest, ignore_errors=True)
        return None
    log('已备份：%s' % '、'.join(saved))
    return dest


def restore(backup):
    """从备份还原 PROTECTED 目录（覆盖构建产物里的同名目录）"""
    if not backup or not os.path.isdir(backup):
        log('没有可用备份，跳过还原')
        return
    for name in PROTECTED:
        src = os.path.join(backup, name)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(DIST, name)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        log('已还原 %s/ (%s)' % (name, human(dst)))


def prune_backups(keep=1):
    """只保留最近 keep 份备份（dist 目录别越堆越大）。

    匹配 `_predist*` 而非 `_predist-*`：早期版本把备份命名为
    `_predist-data-backup-<时间戳>`，要能一并清掉。

    ⚠️ **必须按修改时间排序，不能按名字**：两套命名在同一目录里共存时，名字序与时间序
    是相反的 —— `_predist-backup-<新>` 永远**小于** `_predist-data-backup-<旧>`
    （'b' < 'd'），于是"保留名字最大的 keep 份"会**把刚做的那份剪掉、留下几天前的旧备份**。
    而这份备份正是构建失败/还原失败时的回滚凭据（`main()` 里失败分支就靠它），
    剪错等于把后悔药丢了。同一条教训在 `restore.py` 的 `latest_backup()` 上也踩过。
    """
    try:
        items = [d for d in os.listdir(BACKUP_ROOT)
                 if d.startswith('_predist') and os.path.isdir(os.path.join(BACKUP_ROOT, d))]
        # 新 → 旧
        items.sort(key=lambda d: os.path.getmtime(os.path.join(BACKUP_ROOT, d)), reverse=True)
    except OSError:
        return
    for old in items[keep:]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
        log('清理旧备份 %s' % old)


def _tree_digest(root):
    """root 下每个文件：相对路径 → (大小, sha256)"""
    out = {}
    for r, _dirs, files in os.walk(root):
        for f in files:
            path = os.path.join(r, f)
            h = hashlib.sha256()
            with open(path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''):
                    h.update(chunk)
            out[os.path.relpath(path, root).replace(os.sep, '/')] = (os.path.getsize(path), h.hexdigest())
    return out


def protected_digest():
    """用户文件（PROTECTED 目录里的每个文件）的大小和哈希：打包前算一次，还原后再算一次，必须一样"""
    return {name + '/' + rel: v
            for name in PROTECTED if os.path.isdir(os.path.join(DIST, name))
            for rel, v in _tree_digest(os.path.join(DIST, name)).items()}


def digest_diff(before, after):
    """两次 protected_digest 的差别：(变了的, 少了的, 多出来的)，各是排好序的相对路径列表"""
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    return changed, sorted(before.keys() - after.keys()), sorted(after.keys() - before.keys())


def snapshot_program():
    """把打包前的程序（dist/MyNotepad 里除了 PROTECTED 以外的全部，含 _internal）存到 dist/_rollback-<时间>/。

    为什么：make_backup 只存用户数据和一个 exe，新包有问题时换不回旧程序 —— Python 运行时、前端文件都在
    _internal 里。以前每次打包前手工拷一份，十几次下来攒了 2 GB 没人清。
    目标目录先建好再逐项拷进去，不对整个 dist/MyNotepad 用 copytree：copytree 最后会把源目录的修改时间
    拷到目标上（手工存的 `_rollback-20261120-before-build` 显示的就是 11-18 的时间）
    """
    if not os.path.exists(EXE):
        log('dist 里还没有程序（可能是首次打包），不存回滚快照')
        return None
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(BACKUP_ROOT, '_rollback-%s' % stamp)
    os.makedirs(dest)
    try:
        for name in sorted(os.listdir(DIST)):
            if name in PROTECTED:
                continue
            src = os.path.join(DIST, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dest, name))
            else:
                shutil.copy2(src, os.path.join(dest, name))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)        # 拷了一半的快照不能留着冒充能用的
        raise
    log('已存程序回滚快照：%s（%s，不含 %s）' % (os.path.basename(dest), human(dest), '、'.join(PROTECTED)))
    return dest


def rollback_snapshots():
    """build.py 自己存的回滚快照（目录名），新 → 旧。

    按名字里的时间戳排，不按修改时间：名字格式是定死的，名字序就是时间序；
    修改时间却会被 copytree 改成源目录的（见 snapshot_program）
    """
    try:
        names = [d for d in os.listdir(BACKUP_ROOT)
                 if ROLLBACK_RE.fullmatch(d) and os.path.isdir(os.path.join(BACKUP_ROOT, d))]
    except OSError:
        return []
    return sorted(names, reverse=True)


def prune_rollbacks(keep=2):
    """只留最近 keep 份 build.py 存的回滚快照（每份两百多 MB）"""
    for old in rollback_snapshots()[keep:]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
        log('清理旧的回滚快照 %s' % old)


def restore_program(snapshot):
    """把 dist/MyNotepad 里的程序换成 snapshot 里的那份；PROTECTED 目录（用户数据）不动"""
    if not os.path.isfile(os.path.join(snapshot, 'MyNotepad.exe')):
        raise RuntimeError('快照里没有 MyNotepad.exe，不像一份完整的程序：%s' % snapshot)
    os.makedirs(DIST, exist_ok=True)
    for name in os.listdir(DIST):
        if name in PROTECTED:
            continue
        path = os.path.join(DIST, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    for name in os.listdir(snapshot):
        if name in PROTECTED:
            continue
        src = os.path.join(snapshot, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(DIST, name))
        else:
            shutil.copy2(src, os.path.join(DIST, name))
    log('程序已换回 %s' % os.path.basename(snapshot))


def kill_running_app():
    """打包前关掉正在运行的实例。

    必须做：app 运行时 SQLite 与 data/error.log 处于占用状态，PyInstaller 在
    COLLECT 阶段删除 dist/MyNotepad 会撞 `[WinError 32] 另一个程序正在使用此文件`
    （实测就是被 data/error.log 卡住）。这里主动收尾，避免打包中途失败。
    """
    if not sys.platform.startswith('win'):
        return 0
    script = (
        "Get-Process -Name MyNotepad -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.Kill(); $_.WaitForExit(8000) | Out-Null }; "
        "Get-Process -Name MyNotepad -ErrorAction SilentlyContinue | Measure-Object | "
        "Select-Object -ExpandProperty Count"
    )
    try:
        proc = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                              capture_output=True, text=True, timeout=30)
        remaining = (proc.stdout or '').strip().splitlines()
        left = int(remaining[-1]) if remaining and remaining[-1].isdigit() else 0
    except Exception as exc:
        log('检查运行中实例失败（继续）：%s' % exc)
        return 0
    if left:
        log('警告：仍有 %d 个 MyNotepad 进程未退出，打包可能因文件占用失败' % left)
    else:
        log('已确保没有正在运行的实例')
    # 给 Windows 一点时间释放文件句柄
    time.sleep(1.5)
    return left


def run_build():
    cmd = [sys.executable, '-m', 'PyInstaller', SPEC, '--noconfirm', '--clean']
    log('执行：%s' % ' '.join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        raise RuntimeError('PyInstaller 构建失败（exit %s）' % proc.returncode)
    if not os.path.exists(EXE):
        raise RuntimeError('构建结束但找不到产物：%s' % EXE)
    log('构建完成：%s (%s)' % (EXE, human(EXE)))
    # 与 release.yml 同一道闸门：缺许可文本、FFmpeg 混回来都算构建失败（失败时 main() 会还原数据）
    gate = os.path.join(ROOT, 'build_resources', 'collect_licenses.py')
    if subprocess.run([sys.executable, gate, '--verify', DIST], cwd=ROOT).returncode != 0:
        raise RuntimeError('产物合规检查未通过（见上方输出）')


def _shortcut_dirs():
    """桌面（用户 + 公共）与开始菜单里可能存在的快捷方式路径"""
    home = os.path.expanduser('~')
    cands = [
        os.path.join(home, 'Desktop', SHORTCUT_NAME),
        os.path.join(os.environ.get('PUBLIC', r'C:\Users\Public'), 'Desktop', SHORTCUT_NAME),
        os.path.join(os.environ.get('APPDATA', ''),
                     r'Microsoft\Windows\Start Menu\Programs', SHORTCUT_NAME),
    ]
    return [p for p in cands if p and os.path.exists(p)]


def fix_shortcuts():
    """把快捷方式的 Target 指回新 exe。

    图标**一律用 exe 内嵌资源**（IconLocation 留空 => 取 Target 的图标）。
    为什么不再指向 resources/icon.ico：重建 dist 会把该文件还原成仓库默认图标，
    而「PyInstaller 6.x 把 resources 收进 _internal/」的旧坑也让它随时可能不存在。
    指向 exe 后，重新打包不会再让快捷方式变成白图标。
    """
    targets = _shortcut_dirs()
    if not targets:
        log('未找到快捷方式（可跳过）')
        return 0
    # 用 PowerShell 的 WScript.Shell 修改 .lnk（避免额外依赖）
    fixed = 0
    for lnk in targets:
        ps = (
            "$ErrorActionPreference='Stop';"
            "$w=New-Object -ComObject WScript.Shell;"
            "$s=$w.CreateShortcut('%s');"
            "$s.TargetPath='%s';"
            "$s.WorkingDirectory='%s';"
            "$s.IconLocation='%s,0';"
            "$s.Save()" % (lnk.replace("'", "''"), EXE.replace("'", "''"),
                           DIST.replace("'", "''"), EXE.replace("'", "''"))
        )
        proc = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-Command', ps], capture_output=True, text=True)
        if proc.returncode == 0:
            log('已更新快捷方式：%s（图标指向 exe 内嵌资源）' % lnk)
            fixed += 1
        else:
            log('快捷方式更新失败：%s -> %s' % (lnk, (proc.stderr or '').strip()[:200]))
    if fixed:
        # 刷新图标缓存，否则资源管理器仍显示旧图标
        subprocess.run(['ie4uinit.exe', '-show'], capture_output=True)
        log('已刷新图标缓存')
    return fixed


def smoke_check():
    """起一次 exe 确认没崩（用独立数据目录，绝不碰真实 data/）"""
    import tempfile
    import time
    tmp = tempfile.mkdtemp(prefix='mynotepad_smoke_')
    env = dict(os.environ, MYNOTEPAD_DATA_DIR=tmp)
    log('冒烟启动（数据目录隔离：%s）' % tmp)
    proc = subprocess.Popen([EXE], env=env)
    try:
        time.sleep(12)
        if proc.poll() is not None:
            raise RuntimeError('exe 在 12 秒内退出（exit code %s）' % proc.returncode)
        log('冒烟通过：进程存活 12 秒')
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description='备份-构建-还原-修快捷方式 一条命令打包')
    ap.add_argument('--dry-run', action='store_true', help='只打印步骤，不构建')
    ap.add_argument('--no-restore', action='store_true', help='构建后不还原数据')
    ap.add_argument('--no-smoke', action='store_true', help='跳过冒烟启动')
    ap.add_argument('--no-kill', action='store_true', help='不自动关闭正在运行的实例')
    ap.add_argument('--keep-backup', action='store_true', help='这次不剪枝（数据备份和回滚快照都留着）')
    ap.add_argument('--keep', type=int, default=1, help='保留最近几份用户数据备份（默认 1）')
    ap.add_argument('--keep-rollback', type=int, default=2, help='保留最近几份程序回滚快照（默认 2）')
    ap.add_argument('--prune-only', action='store_true',
                    help='只清理 dist 下的历史备份再退出（不构建），配合 --keep / --keep-rollback 指定保留份数')
    ap.add_argument('--rollback', action='store_true',
                    help='把程序换回最近一份回滚快照再退出（不构建，不动 data、resources）')
    args = ap.parse_args(argv)
    keep, keep_rollback = max(1, args.keep), max(1, args.keep_rollback)

    log('项目根目录：%s' % ROOT)
    log('产物路径  ：%s' % DIST)
    if args.prune_only:
        # 备份会随每次打包累积（数据备份每份约 50 MB，回滚快照每份两百多 MB），这里给一个不改动任何构建产物的清理入口
        log('只清理历史备份：数据备份留最近 %d 份、回滚快照留最近 %d 份（目录：%s）'
            % (keep, keep_rollback, BACKUP_ROOT))
        prune_backups(keep=keep)
        prune_rollbacks(keep=keep_rollback)
        return 0
    if args.rollback:
        snaps = rollback_snapshots()
        if not snaps:
            log('没有 build.py 存的回滚快照（dist/_rollback-<年月日>-<时分秒>），没法回滚')
            return 1
        if not args.no_kill:
            kill_running_app()
        try:
            restore_program(os.path.join(BACKUP_ROOT, snaps[0]))
        except Exception as exc:
            log('回滚失败：%s（快照还在 %s，可以再试一次）' % (exc, snaps[0]))
            return 1
        return 0
    if args.dry_run:
        log('--dry-run：将执行 关实例 -> 存回滚快照 -> 备份 -> PyInstaller -> 还原并核对 -> 剪枝 -> 修快捷方式 -> 冒烟')
        for name in PROTECTED:
            p = os.path.join(DIST, name)
            log('  保护 %-10s %s' % (name, human(p) if os.path.exists(p) else '(不存在)'))
        log('  现有回滚快照 %d 份：%s' % (len(rollback_snapshots()), '、'.join(rollback_snapshots()) or '无'))
        return 0

    backup = snapshot = before = None
    try:
        if not args.no_kill:
            kill_running_app()
        before = protected_digest()
        backup = make_backup()
        snapshot = snapshot_program()
        run_build()
    except Exception as exc:
        log('构建阶段失败：%s' % exc)
        # PyInstaller 一开始就清空了 dist/MyNotepad：程序和数据都要还原，不然用户手里一个能用的都没有
        if snapshot:
            log('正在把打包前的程序换回来 …')
            try:
                restore_program(snapshot)
            except Exception as exc2:
                log('换回程序失败：%s（快照还在 %s）' % (exc2, snapshot))
        if backup:
            log('正在从备份还原，避免丢失运行数据 …')
            restore(backup)
        return 1

    if not args.no_restore:
        restore(backup)
        changed, missing, extra = digest_diff(before, protected_digest())
        if changed or missing or extra:
            for label, names in (('变了', changed), ('少了', missing), ('多了', extra)):
                if names:
                    log('  %s %d 个：%s' % (label, len(names), '、'.join(names[:10])))
            log('还原后的用户文件和打包前不一样！这次不剪枝，备份在 %s' % backup)
            return 1
        log('核对通过：用户文件 %d 个，与打包前逐字节一致' % len(before))
    if not args.keep_backup:
        prune_backups(keep=keep)
        prune_rollbacks(keep=keep_rollback)
    fix_shortcuts()
    if not args.no_smoke:
        try:
            smoke_check()
        except Exception as exc:
            log('冒烟失败：%s' % exc)
            if snapshot:
                log('要换回打包前的程序：python build.py --rollback')
            return 1
    log('全部完成（可执行文件已生成）  产物：%s' % EXE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
