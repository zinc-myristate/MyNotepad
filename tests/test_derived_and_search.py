# -*- coding: utf-8 -*-
"""第 7 轮 A 段：派生索引（指标 / 待办明细 / 属性）与搜索范围语法扩展。

为什么值得单独测：派生索引是**后面三组功能的公共地基**（待办聚合、todo:/has: 搜索、
字数统计、表格视图）。它一旦口径错了，上面所有功能都会"看起来对但数字不对"。
所以这里锁三件事：
  · 同一篇内容在 md / delta 两种格式下的指标必须一致（口径统一）
  · 待办的 idx 顺序必须与正文顺序一致（写回要靠它定位）
  · 搜索每个前缀 token 都要真的生效，且能取反
"""
import json
import os
import re

import pytest
from conftest import PROJECT_ROOT


def md_note(api, text, title='笔记'):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': text})
    return nid


def delta_note(backend, text_lines, title='笔记'):
    """按 Quill 约定构造 delta：行属性挂在含换行的 op 上"""
    nid = backend.api.notes_create()['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    ops = []
    for text, attrs in text_lines:
        ops.append({'insert': text + '\n', 'attributes': attrs} if attrs else {'insert': text + '\n'})
    backend.api.notes_update(nid, {'title': title,
                                   'content': json.dumps({'ops': ops}, ensure_ascii=False)})
    return nid


def derived(backend, nid):
    r = backend.conn.execute('SELECT * FROM note_derived WHERE note_id = ?', (nid,)).fetchone()
    return dict(r) if r else None


def todos(backend, nid):
    return [tuple(r) for r in backend.conn.execute(
        'SELECT idx, text, due, done FROM note_todos WHERE note_id = ? ORDER BY idx',
        (nid,)).fetchall()]


# ---------------- 指标与待办扫描 ----------------

class TestDerivedMetrics:
    def test_created_note_has_zero_metrics(self, api, backend_mod):
        nid = api.notes_create()['id']
        d = derived(backend_mod, nid)
        assert d is not None, '新建笔记也要有派生行（面板不该出现空洞）'
        assert d['word_count'] == 0 and d['todo_total'] == 0 and d['todo_open'] == 0

    def test_counts_words_for_cjk_and_latin(self, api, backend_mod):
        nid = md_note(api, '中文四个字 hello world 123\n')
        d = derived(backend_mod, nid)
        # 中文字按字算（中文四个字 = 5 个字）+ 拉丁词 hello / world / 123（3）
        assert d['word_count'] == 5 + 3, d['word_count']
        assert d['char_count'] > 0

    def test_markdown_metrics(self, api, backend_mod):
        body = ('---\nstatus: 进行中\npriority: 高\n---\n\n# 标题\n\n'
                '- [ ] 未做 📅 2026-09-25\n- [x] 已做\n- [ ] 无期限\n\n'
                '```\ncode\n```\n\n公式 $a^2$ 与链接 [[另一篇]] [[X|别名]]\n')
        nid = md_note(api, body)
        d = derived(backend_mod, nid)
        assert d['todo_total'] == 3 and d['todo_open'] == 2
        assert d['todo_next_due'] == '2026-09-25'
        assert d['has_formula'] == 1 and d['has_code'] == 1
        assert d['link_count'] == 2
        props = json.loads(d['props_json'])
        assert props.get('status') == '进行中' and props.get('priority') == '高'

    def test_delta_metrics_match_markdown(self, api, backend_mod):
        """同一内容两种格式，指标必须一致（口径统一的回归锁）"""
        md_text = '- [ ] 未做 📅 2026-09-25\n- [x] 已做\n\n正文内容\n'
        nid_md = md_note(api, md_text)
        # delta 里待办是**行属性**（list），不是正文里的 "- [ ]" 文本，构造时必须照这个来
        nid_delta = delta_note(backend_mod, [
            ('未做 📅 2026-09-25', {'list': 'unchecked'}),
            ('已做', {'list': 'checked'}),
            ('正文内容', None),
        ])
        d_md, d_delta = derived(backend_mod, nid_md), derived(backend_mod, nid_delta)
        for key in ('todo_total', 'todo_open', 'todo_next_due', 'word_count', 'char_count'):
            assert d_md[key] == d_delta[key], '%s 口径不一致：%r vs %r' % (key, d_md[key], d_delta[key])

    def test_delta_todo_uses_line_attributes(self, api, backend_mod):
        nid = delta_note(backend_mod, [
            ('第一项', {'list': 'unchecked'}),
            ('第二项', {'list': 'checked'}),
            ('普通段落', None),
        ])
        d = derived(backend_mod, nid)
        assert d['todo_total'] == 2 and d['todo_open'] == 1
        assert [t[1] for t in todos(backend_mod, nid)] == ['第一项', '第二项']
        assert [t[3] for t in todos(backend_mod, nid)] == [0, 1]

    def test_todo_idx_follows_document_order(self, api, backend_mod):
        nid = md_note(api, '- [ ] 甲\n段落\n- [x] 乙\n- [ ] 丙\n')
        assert [t[1] for t in todos(backend_mod, nid)] == ['甲', '乙', '丙']
        assert [t[0] for t in todos(backend_mod, nid)] == [0, 1, 2]

    def test_due_is_stripped_from_display_text(self, api, backend_mod):
        nid = md_note(api, '- [ ] 交报告 📅 2026-09-25\n')
        assert todos(backend_mod, nid)[0][1] == '交报告'
        assert todos(backend_mod, nid)[0][2] == '2026-09-25'

    def test_ascii_due_alias_parses_like_emoji(self, api, backend_mod):
        """第 12 轮加的 ASCII 别名 `@2026-09-25`：界面提示里就不必再出现 emoji。

        它和 Obsidian 的 `📅 2026-09-25` 是同一条正则，所以"文字里不带标记、due 取到日期"
        的行为必须逐字一致 —— 勾选写回的文本校验也靠这个口径。
        """
        emoji_id = md_note(api, '- [ ] 交报告 📅 2026-09-25\n')
        alias_id = md_note(api, '- [ ] 交报告 @2026-09-25\n')
        assert todos(backend_mod, alias_id) == todos(backend_mod, emoji_id)
        assert todos(backend_mod, alias_id)[0][1] == '交报告'
        assert todos(backend_mod, alias_id)[0][2] == '2026-09-25'

    def test_ascii_due_alias_keeps_metric_parity(self, api, backend_mod):
        """别名同样要被剥掉再算字数：否则同一篇内容 md/delta 两种格式算出的数字会不一致"""
        alias_id = md_note(api, '- [ ] 未做 @2026-09-25\n- [x] 已做\n- [ ] 无期限\n\n正文内容\n')
        emoji_id = md_note(api, '- [ ] 未做 📅 2026-09-25\n- [x] 已做\n- [ ] 无期限\n\n正文内容\n')
        a, e = derived(backend_mod, alias_id), derived(backend_mod, emoji_id)
        for key in ('word_count', 'char_count', 'todo_total', 'todo_open', 'todo_next_due'):
            assert a[key] == e[key], '%s 在两种写法下不一致：%r vs %r' % (key, a[key], e[key])

    def test_incomplete_or_email_like_at_is_not_a_due(self, api, backend_mod):
        """`@` 别名必须只认**完整 ISO 日期**：邮箱、@某人、只写了年月都不能被误判成截止日"""
        nid = md_note(api, '- [ ] 发给 a@b.com\n- [ ] 提醒 @张三\n- [ ] 只写年月 @2026-09\n')
        got = todos(backend_mod, nid)
        assert [t[2] for t in got] == [None, None, None], got
        assert got[0][1] == '发给 a@b.com', '不是日期就不能从文字里抠掉'
        assert got[2][1] == '只写年月 @2026-09'

    def test_delta_without_date_still_parsed(self, api, backend_mod):
        nid = delta_note(backend_mod, [('- [ ] 待办', None)])
        d = derived(backend_mod, nid)
        assert d['todo_total'] == 0, 'delta 的待办靠 list 属性，正文里的 - [ ] 文本不算'

    def test_flags_for_attachment_and_reminder(self, api, backend_mod, tmp_path):
        nid = md_note(api, '正文\n')
        src = tmp_path / 'a.txt'
        src.write_text('x', encoding='utf-8')
        api.file_copy_to_note(str(src), nid, 'file')
        backend_mod._refresh_derived(nid)
        assert derived(backend_mod, nid)['has_attachment'] == 1
        api.reminder_create(nid, '提醒我', '2030-01-01 09:00:00')
        backend_mod._refresh_derived(nid)
        assert derived(backend_mod, nid)['has_reminder'] == 1

    def test_encrypted_locked_note_has_empty_metrics(self, api, backend_mod):
        nid = md_note(api, '- [ ] 机密待办\n')
        api.note_set_password(nid, 'pw123456')
        api.note_lock(nid)
        backend_mod._refresh_derived(nid)
        d = derived(backend_mod, nid)
        assert d['todo_total'] == 0 and d['word_count'] == 0, '锁定态不得把密文当正文扫'

    def test_deleted_note_loses_derived_rows(self, api, backend_mod):
        nid = md_note(api, '- [ ] 待办\n')
        api.notes_delete(nid)
        api.notes_purge(nid)
        assert derived(backend_mod, nid) is None
        assert todos(backend_mod, nid) == []


class TestFrontMatterParsing:
    def test_no_front_matter(self, backend_mod):
        assert backend_mod.parse_front_matter('# 标题\n正文') == {}

    def test_scalars_list_and_block_list(self, backend_mod):
        text = '---\ntitle: 我的笔记\nrating: 5\ntags: [甲, 乙]\nrefs:\n- 丙\n- 丁\n---\n正文'
        props = backend_mod.parse_front_matter(text)
        assert props['title'] == '我的笔记'
        assert props['rating'] == '5'
        assert props['tags'] == ['甲', '乙']
        assert props['refs'] == ['丙', '丁']

    def test_quoted_values_unquoted(self, backend_mod):
        props = backend_mod.parse_front_matter('---\na: "带引号"\nb: \'单引号\'\n---\n')
        assert props['a'] == '带引号' and props['b'] == '单引号'


# ---------------- 搜索语法扩展 ----------------

class TestSearchSyntax:
    @pytest.fixture
    def seeded(self, api, backend_mod):
        a = md_note(api, '苹果 香蕉\n- [ ] 待办一 📅 2026-01-01\n', title='水果笔记')
        b = md_note(api, '汽车 发动机\n', title='汽车笔记')
        c = md_note(api, '苹果 汽车\n', title='混合')
        api.notes_update(a, {'is_pinned': 1})
        api.notes_update(b, {'is_favorite': 1})
        api.notes_update(c, {'is_favorite': 1})
        backend_mod._refresh_derived(a)
        return {'a': a, 'b': b, 'c': c}

    def ids(self, api, q):
        return set(api.notes_search(q)['ids'])

    def test_title_prefix(self, api, seeded):
        assert self.ids(api, 'title:水果') == {seeded['a']}

    def test_is_flags(self, api, seeded):
        assert self.ids(api, 'is:pinned') == {seeded['a']}
        assert self.ids(api, 'is:favorite') == {seeded['b'], seeded['c']}
        assert self.ids(api, 'is:encrypted') == set()

    def test_has_flags(self, api, seeded):
        assert self.ids(api, 'has:todo') == {seeded['a']}
        assert self.ids(api, 'has:attachment') == set()

    def test_todo_open_and_done(self, api, seeded):
        assert seeded['a'] in self.ids(api, 'todo:open')
        assert seeded['b'] not in self.ids(api, 'todo:open')

    def test_exclude_word(self, api, seeded):
        got = self.ids(api, '苹果 -香蕉')
        assert got == {seeded['c']}, got

    def test_negated_scope_token(self, api, seeded):
        got = self.ids(api, '-is:favorite')
        assert got == {seeded['a']}, got

    def test_date_filter(self, api, seeded, backend_mod):
        backend_mod.conn.execute("UPDATE notes SET created_at = '2020-01-01 00:00:00' WHERE id = ?",
                                 (seeded['a'],))
        backend_mod.conn.commit()
        assert seeded['a'] in self.ids(api, 'created:<2021-01-01')
        assert seeded['a'] not in self.ids(api, 'created:>2021-01-01')

    def test_relative_date(self, api, seeded):
        # 全部笔记都是"刚刚创建"的 → updated:<1d 应命中所有
        assert len(self.ids(api, 'updated:<1d')) == 3

    def test_combined_scope_and_keyword(self, api, seeded):
        assert self.ids(api, 'is:favorite 汽车') == {seeded['b'], seeded['c']}

    def test_unknown_prefix_stays_keyword(self, backend_mod):
        """不认识的 a:b 要留在自由文本里（用户可能真想搜这个）"""
        text, scope = backend_mod._parse_search_scope('a:b 苹果')
        assert 'a:b' in text and not scope.get('has') and not scope.get('is')

    def test_tag_hierarchy_matches_children(self, api):
        parent = api.tags_create('项目')['id']
        child = api.tags_create('项目/子项目')['id']
        n1 = md_note(api, '甲\n')
        n2 = md_note(api, '乙\n')
        api.note_tags_set(n1, [parent])
        api.note_tags_set(n2, [child])
        assert set(api.notes_search('tag:项目')['ids']) == {n1, n2}
        assert set(api.notes_search('tag:项目/子项目')['ids']) == {n2}


# ---------------- 待办面板的数据与写回 ----------------

class TestTodosList:
    def test_scopes_and_counts(self, api, backend_mod):
        today = backend_mod.datetime.now().strftime('%Y-%m-%d')
        nid = md_note(api, '- [ ] 今天的事 📅 %s\n- [ ] 逾期的事 📅 2020-01-01\n'
                          '- [ ] 无期的事\n- [x] 做完的事\n' % today, title='清单')
        res = api.todos_list('open')
        assert {i['text'] for i in res['items']} == {'今天的事', '逾期的事', '无期的事'}
        assert res['counts']['open'] == 3
        assert [i['text'] for i in api.todos_list('today')['items']] == ['今天的事']
        assert [i['text'] for i in api.todos_list('overdue')['items']] == ['逾期的事']
        assert [i['text'] for i in api.todos_list('nodue')['items']] == ['无期的事']
        assert [i['text'] for i in api.todos_list('done')['items']] == ['做完的事']
        assert all(i['note_title'] == '清单' for i in res['items'])
        assert nid

    def test_week_scope(self, api, backend_mod):
        soon = (backend_mod.datetime.now() + backend_mod.timedelta(days=3)).strftime('%Y-%m-%d')
        far = (backend_mod.datetime.now() + backend_mod.timedelta(days=30)).strftime('%Y-%m-%d')
        md_note(api, '- [ ] 三天后 📅 %s\n- [ ] 一个月后 📅 %s\n' % (soon, far))
        assert [i['text'] for i in api.todos_list('week')['items']] == ['三天后']

    def test_sorting_due_first_then_no_due(self, api):
        md_note(api, '- [ ] 后面 📅 2030-01-01\n- [ ] 先做 📅 2020-01-01\n- [ ] 无期\n')
        assert [i['text'] for i in api.todos_list('open')['items']] == ['先做', '后面', '无期']

    def test_trashed_note_todos_disappear(self, api):
        nid = md_note(api, '- [ ] 待办\n')
        assert len(api.todos_list('open')['items']) == 1
        api.notes_delete(nid)
        assert api.todos_list('open')['items'] == []

    def test_encrypted_note_never_leaks_todos(self, api):
        nid = md_note(api, '- [ ] 机密待办\n')
        assert len(api.todos_list('open')['items']) == 1
        api.note_set_password(nid, 'pw123456')
        assert api.todos_list('open')['items'] == [], '加密后派生必须清空（不得残留明文待办）'


class TestTodoToggle:
    def test_markdown_writes_back(self, api, backend_mod):
        nid = md_note(api, '- [ ] 甲\n- [ ] 乙\n')
        r = api.todo_toggle(nid, 0, '甲')
        assert r['ok'] and r['done'] == 1
        content = api.notes_get(nid)['content']
        assert '- [x] 甲' in content and '- [ ] 乙' in content, content
        assert derived(backend_mod, nid)['todo_open'] == 1
        assert api.todo_toggle(nid, 0, '甲')['ok']
        assert '- [ ] 甲' in api.notes_get(nid)['content']

    def test_delta_flips_line_attribute(self, api, backend_mod):
        nid = delta_note(backend_mod, [('甲', {'list': 'unchecked'}), ('乙', {'list': 'unchecked'})])
        assert api.todo_toggle(nid, 1, '乙')['ok']
        ops = json.loads(api.notes_get(nid)['content'])['ops']
        lists = [o['attributes']['list'] for o in ops if o.get('attributes', {}).get('list')]
        assert lists == ['unchecked', 'checked'], lists
        assert derived(backend_mod, nid)['todo_open'] == 1

    def test_text_mismatch_refused(self, api):
        nid = md_note(api, '- [ ] 甲\n')
        r = api.todo_toggle(nid, 0, '完全不同的文字')
        assert r['ok'] is False and '变化' in r['error']
        assert '- [ ] 甲' in api.notes_get(nid)['content']

    def test_toggle_works_with_ascii_due_alias(self, api, backend_mod):
        """别名写的截止日同样能勾：写回的文本校验剥标记要走同一条正则，否则一勾就报"正文变化" """
        nid = md_note(api, '- [ ] 交报告 @2026-09-25\n')
        r = api.todo_toggle(nid, 0, '交报告')
        assert r['ok'] and r['done'] == 1, r
        content = api.notes_get(nid)['content']
        assert '- [x] 交报告 @2026-09-25' in content, content
        assert derived(backend_mod, nid)['todo_open'] == 0

    def test_missing_todo_refused(self, api):
        nid = md_note(api, '没有待办\n')
        assert api.todo_toggle(nid, 0, 'x')['ok'] is False

    def test_no_version_created(self, api):
        """勾待办不是编辑行为：不该每次塞一个历史版本"""
        nid = md_note(api, '- [ ] 甲\n- [ ] 乙\n')
        before = len(api.versions_list(nid))
        api.todo_toggle(nid, 0, '甲')
        api.todo_toggle(nid, 1, '乙')
        assert len(api.versions_list(nid)) == before

    def test_encrypted_locked_refused(self, api):
        nid = md_note(api, '- [ ] 甲\n')
        api.note_set_password(nid, 'pw123456')
        api.note_lock(nid)
        r = api.todo_toggle(nid, 0, '甲')
        assert r['ok'] is False and '解锁' in r['error']

    def test_encrypted_unlocked_can_toggle(self, api):
        nid = md_note(api, '- [ ] 甲\n')
        api.note_set_password(nid, 'pw123456')
        assert api.note_verify_password(nid, 'pw123456')
        assert api.todo_toggle(nid, 0, '甲')['ok']
        assert '- [x] 甲' in api.notes_get(nid)['content']

    def test_search_reflects_toggle(self, api):
        nid = md_note(api, '- [ ] 甲\n')
        assert nid in api.notes_search('todo:open')['ids']
        api.todo_toggle(nid, 0, '甲')
        assert nid not in api.notes_search('todo:open')['ids']


# ---------------- 标签管理 ----------------

class TestTagManagement:
    def test_tags_list_counts(self, api):
        tid = api.tags_create('数学')['id']
        n1 = md_note(api, '甲\n')
        md_note(api, '乙\n')
        api.note_tags_set(n1, [tid])
        rows = {t['id']: t for t in api.tags_list()}
        assert rows[tid]['note_count'] == 1

    def test_rename(self, api):
        tid = api.tags_create('数学')['id']
        r = api.tags_rename(tid, '高数')
        assert r['ok'] and r['tag']['name'] == '高数'

    def test_rename_conflict_suggests_merge(self, api):
        a = api.tags_create('甲')['id']
        api.tags_create('乙')
        r = api.tags_rename(a, '乙')
        assert r['ok'] is False and '合并' in r['error']

    def test_rename_into_hierarchy_affects_search(self, api):
        tid = api.tags_create('子')['id']
        api.tags_rename(tid, '项目/子')
        nid = md_note(api, '甲\n')
        api.note_tags_set(nid, [tid])
        assert nid in api.notes_search('tag:项目')['ids']

    def test_merge_moves_and_dedupes(self, api):
        src = api.tags_create('旧名')['id']
        dst = api.tags_create('新名')['id']
        n1, n2 = md_note(api, '甲\n'), md_note(api, '乙\n')
        api.note_tags_set(n1, [src])
        api.note_tags_set(n2, [src, dst])
        r = api.tags_merge(src, dst)
        assert r['ok'] and r['moved'] == 2
        assert all(t['id'] != src for t in api.tags_list())
        assert {t['id'] for t in api.note_tags_get(n1)} == {dst}
        assert {t['id'] for t in api.note_tags_get(n2)} == {dst}, '合并不该产生重复关联'

    def test_merge_into_self_refused(self, api):
        tid = api.tags_create('甲')['id']
        assert api.tags_merge(tid, tid)['ok'] is False


# ---------------- 保存的搜索 ----------------

class TestSavedSearches:
    def test_create_list_with_count(self, api):
        md_note(api, '甲\n', title='苹果')
        md_note(api, '乙\n', title='汽车')
        assert api.saved_search_create('只看苹果', '苹果')['ok']
        rows = api.saved_searches_list()
        assert len(rows) == 1 and rows[0]['name'] == '只看苹果' and rows[0]['count'] == 1

    def test_empty_query_refused(self, api):
        assert api.saved_search_create('空', '  ')['ok'] is False

    def test_name_defaults_to_query(self, api):
        api.saved_search_create('', 'tag:数学')
        assert api.saved_searches_list()[0]['name'] == 'tag:数学'

    def test_update_and_delete(self, api):
        sid = api.saved_search_create('甲', '苹果')['id']
        assert api.saved_search_update(sid, {'name': '乙', 'query': '汽车'})['ok']
        row = api.saved_searches_list()[0]
        assert row['name'] == '乙' and row['query'] == '汽车'
        assert api.saved_search_update(sid, {'nope': 1})['ok'] is False
        assert api.saved_search_delete(sid)['ok']
        assert api.saved_searches_list() == []

    def test_order_preserved(self, api):
        api.saved_search_create('一', 'a')
        api.saved_search_create('二', 'b')
        assert [r['name'] for r in api.saved_searches_list()] == ['一', '二']


# ---------------- 界面提示 ⟷ 解析器（文档不能和实现分叉） ----------------

class TestTodoHintMatchesParser:
    """待办面板底部那句提示，教的就是用户要照抄的语法 —— 它必须真的能被解析。

    为什么值得一条测试：提示文案是手写的，解析器是代码；两边一分叉（比如提示教了
    `@2026-09-25` 而正则只认 `📅`），用户照着写却发现"面板里什么都没有"，
    而且这种错**不会**在任何别的测试里露头。顺带把"提示里不许出现 emoji"也钉在这里
    （面板要一行放得下，且 UI 图标一律 SVG）。
    """

    def _hint(self):
        html = open(os.path.join(PROJECT_ROOT, 'renderer', 'index.html'), encoding='utf-8').read()
        m = re.search(r'<p class="todo-hint">(.*?)</p>', html, re.S)
        assert m, 'index.html 里找不到 .todo-hint'
        return m.group(1)

    def test_hint_example_is_parseable(self, backend_mod):
        code = re.search(r'<code>(.*?)</code>', self._hint(), re.S)
        assert code, '提示里应当有一个可照抄的 <code> 示例'
        example = code.group(1)
        assert backend_mod._TODO_RE.match(example), \
            '提示里的示例不是合法待办：%r' % example
        due = backend_mod._DUE_RE.search(example)
        assert due and due.group(1) == '2026-09-25', \
            '提示里的日期写法解析不出来（用户照抄就没日期）：%r' % example

    def test_hint_has_no_emoji(self, backend_mod):
        from test_ui_icons import ICON_EMOJI
        bad = [c for c in self._hint() if c in ICON_EMOJI]
        assert not bad, '提示里又出现 emoji 了：%s' % ''.join(bad)

    def test_hint_matches_renderer_text(self, api, backend_mod):
        """照提示写一行，面板看到的文字/日期要和提示的意思一致（去掉标记、留下事项）"""
        nid = md_note(api, '- [ ] 事项 @2026-09-25\n')
        idx, text, due, _done = todos(backend_mod, nid)[0]
        assert text == '事项' and due == '2026-09-25', (idx, text, due)
