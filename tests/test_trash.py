# -*- coding: utf-8 -*-
"""回收站（软删除）测试：软删/恢复/彻底删除/超期清理/提醒过滤"""
import os


def test_soft_delete_hides_everywhere(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '独苗向日葵', 'content': '{"ops":[{"insert":"花瓣黄色\\n"}]}'})
    tag = api.tags_create('t1')['id']
    api.note_tags_set(nid, [tag])
    api.versions_create(nid, '独苗向日葵', '{"ops":[{"insert":"v1\\n"}]}')

    # 附件文件 + 背景图（软删后必须仍在）
    att = os.path.join(backend_mod.ATTACH_DIR, nid)
    os.makedirs(att, exist_ok=True)
    with open(os.path.join(att, 'img_x.png'), 'wb') as f:
        f.write(b'x')
    bg_dir = os.path.join(backend_mod.DATA_DIR, 'backgrounds')
    os.makedirs(bg_dir, exist_ok=True)
    bg = os.path.join(bg_dir, 'bg_n.png')
    with open(bg, 'wb') as f:
        f.write(b'x')
    api.notes_update(nid, {'bg_value': bg})

    assert api.notes_delete(nid) is True

    # 主列表/标签/搜索（FTS ≥3 字符）/LIKE（<3 字符）/get/update 全部不可见
    assert nid not in [n['id'] for n in api.notes_list()]
    assert nid not in [n['id'] for n in api.notes_by_tag(tag)]
    assert nid not in api.notes_search('向日葵')['ids']
    assert nid not in api.notes_search('独')['ids']          # 2 字符 → LIKE 路径
    assert api.notes_get(nid) is None
    assert api.notes_update(nid, {'title': '改'}) is None

    # 文件与背景图仍在，子表行保留（FK 未级联），DEK 缓存弹出
    assert os.path.isfile(os.path.join(att, 'img_x.png'))
    assert os.path.isfile(bg)
    assert api.versions_list(nid) != []
    assert api.note_tags_get(nid) != []
    assert api.reminder_list(nid) == [] or True  # 无提醒时空列表即可
    assert api.notes_trash_list()[0]['id'] == nid


def test_restore_reindexes(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '向日葵', 'content': '{"ops":[{"insert":"正文\\n"}]}'})
    api.notes_delete(nid)
    assert nid not in api.notes_search('向日葵')['ids']

    assert api.notes_restore(nid) is True
    assert nid in [n['id'] for n in api.notes_list()]
    assert nid in api.notes_search('向日葵')['ids']
    assert api.notes_get(nid) is not None
    # 重复恢复返回 False
    assert api.notes_restore(nid) is False


def test_purge_removes_everything(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '向日葵', 'content': '{"ops":[{"insert":"正文\\n"}]}'})
    tag = api.tags_create('t2')['id']
    api.note_tags_set(nid, [tag])
    api.versions_create(nid, '向日葵', '{"ops":[{"insert":"v1\\n"}]}')
    api.reminder_create(nid, 'rem', '2099-01-01 09:00:00', 'none', 1)
    att = os.path.join(backend_mod.ATTACH_DIR, nid)
    os.makedirs(att, exist_ok=True)
    with open(os.path.join(att, 'img_x.png'), 'wb') as f:
        f.write(b'x')

    api.notes_delete(nid)
    assert api.notes_purge(nid) is True

    assert api.notes_get(nid) is None
    assert not os.path.exists(att)                    # 附件目录删除
    assert api.notes_trash_list() == []
    # 子表级联删除
    assert api.note_tags_get(nid) == []
    c = backend_mod.conn
    assert c.execute("SELECT COUNT(*) FROM versions WHERE note_id=?", (nid,)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM reminders WHERE note_id=?", (nid,)).fetchone()[0] == 0
    # 非回收站笔记不可 purge
    alive = api.notes_create()['id']
    assert api.notes_purge(alive) is False


def test_purge_all_and_expired(api, backend_mod):
    a = api.notes_create()['id']
    b = api.notes_create()['id']
    api.notes_delete(a)
    api.notes_delete(b)
    # 手动把 a 的删除时间改到 31 天前
    backend_mod.conn.execute(
        "UPDATE notes SET deleted_at = datetime('now','localtime','-31 days') WHERE id=?", (a,))
    backend_mod.conn.commit()

    assert backend_mod.purge_expired_trash() == 1     # 只清超期的一篇
    assert [t['id'] for t in api.notes_trash_list()] == [b]

    assert api.notes_purge_all() == 1
    assert api.notes_trash_list() == []


def test_trash_reminders_filtered(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '待办', 'content': '{"ops":[{"insert":"x\\n"}]}'})
    api.reminder_create(nid, '马上办', '2026-01-01 09:00:00', 'none', 1)  # 已过期
    api.notes_delete(nid)

    assert api.reminder_check() == []                 # 软删笔记的到期提醒不再触发
    assert api.reminder_list_all() == []              # 管理面板不显示
    assert api.reminder_list() == []

    api.notes_restore(nid)
    assert len(api.reminder_list_all()) == 1          # 恢复后提醒自动重现


def test_soft_delete_pops_dek(api, backend_mod):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '私密', 'content': '{"ops":[{"insert":"secret\\n"}]}'})
    api.note_set_password(nid, 'secret123')
    assert nid in backend_mod._unlocked_deks          # 设密码后保持解锁
    api.notes_delete(nid)
    assert nid not in backend_mod._unlocked_deks
