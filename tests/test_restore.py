# -*- coding: utf-8 -*-
"""备份恢复：把「我们有备份」变成「我们验证过能从备份恢复」。

恢复是破坏性操作，所以这一组测试的重点全在**失败路径绝不动当前库**：
备份不存在 / 备份损坏 / 应用在运行 / 用户没确认 —— 都必须原样保留当前数据。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import restore  # noqa: E402


def _mk_note(backend, title, content='{"ops":[{"insert":"x\\n"}]}'):
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': title, 'content': content})
    return nid


def _titles(db_path):
    """用户看得见的笔记标题（不含回收站里的——软删除只是置了 deleted_at）"""
    c = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    try:
        return sorted(r[0] for r in c.execute(
            "SELECT title FROM notes WHERE deleted_at IS NULL").fetchall())
    finally:
        c.close()


class TestListBackups:
    def test_empty_when_no_dir(self, tmp_path):
        assert restore.list_backups(str(tmp_path)) == []

    def test_lists_automatic_and_pre_restore(self, api, backend_mod, tmp_path):
        _mk_note(backend_mod, '甲')
        backend_mod.backup_database()
        # 造一份"恢复前快照"，检查分类标注
        os.makedirs(tmp_path / 'backups', exist_ok=True)
        (tmp_path / 'backups' / 'pre-restore-20260101-000000.db').write_bytes(
            (tmp_path / 'notes.db').read_bytes())
        items = restore.list_backups(str(tmp_path))
        kinds = {i['name']: i['kind'] for i in items}
        assert any(v == '自动备份' for v in kinds.values())
        assert kinds.get('pre-restore-20260101-000000.db') == '恢复前快照'
        assert all(i['notes'] == 1 for i in items if i['notes'] is not None)

    def test_count_notes_does_not_create_file(self, tmp_path):
        missing = tmp_path / 'nope.db'
        assert restore.count_notes(str(missing)) is None
        assert not missing.exists(), '只读探测不能留下副作用（sqlite3.connect 会建空文件）'


class TestRestoreHappyPath:
    def test_restores_purged_notes(self, api, backend_mod, tmp_path):
        """核心场景：备份 → 彻底删除（回收站也清掉）→ 恢复 → 笔记回来"""
        a = _mk_note(backend_mod, '要保住的笔记')
        _mk_note(backend_mod, '另一篇')
        backend_mod.backup_database()
        backups = [b for b in restore.list_backups(str(tmp_path)) if b['kind'] == '自动备份']
        assert backups, '应已生成自动备份'

        backend_mod.api.notes_delete(a)          # 先进回收站
        backend_mod.api.notes_purge(a)           # 再彻底删除（备份之外已无副本）
        assert '要保住的笔记' not in _titles(str(tmp_path / 'notes.db'))

        ok, msg = restore.restore(backups[-1]['path'], str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert ok, msg
        assert '要保住的笔记' in _titles(str(tmp_path / 'notes.db'))

    def test_makes_pre_restore_snapshot(self, api, backend_mod, tmp_path):
        _mk_note(backend_mod, '现在的内容')
        backend_mod.backup_database()
        b = [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '自动备份'][-1]
        backend_mod.api.notes_delete(_mk_note(backend_mod, '恢复前才有的笔记'))

        ok, _ = restore.restore(b['path'], str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert ok
        snaps = [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '恢复前快照']
        assert snaps, '覆盖前必须留一份当前库快照（后悔药）'

    def test_reports_counts(self, api, backend_mod, tmp_path):
        _mk_note(backend_mod, '唯一一篇')
        backend_mod.backup_database()
        b = [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '自动备份'][-1]
        lines = []
        ok, msg = restore.restore(b['path'], str(tmp_path), assume_yes=True, log=lines.append)
        assert ok and '1 篇' in msg, msg
        assert any('待恢复备份' in ln for ln in lines), '应打印将要做什么，而不是默默覆盖'


class TestRestoreRefuses:
    """失败路径：一律不能改动当前库"""

    def test_missing_backup(self, api, backend_mod, tmp_path):
        _mk_note(backend_mod, '甲')
        before = _titles(str(tmp_path / 'notes.db'))
        ok, msg = restore.restore(str(tmp_path / 'nope.db'), str(tmp_path), assume_yes=True,
                                 log=lambda *_: None)
        assert not ok and '不存在' in msg
        assert _titles(str(tmp_path / 'notes.db')) == before

    def test_corrupt_backup_rejected(self, api, backend_mod, tmp_path):
        _mk_note(backend_mod, '甲')
        bad = tmp_path / 'backups' / 'notes-broken.db'
        os.makedirs(bad.parent, exist_ok=True)
        bad.write_bytes('这不是一个 SQLite 文件'.encode('utf-8') * 20)
        before = _titles(str(tmp_path / 'notes.db'))
        ok, msg = restore.restore(str(bad), str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert not ok and '完整性' in msg
        assert _titles(str(tmp_path / 'notes.db')) == before, '校验失败绝不能覆盖'

    def test_refuses_while_app_running(self, api, backend_mod, tmp_path, monkeypatch):
        _mk_note(backend_mod, '甲')
        backend_mod.backup_database()
        b = [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '自动备份'][-1]
        monkeypatch.setattr(restore, 'app_running', lambda: True)
        before = _titles(str(tmp_path / 'notes.db'))
        ok, msg = restore.restore(b['path'], str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert not ok and '正在运行' in msg, msg
        assert _titles(str(tmp_path / 'notes.db')) == before

    def test_cancel_changes_nothing(self, api, backend_mod, tmp_path, monkeypatch):
        _mk_note(backend_mod, '甲')
        backend_mod.backup_database()
        b = [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '自动备份'][-1]
        monkeypatch.setattr('builtins.input', lambda *_: 'n')
        before = _titles(str(tmp_path / 'notes.db'))
        ok, msg = restore.restore(b['path'], str(tmp_path), assume_yes=False, log=lambda *_: None)
        assert not ok and '取消' in msg
        assert _titles(str(tmp_path / 'notes.db')) == before
        assert not [x for x in restore.list_backups(str(tmp_path)) if x['kind'] == '恢复前快照'], \
            '取消时不该留下快照'


class TestDataDirPick:
    def test_explicit_wins(self, tmp_path):
        d, why = restore.pick_data_dir(str(tmp_path))
        assert os.path.abspath(str(tmp_path)) == d and '命令行' in why

    def test_picks_most_recently_modified(self, tmp_path, monkeypatch):
        a, b = tmp_path / 'a', tmp_path / 'b'
        for d in (a, b):
            d.mkdir()
        (a / 'notes.db').write_bytes(b'x')
        (b / 'notes.db').write_bytes(b'x')
        os.utime(a / 'notes.db', (1000, 1000))
        os.utime(b / 'notes.db', (2000, 2000))
        monkeypatch.setattr(restore, 'candidate_data_dirs', lambda: [str(a), str(b)])
        picked, why = restore.pick_data_dir()
        assert picked == str(b), '应挑 notes.db 更新的那份'
        assert '最近修改' in why, '必须打印挑选理由（否则用户不知道恢复了哪本）'
        assert str(a) in why, '应把其他候选也说出来'


class TestMainCli:
    def test_list_and_no_source(self, api, backend_mod, tmp_path, capsys):
        _mk_note(backend_mod, '甲')
        backend_mod.backup_database()
        rc = restore.main(['--data-dir', str(tmp_path), '--list'])
        out = capsys.readouterr().out
        assert rc == 0 and 'notes-' in out and '可用备份' in out

    def test_missing_source_fails(self, tmp_path, capsys):
        rc = restore.main(['--data-dir', str(tmp_path), '--from', 'nope.db'])
        assert rc == 1
        assert '找不到备份' in capsys.readouterr().out

    def test_latest_and_yes(self, api, backend_mod, tmp_path, capsys):
        _mk_note(backend_mod, '要恢复的')
        backend_mod.backup_database()
        backend_mod.api.notes_delete(
            [n['id'] for n in backend_mod.api.notes_list()][0])
        rc = restore.main(['--data-dir', str(tmp_path), '--from', 'latest', '-y'])
        out = capsys.readouterr().out
        assert rc == 0 and '恢复完成' in out
        assert '要恢复的' in _titles(str(tmp_path / 'notes.db'))
