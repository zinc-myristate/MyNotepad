# -*- coding: utf-8 -*-
"""图片外置迁移测试：base64 内嵌 Delta → attachments 引用 dict"""
import base64
import json
import os

# 1x1 红色 PNG
PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
PNG_URI = 'data:image/png;base64,' + PNG_B64
RAW_PNG = base64.b64decode(PNG_B64)

DELTA_IMG = json.dumps({'ops': [
    {'insert': '标题\n'},
    {'insert': {'image': PNG_URI}},
    {'insert': '尾部\n'},
]})


def _make_note_with_image(api, title='图笔记'):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': DELTA_IMG})
    return nid


def test_migrate_externalizes_and_dictifies(api, backend_mod):
    nid = _make_note_with_image(api)
    # 手工造一条含图的版本行
    vid = api.versions_create(nid, '图笔记', DELTA_IMG)
    assert vid is not None

    assert backend_mod.migrate_images() == 2  # notes + versions 各一行

    # 文件落盘
    note_dir = os.path.join(backend_mod.ATTACH_DIR, nid)
    files = os.listdir(note_dir)
    assert len(files) == 1 and files[0].startswith('img_') and files[0].endswith('.png')
    with open(os.path.join(note_dir, files[0]), 'rb') as f:
        assert f.read() == RAW_PNG

    # attachments 行
    rows = api.attachments_list(nid)
    assert len(rows) == 1 and rows[0]['type'] == 'image' and rows[0]['filename'] == files[0]

    # notes.content 已 dict 化（含 id/storedPath，无 base64）
    got = json.loads(api.notes_get(nid)['content'])
    v = got['ops'][1]['insert']['image']
    assert isinstance(v, dict) and v['id'] == rows[0]['id'] and v['storedPath'].endswith(files[0])
    assert 'data:image' not in api.notes_get(nid)['content']

    # versions 同步 dict 化
    ver = api.versions_get(vid)
    vv = json.loads(ver['content'])['ops'][1]['insert']['image']
    assert isinstance(vv, dict)


def test_migrate_idempotent(api, backend_mod):
    nid = _make_note_with_image(api)
    backend_mod.migrate_images()
    files_before = os.listdir(os.path.join(backend_mod.ATTACH_DIR, nid))
    rows_before = len(api.attachments_list(nid))
    assert backend_mod.migrate_images() == 0  # 二次运行零变更
    assert os.listdir(os.path.join(backend_mod.ATTACH_DIR, nid)) == files_before
    assert len(api.attachments_list(nid)) == rows_before


def test_migrate_skips_dict_ops(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'content': json.dumps({'ops': [
        {'insert': {'image': {'id': 'x', 'filename': 'y.png', 'storedPath': '/tmp/y.png'}}}
    ]})})
    assert backend_mod.migrate_images() == 0
    assert api.attachments_list(nid) == []


def test_migrate_skips_encrypted(api, backend_mod):
    nid = _make_note_with_image(api)
    api.note_set_password(nid, 'secret123')
    assert backend_mod.migrate_images() == 0
    assert api.attachments_list(nid) == []


def test_migrate_empty_db_noop(api, backend_mod, tmp_path):
    assert backend_mod.migrate_images() == 0
    # 无 premig 快照产生
    backups = os.listdir(os.path.join(backend_mod.DATA_DIR, 'backups')) if \
        os.path.isdir(os.path.join(backend_mod.DATA_DIR, 'backups')) else []
    assert not any(b.startswith('premig-') for b in backups)


def test_externalize_deterministic(api, backend_mod):
    """同一字节两次外置 → 同一文件名、单附件行（幂等核心）"""
    nid = api.notes_create()['id']
    r1 = backend_mod._externalize_image(nid, PNG_URI)
    r2 = backend_mod._externalize_image(nid, PNG_URI)
    assert r1 is not None and r2 is not None
    assert r1[1] == r2[1]
    assert len(api.attachments_list(nid)) == 1


def test_migrate_bad_uri_keeps_original(api, backend_mod):
    """无法解析的 data URI：保留原串不崩溃，可重试"""
    nid = api.notes_create()['id']
    bad = json.dumps({'ops': [{'insert': {'image': 'data:image/png;base64,@@@@not-base64'}}]})
    api.notes_update(nid, {'content': bad})
    assert backend_mod.migrate_images() == 0
    assert 'data:image' in api.notes_get(nid)['content']
