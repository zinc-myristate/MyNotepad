# -*- coding: utf-8 -*-
"""一键全库导出 zip 测试"""
import os
import sqlite3
import zipfile


def test_export_all_to_zip(api, backend_mod, tmp_path):
    # 造数据：2 笔记 + 1 附件文件 + 1 背景文件
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '笔记A', 'content': '{"ops":[{"insert":"内容\\n"}]}'})
    api.notes_create()

    att = os.path.join(backend_mod.ATTACH_DIR, nid)
    os.makedirs(att, exist_ok=True)
    att_file = os.path.join(att, 'img_test.png')
    with open(att_file, 'wb') as f:
        f.write(b'fake-png-bytes')

    bg_dir = os.path.join(backend_mod.DATA_DIR, 'backgrounds')
    os.makedirs(bg_dir, exist_ok=True)
    bg_file = os.path.join(bg_dir, 'bg.png')
    with open(bg_file, 'wb') as f:
        f.write(b'fake-bg-bytes')

    out = str(tmp_path / 'backup.zip')
    assert api.export_all_to_zip(out) is True

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert 'notes.db' in names
        assert any(n.startswith('attachments/') and n.endswith('img_test.png') for n in names)
        assert any(n.startswith('backgrounds/') and n.endswith('bg.png') for n in names)
        # 附件字节一致
        with zf.open(next(n for n in names if n.endswith('img_test.png'))) as f:
            assert f.read() == b'fake-png-bytes'
        # 解出 db 行数一致
        zf.extract('notes.db', str(tmp_path / 'x'))
        db = sqlite3.connect(str(tmp_path / 'x' / 'notes.db'))
        try:
            assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0] == 2
        finally:
            db.close()


def test_export_all_empty_db(api, tmp_path):
    out = str(tmp_path / 'empty.zip')
    assert api.export_all_to_zip(out) is True
    with zipfile.ZipFile(out) as zf:
        assert 'notes.db' in zf.namelist()


def test_export_all_wrapper(app_ns, tmp_path, monkeypatch):
    """AppApi.export_all：monkeypatch 文件对话框后返回保存路径"""
    out = str(tmp_path / 'via-wrapper.zip')
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: out)
    assert app_ns['api'].export_all() == out
    assert os.path.isfile(out)
