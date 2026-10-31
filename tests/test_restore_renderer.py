"""运行版前端回滚脚本：真跑一遍「恢复」，不只看它能解析。

「有备份」和「能恢复」是两件事（见 CLAUDE.md《备份与恢复》）。这里在临时目录里搭一个
和 `dist/` 同形状的目录树，用真 PowerShell 执行脚本，验证三件事：
恢复后字节一致、更新新增的文件被移除、任何对不上的情况都**一个字节也不动**。
"""
import hashlib
import json
import os
import shutil
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, 'restore_renderer.ps1')
POWERSHELL = shutil.which('powershell')

pytestmark = pytest.mark.skipif(not POWERSHELL, reason='需要 Windows PowerShell')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.fixture
def tree(tmp_path):
    """dist/MyNotepad/_internal/renderer（已更新）+ dist/_backup（原件 + manifest）"""
    root = tmp_path / 'project'
    renderer = root / 'dist' / 'MyNotepad' / '_internal' / 'renderer'
    backup = root / 'dist' / '_renderer-backup-test'
    (renderer / 'js').mkdir(parents=True)
    (backup / 'js').mkdir(parents=True)
    (root / 'dist' / 'MyNotepad' / 'data').mkdir()
    (root / 'dist' / 'MyNotepad' / 'data' / 'notes.db').write_bytes(b'user data')
    shutil.copy(SCRIPT, root / 'restore_renderer.ps1')

    (backup / 'index.html').write_bytes(b'old html')
    (backup / 'js' / 'a.js').write_bytes(b'old js')
    (renderer / 'index.html').write_bytes(b'new html')
    (renderer / 'js' / 'a.js').write_bytes(b'new js')
    (renderer / 'workspace.css').write_bytes(b'added by update')
    (renderer / 'keep.css').write_bytes(b'not in manifest')
    manifest = [
        {'Path': 'index.html', 'OriginalHash': _sha(backup / 'index.html'), 'InstalledHash': _sha(renderer / 'index.html')},
        {'Path': 'js/a.js', 'OriginalHash': _sha(backup / 'js' / 'a.js'), 'InstalledHash': _sha(renderer / 'js' / 'a.js')},
        {'Path': 'workspace.css', 'OriginalHash': None, 'InstalledHash': _sha(renderer / 'workspace.css')},
    ]
    (backup / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return {'root': root, 'renderer': renderer, 'backup': backup}


def _restore(tree, *extra):
    # 从 PowerShell 7 启动的进程带着它的 PSModulePath，Windows PowerShell 继承后找不到 Get-FileHash
    env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    return subprocess.run(
        [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(tree['root'] / 'restore_renderer.ps1'), '-Backup', str(tree['backup']), *extra],
        capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(tree['root']), env=env)


def _snapshot(tree):
    return {p.relative_to(tree['root']).as_posix(): p.read_bytes()
            for p in tree['root'].rglob('*') if p.is_file()}


@pytest.fixture(autouse=True)
def _app_not_running():
    """脚本在应用运行时拒绝恢复；用户开着记事本时这里的用例不该因此失败"""
    probe = subprocess.run([POWERSHELL, '-NoProfile', '-Command', 'Get-Process -Name MyNotepad -ErrorAction SilentlyContinue'],
                           capture_output=True, text=True)
    if probe.stdout.strip():
        pytest.skip('MyNotepad 正在运行：恢复脚本会按设计拒绝执行')


def test_restore_puts_every_original_back_and_touches_nothing_else(tree):
    r = _restore(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    renderer = tree['renderer']
    assert (renderer / 'index.html').read_bytes() == b'old html'
    assert (renderer / 'js' / 'a.js').read_bytes() == b'old js'
    assert not (renderer / 'workspace.css').exists(), '更新新增的文件要被移除'
    assert (renderer / 'keep.css').read_bytes() == b'not in manifest'
    assert (tree['root'] / 'dist' / 'MyNotepad' / 'data' / 'notes.db').read_bytes() == b'user data'


def test_verify_only_changes_nothing(tree):
    before = _snapshot(tree)
    r = _restore(tree, '-VerifyOnly')
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(tree) == before


def test_refuses_when_installed_file_changed_since_update(tree):
    """有人在更新之后又动过文件：覆盖回去等于悄悄丢掉那次改动，必须整体拒绝（包括排在前面的文件）"""
    (tree['renderer'] / 'js' / 'a.js').write_bytes(b'edited after update')
    before = _snapshot(tree)
    r = _restore(tree)
    assert r.returncode != 0
    assert _snapshot(tree) == before


def test_refuses_when_backup_is_damaged(tree):
    (tree['backup'] / 'index.html').write_bytes(b'corrupted')
    before = _snapshot(tree)
    r = _restore(tree)
    assert r.returncode != 0
    assert _snapshot(tree) == before


def test_refuses_manifest_paths_that_escape_the_renderer(tree):
    outside = tree['root'] / 'dist' / 'MyNotepad' / 'data' / 'notes.db'
    manifest = json.loads((tree['backup'] / 'manifest.json').read_text(encoding='utf-8'))
    manifest.append({'Path': '../../data/notes.db', 'OriginalHash': None, 'InstalledHash': _sha(outside)})
    (tree['backup'] / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    before = _snapshot(tree)
    r = _restore(tree)
    assert r.returncode != 0
    assert _snapshot(tree) == before


def test_refuses_backup_outside_dist(tree, tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    shutil.copytree(tree['backup'], elsewhere)
    tree = dict(tree, backup=elsewhere)
    before = _snapshot(tree)
    r = _restore(tree)
    assert r.returncode != 0
    assert _snapshot(tree) == before


def test_a_half_finished_restore_can_simply_be_run_again(tree):
    """半路失败（比如文件被占用）之后再跑一次要能收尾，而不是因为"已恢复的文件对不上"被拒绝"""
    (tree['renderer'] / 'index.html').write_bytes(b'old html')          # 第一个文件已恢复
    r = _restore(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tree['renderer'] / 'js' / 'a.js').read_bytes() == b'old js'
    assert not (tree['renderer'] / 'workspace.css').exists()
    r = _restore(tree)                                                  # 全部恢复后再跑也是安全的空操作
    assert r.returncode == 0, r.stdout + r.stderr
