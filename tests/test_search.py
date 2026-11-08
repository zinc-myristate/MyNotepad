# -*- coding: utf-8 -*-
"""FTS5 全文搜索：回填/同步/中英文/加密取舍/特殊字符/LIKE 回退"""
import pytest


def delta(text):
    import json
    return json.dumps({"ops": [{"insert": text + "\n"}]}, ensure_ascii=False)


@pytest.fixture
def seeded(delta_note, api):
    """三篇 delta 笔记：普通中文、普通英文、加密"""
    a = delta_note('工作计划', delta('明天开会讨论项目进度安排'))
    b = delta_note('Meeting Notes', delta('discuss the quarterly roadmap'))
    c = delta_note('私密日记', delta('这是绝密内容不该被搜到'))
    api.note_set_password(c, 'secret123')
    return {'cn': a, 'en': b, 'enc': c}


def ids(result):
    return set(result['ids'])


@pytest.fixture
def delta_note(api, backend_mod):
    """建一篇**显式 delta 格式**的笔记。

    第 6 轮起 notes_create 默认 'md'（Markdown 文本），而本文件大量用例构造的是
    Quill Delta JSON —— 那些是「Delta 时代」的行为契约，必须显式声明格式；否则
    md 解析器会把 JSON 当成 Markdown 正文，测出来的结论没有意义。
    """
    def make(title='', content=''):
        nid = api.notes_create()['id']
        backend_mod.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
        backend_mod.conn.commit()
        api.notes_update(nid, {'title': title, 'content': content})
        return nid

    return make


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

    def test_snippet_adds_ellipsis_when_truncated(self, api, delta_note):
        nid = delta_note('长文', delta('开头' + '填充' * 60 + '目标词' + '后续' * 60))
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


def reference_snippet(plain, q, width=40):
    """命中片段的原始定义（2026-11-08 之前逐篇现算的实现，原样抄过来当对照）"""
    text = ' '.join(plain.split())
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:width * 2]
    start = max(0, i - width)
    end = min(len(text), i + len(q) + width)
    return ('…' if start > 0 else '') + text[start:end] + ('…' if end < len(text) else '')


def reference_snippets(backend_mod, ids, q, width=40):
    out = {}
    for nid in ids:
        r = backend_mod.conn.execute(
            "SELECT content, format, password_hash FROM notes WHERE id = ?", (nid,)).fetchone()
        if r is None:
            continue
        out[nid] = '' if r['password_hash'] else reference_snippet(
            backend_mod.note_plain_text(r['content'], r['format'] or 'delta'), q, width)
    return out


class TestSnippetFastPath:
    """命中片段的快路径（_fts_snippets）必须与逐篇按全文现算逐字相同。

    为什么单独钉：常见词能命中上千篇，逐篇对全文跑七趟正则时 800 篇 × 10KB 要 1.24 秒。
    快路径让 SQLite 定位、只取命中附近一小段回来折叠空白；窗口截在空白里、碰到首尾、
    查询带空白或非 ASCII 大小写字母，都是两边可能分叉的地方。
    """

    @pytest.fixture
    def corpus(self, api, backend_mod):
        import random
        rng = random.Random(20261108)
        han = '的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面'
        words = ['Mixed', 'content', 'API', 'Python', 'roadmap', 'École', 'ÉCOLE', 'note', 'XYZ']
        marks = ['# 标题\n', '**加粗**', '[链接](https://example.com/a)', '![图](attachments/a/b.png)',
                 '`code`', '\n```py\nprint(1)\n```\n', '- [ ] 待办 @2026-09-25\n', '<u>下划线</u>']
        gaps = [' ', '  ', '\n', '\n\n', '\t', '　', ' \n ', ' ' * 30]

        def text(n):
            out = []
            for _ in range(n):
                k = rng.random()
                if k < 0.45:
                    out.append(''.join(rng.choice(han) for _ in range(rng.randint(1, 10))))
                elif k < 0.65:
                    out.append(rng.choice(words))
                elif k < 0.8:
                    out.append(rng.choice(marks))
                else:
                    out.append(rng.choice(gaps))
            return ''.join(out)

        ids = []
        for i in range(90):
            nid = api.notes_create()['id']
            if i % 4 == 0:
                backend_mod.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
                backend_mod.conn.commit()
                content = delta(text(rng.randint(0, 80)))
            else:
                content = ('---\n状态: 进行中\n---\n' if i % 7 == 0 else '') + ('   \n\t' if i % 5 == 0 else '') \
                    + text(rng.randint(0, 200)) + ('  \n ' if i % 3 == 0 else '')
            api.notes_update(nid, {'title': '笔记%d' % i, 'content': content})
            ids.append(nid)
        for nid in ids[::11]:
            api.note_set_password(nid, 'secret123')
        queries = {'mixed', 'MIXED', 'Mixed content', 'école', 'xyz', '不存在的词', 'a', '的', '下划线', '加粗'}
        for nid in rng.sample(ids, 40):
            plain = backend_mod.note_plain_text(*backend_mod.conn.execute(
                "SELECT content, format FROM notes WHERE id = ?", (nid,)).fetchone())
            if len(plain) > 6:
                a = rng.randint(0, len(plain) - 4)
                q = plain[a:a + rng.randint(1, 5)].strip()
                if q:
                    queries.add(q)
        return ids, sorted(queries)

    @staticmethod
    def snippets(backend_mod, monkeypatch, strategy, ids, q, width=40):
        """三条取片段的路：扫一遍 FTS / 逐篇现算 / 给 MATCH 只读命中行（_search_plain 的 FTS 路径）"""
        if strategy == 'match':
            return backend_mod.Api._snippets_for(ids, q, width, match='"' + q.replace('"', '""') + '"')
        monkeypatch.setattr(backend_mod, 'SCAN_COST_RATIO', 10 ** 9 if strategy == 'scan' else 0)
        return backend_mod.Api._snippets_for(ids, q, width)

    @pytest.mark.parametrize('strategy', ['scan', 'direct', 'match'])
    def test_matches_the_reference_on_a_random_corpus(self, backend_mod, monkeypatch, corpus, strategy):
        ids, queries = corpus
        for q in queries:
            if strategy == 'match' and len(q) < 3:
                continue                          # trigram 只对 ≥3 字的查询用 MATCH（同 _search_plain）
            assert self.snippets(backend_mod, monkeypatch, strategy, ids, q) == \
                reference_snippets(backend_mod, ids, q), q

    @pytest.mark.parametrize('strategy', ['scan', 'direct', 'match'])
    @pytest.mark.parametrize('width', [1, 2, 3, 40])
    def test_window_edges(self, api, backend_mod, monkeypatch, width, strategy):
        """命中贴着开头/结尾、首尾大段空白、窗口里几乎全是空白（逼出按全文重算）"""
        bodies = ['目标词' + '尾' * 3, '头' * 3 + '目标词', '  \n\t目标词  \n ', '\n' * 3 + '甲乙' + '目标词',
                  '前' + ' ' * 500 + '目标词' + ' ' * 500 + '后', '目标词' + ' ' * (width * 4) + '后',
                  ' ' * (width * 4 - 1) + '前' + '目标词', '只有标题命中' + ' ' * 300, '   ', '',
                  '前' * (width + 1) + '目标词' + '后' * (width + 1), '前' * width + '目标词' + '后' * width]
        ids = []
        for body in bodies:
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': '目标词', 'content': body})
            ids.append(nid)
        for q in ('目标词', '前', '后', '不存在的词'):
            if strategy == 'match' and len(q) < 3:
                continue
            assert self.snippets(backend_mod, monkeypatch, strategy, ids, q, width) == \
                reference_snippets(backend_mod, ids, q, width), (q, width)

    def test_few_hits_in_a_big_library_skip_the_scan(self, api, backend_mod, monkeypatch):
        """命中少时逐篇算：按 note_id 查 FTS 是整表扫描，1 篇命中也要读完全库（修前实测 1.2 → 59 ms）"""
        ids = []
        for i in range(40):
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': 'T%d' % i, 'content': '第 %d 篇的正文' % i})
            ids.append(nid)

        def boom(*_a, **_k):
            raise AssertionError('少量命中不该扫整张 FTS 表')

        monkeypatch.setattr(backend_mod, '_fts_snippets', boom)
        assert backend_mod.Api._snippets_for(ids[:2], '正文') == reference_snippets(backend_mod, ids[:2], '正文')
        # 代价模型：40 篇的库，2 篇逐篇算，39 篇扫一遍
        assert not backend_mod._scan_is_cheaper(2) and backend_mod._scan_is_cheaper(39)

    def test_scope_search_computes_snippets_once(self, api, backend_mod, monkeypatch):
        """范围搜索内部先做一次关键词搜索，那次的片段算出来就被丢掉 —— 不该算"""
        tag = api.tags_create('数学')['id']
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '夹逼定理的证明'})
        api.note_tags_set(nid, [tag])
        calls = []
        real = backend_mod.Api._snippets_for

        def spy(*args, **kwargs):
            calls.append(args[0])
            return real(*args, **kwargs)

        monkeypatch.setattr(backend_mod.Api, '_snippets_for', staticmethod(spy))
        assert api.notes_search('tag:数学 夹逼定理')['ids'] == [nid]
        assert len(calls) == 1

    def test_common_word_does_not_recompute_plain_text(self, api, backend_mod, monkeypatch):
        """快路径不许再逐篇跑 note_plain_text（那正是 1.24 秒的来源）"""
        if not backend_mod.FTS_AVAILABLE:
            pytest.skip('FTS5 不可用')
        ids = []
        for i in range(40):        # 2 篇命中只占全库一小部分：不给 MATCH 的话会退回逐篇现算
            nid = api.notes_create()['id']
            body = '# 标题\n\n共同的词 **出现** 在这里' if i < 2 else '别的内容 %d' % i
            api.notes_update(nid, {'title': 'T%d' % i, 'content': body})
            ids.append(nid)
        ids = ids[:2]

        def boom(*_a, **_k):
            raise AssertionError('不该逐篇现算纯文本，也不该退回按全文算')

        by_text = backend_mod._snippet_from_text
        monkeypatch.setattr(backend_mod, 'note_plain_text', boom)
        monkeypatch.setattr(backend_mod, '_snippet_from_text', boom)
        snippets = api.notes_search('共同的词')['snippets']
        assert set(snippets) == set(ids) and all('共同的词' in s for s in snippets.values())
        # 带空格的查询按全文算；值得扫描时，全文要取 FTS 里存好的，不能现算
        monkeypatch.setattr(backend_mod, '_snippet_from_text', by_text)
        monkeypatch.setattr(backend_mod, 'SCAN_COST_RATIO', 10 ** 9)
        assert backend_mod.Api._snippets_for(ids, '出现 在')[ids[0]].startswith('标题')

    def test_trashed_notes_fall_back_to_content(self, api, backend_mod):
        """回收站里的笔记不在 FTS 里（in:trash 范围会给出它们）：要从正文现算"""
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '删掉的', 'content': '一些 **正文** 和关键词'})
        api.notes_delete(nid)
        assert backend_mod.Api._snippets_for([nid], '关键词') == \
            reference_snippets(backend_mod, [nid], '关键词')

    def test_unknown_ids_are_left_out(self, backend_mod):
        assert backend_mod.Api._snippets_for(['不存在的笔记'], '词') == {}


def test_rebuild_indexes_plain_text_for_markdown(api, backend_mod):
    """全量重建要按格式提取纯文本。'1' 版的重建一律按 Delta 解析，Markdown 笔记的 body 是带标记的原文，
    而摘要的快路径直接读 body。"""
    if not backend_mod.FTS_AVAILABLE:
        pytest.skip('FTS5 不可用')
    md_id = api.notes_create()['id']
    md_text = '# 标题\n\n**粗体** 和 [链接](https://example.com)'
    api.notes_update(md_id, {'title': 'MD', 'content': md_text})
    enc = api.notes_create()['id']
    api.notes_update(enc, {'title': '密', 'content': '绝密正文'})
    api.note_set_password(enc, 'secret123')
    gone = api.notes_create()['id']
    api.notes_update(gone, {'title': '删', 'content': '回收站里的'})
    api.notes_delete(gone)
    backend_mod._fts_rebuild()
    backend_mod.conn.commit()
    body = dict(backend_mod.conn.execute("SELECT note_id, body FROM notes_fts").fetchall())
    assert body[md_id] == backend_mod.note_plain_text(md_text, 'md')
    assert '**' not in body[md_id] and 'https://' not in body[md_id]
    assert body[enc] == '', '加密笔记的正文不能进索引'
    assert gone not in body, '回收站里的笔记不进索引'


class TestSearchConnection:
    """notes_search 是只读方法（不进写锁），它和它的辅助函数都要按上下文取连接（_ctx_conn）。

    修复前 _search_plain / _snippets_for 直接用全局写连接：另一个线程写到一半、还没提交时，
    搜索就能读到那些数据（实测复现），读写也挤在同一个连接对象上。
    """

    def _hold_uncommitted_note(self, backend_mod, title, inside=None):
        import threading
        import uuid
        nid = str(uuid.uuid4())
        ready, done, seen = threading.Event(), threading.Event(), {}

        def writer():
            with backend_mod._db_lock:
                backend_mod._mark_write_owner()
                try:
                    backend_mod.conn.execute(
                        "INSERT INTO notes (id, title, content, format) VALUES (?, ?, '', 'md')", (nid, title))
                    backend_mod.conn.execute(
                        "INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, '')", (nid, title))
                    if inside:
                        seen['inside'] = inside()
                    ready.set()
                    done.wait(10)
                    backend_mod.conn.rollback()
                finally:
                    backend_mod._clear_write_owner()

        t = threading.Thread(target=writer)
        t.start()
        ready.wait(10)
        return nid, done, t, seen

    def test_search_does_not_see_another_threads_uncommitted_write(self, api, backend_mod):
        if not backend_mod.FTS_AVAILABLE:
            pytest.skip('FTS5 不可用')
        api.notes_create()
        nid, done, t, _ = self._hold_uncommitted_note(backend_mod, '未提交标题甲乙丙')
        try:
            assert nid not in api.notes_search('未提交标题甲乙丙')['ids']
            assert nid not in api.notes_search('甲乙')['ids']               # LIKE 路径
        finally:
            done.set()
            t.join(10)

    def test_search_inside_the_write_path_sees_its_own_transaction(self, api, backend_mod):
        """写路径里（todos_list 等会在持锁时调 notes_search）必须用写连接，才看得到自己未提交的数据"""
        if not backend_mod.FTS_AVAILABLE:
            pytest.skip('FTS5 不可用')
        nid, done, t, seen = self._hold_uncommitted_note(
            backend_mod, '写路径里的标题丁戊己', inside=lambda: api.notes_search('写路径里的标题丁戊己')['ids'])
        done.set()
        t.join(10)
        assert nid in seen['inside']


class TestPreview:
    """列表摘要（notes_list 的 preview 字段）：正文纯文本、加密/坏数据不外泄"""

    def test_preview_is_plaintext_not_delta(self, api, delta_note):
        nid = delta_note('T', delta('第一行正文\n第二行正文'))
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert n['preview'] == '第一行正文 第二行正文'
        assert 'ops' not in n['preview']

    def test_preview_truncated(self, api, backend_mod, delta_note):
        nid = delta_note('T', delta('字' * 500))
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert len(n['preview']) == backend_mod.PREVIEW_MAX

    def test_preview_empty_for_encrypted(self, api, delta_note):
        nid = delta_note('T', delta('绝密正文'))
        api.note_set_password(nid, 'secret123')
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert n['preview'] == '', '加密笔记不能把明文/密文摘要送到前端'
        assert n['has_password'] == 1

    def test_preview_skips_embeds_and_bad_json(self, api, delta_note):
        nid = delta_note('T', '{"ops":[{"insert":{"image":"x"}},{"insert":"文字"}]}')
        assert [x for x in api.notes_list() if x['id'] == nid][0]['preview'] == '文字'
        api.notes_update(nid, {'title': 'T', 'content': '不是JSON'})
        assert [x for x in api.notes_list() if x['id'] == nid][0]['preview'] == '', \
            '坏数据不能把原始 JSON 当摘要显示'

    def test_notes_list_does_not_ship_content(self, api, delta_note):
        nid = delta_note('T', delta('正文'))
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert 'content' not in n, 'notes_list 不应把整篇正文发给前端（大 payload + 密文外泄）'

    def test_notes_list_includes_notebook_id(self, api):
        """回归：notes_list 曾漏掉 notebook_id，导致前端按笔记本筛选永远空列表、
        下拉里的「N 篇」计数恒为 0（笔记本功能实际不可用）。"""
        nb = api.notebooks_create('课程')['id']
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'notebook_id': nb})
        n = [x for x in api.notes_list() if x['id'] == nid][0]
        assert n.get('notebook_id') == nb, '列表必须带上 notebook_id'
        assert len([x for x in api.notes_list() if x['notebook_id'] == nb]) == 1


class TestSearchScope:
    """范围语法：tag: / notebook: / in:trash，可与关键词组合"""

    @pytest.fixture
    def scoped(self, api):
        nb_a = api.notebooks_create('课程A')['id']
        nb_b = api.notebooks_create('课程B')['id']
        t_math = api.tags_create('数学')['id']
        t_food = api.tags_create('菜谱')['id']
        out = {}
        for key, title, body, nb in (('a', '极限与连续', '夹逼定理的证明', nb_a),
                                     ('b', '矩阵分解', '特征值与特征向量', nb_a),
                                     ('c', '红烧肉做法', '先焯水再炒糖色', nb_b)):
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': title, 'content': delta(body), 'notebook_id': nb})
            out[key] = nid
        api.note_tags_set(out['a'], [t_math])
        api.note_tags_set(out['b'], [t_math])
        api.note_tags_set(out['c'], [t_food])
        out.update({'nb_a': nb_a, 'nb_b': nb_b, 't_math': t_math, 't_food': t_food})
        return out

    def test_tag_scope_alone(self, api, scoped):
        r = api.notes_search('tag:数学')
        assert set(r['ids']) == {scoped['a'], scoped['b']}

    def test_tag_scope_with_keyword(self, api, scoped):
        r = api.notes_search('tag:数学 夹逼')
        assert set(r['ids']) == {scoped['a']}, '范围与关键词要同时满足'

    def test_notebook_scope_by_name_and_alias(self, api, scoped):
        assert set(api.notes_search('notebook:课程A')['ids']) == {scoped['a'], scoped['b']}
        assert set(api.notes_search('nb:课程B')['ids']) == {scoped['c']}

    def test_notebook_scope_by_id(self, api, scoped):
        assert set(api.notes_search('notebook:' + scoped['nb_a'])['ids']) == \
            {scoped['a'], scoped['b']}

    def test_unknown_scope_value_returns_empty(self, api, scoped):
        assert api.notes_search('tag:不存在')['ids'] == []
        assert api.notes_search('notebook:不存在')['ids'] == []

    def test_trash_scope_only_trashed(self, api, scoped):
        api.notes_delete(scoped['a'])
        r = api.notes_search('in:trash')
        assert set(r['ids']) == {scoped['a']}, '只应列出回收站里的那篇'
        assert scoped['a'] not in set(api.notes_search('')['ids'])

    def test_trash_scope_with_keyword_uses_title(self, api, scoped):
        api.notes_delete(scoped['a'])
        assert set(api.notes_search('in:trash 极限')['ids']) == {scoped['a']}
        assert api.notes_search('in:trash 矩阵')['ids'] == []

    def test_snippet_still_provided_for_scoped_hits(self, api, scoped):
        r = api.notes_search('tag:数学 夹逼')
        assert '夹逼' in r['snippets'][scoped['a']]

    def test_unknown_prefix_is_treated_as_text(self, api, scoped):
        """不认识的 a:b 不能被吞掉——用户可能真的想搜这个词"""
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': delta('版本 v1:2 的说明')})
        assert nid in set(api.notes_search('v1:2')['ids'])
