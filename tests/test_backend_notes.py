# -*- coding: utf-8 -*-
"""笔记基础 CRUD 与列表行为"""


def test_create_and_get(api):
    note = api.notes_create()
    assert note['id'] and note['title'] == '未命名笔记'
    assert api.notes_get(note['id'])['id'] == note['id']


def test_update_title_and_content(api):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '标题A', 'content': '{"ops":[{"insert":"正文\\n"}]}'})
    got = api.notes_get(nid)
    assert got['title'] == '标题A'
    assert got['content'] == '{"ops":[{"insert":"正文\\n"}]}'


def test_update_field_whitelist(api):
    nid = api.notes_create()['id']
    # 非白名单字段被忽略；全非法字段返回 None
    assert api.notes_update(nid, {'password_hash': 'evil', 'nonsense': 1}) is None
    row = api.notes_get(nid)
    assert row['title'] == '未命名笔记'


def test_list_excludes_content_and_hash(api):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'content': '{"ops":[{"insert":"x\\n"}]}'})
    rows = api.notes_list()
    assert len(rows) == 1
    assert 'content' not in rows[0]
    assert 'password_hash' not in rows[0]
    assert rows[0]['has_password'] == 0


def test_get_never_returns_secret_columns(api):
    nid = api.notes_create()['id']
    api.note_set_password(nid, 'secret123')
    got = api.notes_get(nid)
    assert 'password_hash' not in got
    assert 'enc_dek' not in got


def test_pin_and_delete(api):
    a = api.notes_create()['id']
    b = api.notes_create()['id']
    api.notes_update(b, {'is_pinned': 1})
    rows = api.notes_list()
    assert rows[0]['id'] == b  # 置顶排最前
    api.notes_delete(a)
    assert len(api.notes_list()) == 1
    assert api.notes_get(a) is None
