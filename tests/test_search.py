# -*- coding: utf-8 -*-
"""FTS5 全文搜索：回填/同步/中英文/加密取舍/特殊字符/LIKE 回退"""
import pytest


def delta(text):
    import json
    return json.dumps({"ops": [{"insert": text + "\n"}]}, ensure_ascii=False)


@pytest.fixture
def seeded(api):
    """三篇笔记：普通中文、普通英文、加密"""
    a = api.notes_create()['id']
    api.notes_update(a, {'title': '工作计划', 'content': delta('明天开会讨论项目进度安排')})
    b = api.notes_create()['id']
    api.notes_update(b, {'title': 'Meeting Notes', 'content': delta('discuss the quarterly roadmap')})
    c = api.notes_create()['id']
    api.notes_update(c, {'title': '私密日记', 'content': delta('这是绝密内容不该被搜到')})
    api.note_set_password(c, 'secret123')
    return {'cn': a, 'en': b, 'enc': c}


def ids(result):
    return set(result['ids'])


class TestSearch:
    def test_title_hit(self, api, seeded):
        r = api.notes_search('工作计划')
        assert seeded['cn'] in ids(r) and seeded['cn'] in set(r['title_hits'])

    def test_body_hit_chinese(self, api, seeded):
        r = api.notes_search('项目进度')
        assert seeded['cn'] in ids(r)
        assert seeded['cn'] not in set(r['title_hits'])  # 是正文命中不是标题

    def test_body_hit_english(self, api, seeded):
        assert seeded['en'] in ids(api.notes_search('roadmap'))

    def test_two_char_chinese_falls_back_to_like(self, api, seeded):
        # 2 字符走 LIKE 回退，也必须能命中正文
        assert seeded['cn'] in ids(api.notes_search('开会'))

    def test_no_delta_json_false_positive(self, api, seeded):
        # 旧实现搜 Delta JSON 原文会误命中 "insert"/"ops"
        assert ids(api.notes_search('insert')) == set()
        assert ids(api.notes_search('ops')) == set()

    def test_empty_query(self, api, seeded):
        r = api.notes_search('')
        assert r['ids'] == [] and r['title_hits'] == [] and r['snippets'] == {}
        r = api.notes_search('   ')
        assert r['ids'] == [] and r['title_hits'] == [] and r['snippets'] == {}

    def test_special_chars_safe(self, api, seeded):
        for q in ['100%', 'a_b', '"quoted"', 'back\\slash', "'; DROP TABLE notes;--"]:
            api.notes_search(q)  # 不抛异常即可


class TestEncryptedNotes:
    def test_encrypted_body_never_hit(self, api, seeded):
        assert ids(api.notes_search('绝密内容')) == set()      # FTS 路径
        assert ids(api.notes_search('绝密')) == set()          # LIKE 路径

    def test_encrypted_title_still_searchable(self, api, seeded):
        assert seeded['enc'] in ids(api.notes_search('私密日记'))
        assert seeded['enc'] in ids(api.notes_search('私密'))  # LIKE 路径

    def test_fts_index_has_no_plaintext(self, api, backend_mod, seeded):
        """索引表中绝不能有加密笔记明文"""
        if not backend_mod.FTS_AVAILABLE:
            pytest.skip('FTS5 不可用')
        row = backend_mod.conn.execute(
            "SELECT body FROM notes_fts WHERE note_id = ?", (seeded['enc'],)).fetchone()
        assert row is not None and row['body'] == ''

    def test_remove_password_reindexes_body(self, api, seeded):
        api.note_remove_password(seeded['enc'], 'secret123')
        assert seeded['enc'] in ids(api.notes_search('绝密内容'))

    def test_set_password_clears_body(self, api, seeded):
        api.note_set_password(seeded['cn'], 'newpass123')
        assert ids(api.notes_search('项目进度')) == set()
        assert seeded['cn'] in ids(api.notes_search('工作计划'))  # 标题仍可搜


class TestSync:
    def test_update_resyncs(self, api, seeded):
        api.notes_update(seeded['cn'], {'content': delta('改成了全新的正文内容')})
        assert seeded['cn'] in ids(api.notes_search('全新的正文'))
        assert ids(api.notes_search('项目进度')) == set()

    def test_delete_removes_from_index(self, api, seeded):
        api.notes_delete(seeded['cn'])
        assert ids(api.notes_search('工作计划')) == set()

    def test_restore_resyncs(self, api, seeded):
        vid = api.versions_create(seeded['cn'], '工作计划', delta('旧版本独有词汇甲乙丙'))
        api.notes_update(seeded['cn'], {'content': delta('新内容')})
        api.versions_restore(vid)
        assert seeded['cn'] in ids(api.notes_search('独有词汇'))

    def test_backfill_on_rebuild(self, backend_mod, api, seeded):
        """fts_version 变更触发全量回填（模拟：清索引后重建）"""
        if not backend_mod.FTS_AVAILABLE:
            pytest.skip('FTS5 不可用')
        backend_mod.conn.execute("DELETE FROM notes_fts")
        backend_mod.conn.execute("DELETE FROM settings WHERE key='fts_version'")
        backend_mod.conn.commit()
        # 复用回填逻辑：逐行 sync
        for r in backend_mod.conn.execute("SELECT id FROM notes").fetchall():
            backend_mod._fts_sync_from_row(r['id'])
        backend_mod.conn.commit()
        assert seeded['cn'] in ids(api.notes_search('项目进度'))
        assert ids(api.notes_search('绝密内容')) == set()


def test_delta_to_text(backend_mod):
    f = backend_mod._delta_to_text
    assert f('{"ops":[{"insert":"你好"},{"insert":{"image":"x"}},{"insert":"世界\\n"}]}') == '你好世界\n'
    assert f('') == ''
    assert f('不是JSON的内容') == '不是JSON的内容'  # 解析失败回退原串


class TestSnippets:
    """命中片段：列表要能显示「为什么这条命中了」"""

    def test_snippet_contains_query_and_context(self, api, seeded):
        r = api.notes_search('项目进度')
        snip = r['snippets'][seeded['cn']]
        assert '项目进度' in snip
        assert '明天开会' in snip, '片段应带上下文，便于判断是否是想要的笔记'

    def test_snippet_adds_ellipsis_when_truncated(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '长文', 'content': delta('开头' + '填充' * 60 + '目标词' + '后续' * 60)})
        snip = api.notes_search('目标词')['snippets'][nid]
        assert '目标词' in snip
        assert snip.startswith('…') and snip.endswith('…'), '两侧被截断时应加省略号'

    def test_title_only_hit_falls_back_to_body_head(self, api, seeded):
        # 「工作计划」只出现在标题里 → 片段退化为正文开头，而不是空
        snip = api.notes_search('工作计划')['snippets'][seeded['cn']]
        assert snip.startswith('明天开会')

    def test_encrypted_snippet_is_empty(self, api, seeded):
        """加密笔记即使标题命中，也不返回任何正文片段"""
        r = api.notes_search('私密日记')
        assert seeded['enc'] in r['ids']
        assert r['snippets'][seeded['enc']] == ''

    def test_snippets_only_for_matched_ids(self, api, seeded):
        r = api.notes_search('roadmap')
        assert set(r['snippets']) <= set(r['ids'])


class TestPreview:
    """列表摘要（notes_list 的 preview 字段）：正文纯文本、加密/坏数据不外泄"""

    def test_preview_is_plaintext_not_delta(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': delta('第一行正文\n第二行正文')})
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert n['preview'] == '第一行正文 第二行正文'
        assert 'ops' not in n['preview']

    def test_preview_truncated(self, api, backend_mod):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': delta('字' * 500)})
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert len(n['preview']) == backend_mod.PREVIEW_MAX

    def test_preview_empty_for_encrypted(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': delta('绝密正文')})
        api.note_set_password(nid, 'secret123')
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert n['preview'] == '', '加密笔记不能把明文/密文摘要送到前端'
        assert n['has_password'] == 1

    def test_preview_skips_embeds_and_bad_json(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '{"ops":[{"insert":{"image":"x"}},{"insert":"文字"}]}'})
        assert [x for x in api.notes_list() if x['id'] == nid][0]['preview'] == '文字'
        api.notes_update(nid, {'title': 'T', 'content': '不是JSON'})
        assert [x for x in api.notes_list() if x['id'] == nid][0]['preview'] == '', \
            '坏数据不能把原始 JSON 当摘要显示'

    def test_notes_list_does_not_ship_content(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': delta('正文')})
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert 'content' not in n, 'notes_list 不应把整篇正文发给前端（大 payload + 密文外泄）'
