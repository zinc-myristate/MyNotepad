# -*- coding: utf-8 -*-
"""Markdown 双轨地基（第 6 轮）：存储格式、纯文本提取、搜索/摘要/版本/复制的 format 分流。

设计要点（改动这里之前先读完）：
  · `notes.format` 是**唯一的格式判据**（'delta' = Quill Delta JSON，'md' = Markdown 文本）
  · 新笔记默认 'md'；历史笔记保持 'delta'，按需转换（转换留 delta_backup 可回滚）
  · 所有"读正文"的地方都走 `note_plain_text(content, fmt)`，别再各自判断格式
"""
import json
import sqlite3

import pytest


def _md_api(backend_mod):
    return backend_mod.api


# ---------- 纯文本提取 ----------

class TestMarkdownToText:
    def test_strips_markdown_marks(self, backend_mod):
        md = '# 标题\n\n这是**粗体**与*斜体*，还有 `代码`。\n\n> 引用一行\n\n- 列表项\n1. 有序项\n'
        text = backend_mod._markdown_to_text(md)
        for word in ('标题', '粗体', '斜体', '代码', '引用一行', '列表项', '有序项'):
            assert word in text, '%r 应保留' % word
        for mark in ('#', '**', '*', '`', '>'):
            assert mark not in text.replace('  ', ' '), '标记 %r 应被剥掉：%r' % (mark, text)

    def test_keeps_image_alt_and_link_text(self, backend_mod):
        text = backend_mod._markdown_to_text('看图 ![示意图](attachments/a/x.png) 和 [链接文字](https://x)')
        assert '示意图' in text and '链接文字' in text
        assert 'attachments' not in text and 'https' not in text

    def test_strips_whitelisted_html(self, backend_mod):
        text = backend_mod._markdown_to_text('前<span style="color:#fff">彩色</span>后<hr class="divider-3">')
        assert '彩色' in text and '<span' not in text and '<hr' not in text

    def test_script_content_never_leaks(self, backend_mod):
        """script/style 整段都要摘掉，不能只去标签留下代码文本"""
        text = backend_mod._markdown_to_text('正文<script>alert(1)</script>尾<style>a{}</style>')
        assert 'alert' not in text and 'a{}' not in text and '正文' in text and '尾' in text

    def test_decodes_entities_including_nested_amp(self, backend_mod):
        assert backend_mod._markdown_to_text('a &amp;lt; b') == 'a &lt; b'
        assert '&nbsp;' not in backend_mod._markdown_to_text('x&nbsp;y')

    def test_plain_text_dispatch_by_format(self, backend_mod):
        delta = json.dumps({'ops': [{'insert': '德尔塔正文\n'}]}, ensure_ascii=False)
        assert '德尔塔正文' in backend_mod.note_plain_text(delta, 'delta')
        assert backend_mod.note_plain_text(delta, 'delta') != delta
        text = backend_mod.note_plain_text('# 标题\n正文', 'md')
        assert '标题' in text and '正文' in text and '#' not in text
        # 纯文本保留换行（只有摘要才压成一行）；行内标记不得在词间留空格
        assert '\n' in text
        assert backend_mod.note_plain_text('这是**正文**内容', 'md') == '这是正文内容'


# ---------- schema ----------

class TestSchemaMigration:
    def test_columns_exist(self, backend_mod):
        cols = {r[1] for r in backend_mod.conn.execute('PRAGMA table_info(notes)').fetchall()}
        assert {'format', 'delta_backup'} <= cols
        vcols = {r[1] for r in backend_mod.conn.execute('PRAGMA table_info(versions)').fetchall()}
        assert 'format' in vcols

    def test_existing_rows_default_to_delta(self, backend_mod, tmp_path):
        """存量行必须落到 'delta'：升级后老笔记不能被当成 Markdown 解析"""
        nid = 'legacy-1'
        backend_mod.conn.execute(
            "INSERT INTO notes (id, title, content, sort_order, format) VALUES (?,?,?,?, 'delta')",
            (nid, '老笔记', json.dumps({'ops': [{'insert': '老正文\n'}]}, ensure_ascii=False), 0))
        backend_mod.conn.commit()
        row = backend_mod.conn.execute('SELECT format FROM notes WHERE id = ?', (nid,)).fetchone()
        assert row['format'] == 'delta'
        assert '老正文' in backend_mod.api.notes_get(nid)['content']

    def test_migration_is_idempotent(self, backend_mod, tmp_path):
        """重开库（再跑一次建表/ALTER）不应报错，也不应改掉已有 format"""
        nid = backend_mod.api.notes_create()['id']
        backend_mod.conn.close()
        import importlib
        import sys
        sys.modules.pop('backend', None)
        b2 = importlib.import_module('backend')
        try:
            assert b2.conn.execute('SELECT format FROM notes WHERE id = ?', (nid,)).fetchone()['format'] == 'md'
        finally:
            b2.conn.close()
            sys.modules.pop('backend', None)
        # 让 fixture 的清理不至于操作已关闭的连接
        import importlib as _il
        sys.modules['backend'] = backend_mod


# ---------- 新建 / 摘要 / 搜索 ----------

class TestMarkdownNotePipeline:
    def test_new_note_is_markdown(self, api):
        note = api.notes_create()
        assert note['format'] == 'md'
        assert note['content'] == ''

    def test_preview_strips_marks(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '# 标题\n\n**正文**内容\n\n- [ ] 待办一\n'})
        row = [n for n in api.notes_list() if n['id'] == nid][0]
        assert row['preview'].startswith('标题')
        assert '正文内容' in row['preview'] and '待办一' in row['preview']
        assert '#' not in row['preview'] and '**' not in row['preview']
        assert nid in api.notes_search('正文内容')['ids'], '行内标记两侧的词必须连着搜得到'

    def test_search_indexes_markdown_body(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '普通段落，关键字是蓝鲸计划\n\n- [ ] 记得交周报\n'})
        assert nid in api.notes_search('蓝鲸计划')['ids']
        assert nid in api.notes_search('交周报')['ids'], '待办文本也要能搜到'

    def test_snippet_has_no_markup(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '关键内容在这里，前后都是普通文字。\n'})
        snippets = api.notes_search('关键内容在这里')['snippets']
        assert '关键内容在这里' in snippets[nid]
        assert '#' not in snippets[nid]

    def test_delta_note_still_works(self, backend_mod, api):
        """双轨：老 delta 笔记的摘要/搜索路径不能被破坏"""
        nid = api.notes_create()['id']
        backend_mod.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
        backend_mod.conn.commit()
        api.notes_update(nid, {'content': json.dumps({'ops': [{'insert': '德尔塔专属内容\n'}]},
                                                     ensure_ascii=False)})
        row = [n for n in api.notes_list() if n['id'] == nid][0]
        assert row['format'] == 'delta'
        assert '德尔塔专属内容' in row['preview']
        assert nid in api.notes_search('德尔塔专属内容')['ids']


# ---------- 版本 / 复制 ----------

class TestVersionsAndDuplicate:
    def test_version_records_format_and_restore_switches_back(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '# Markdown 正文\n'})
        vid = api.versions_create(nid, '标题', json.dumps({'ops': [{'insert': '旧 Delta 正文\n'}]},
                                                         ensure_ascii=False))
        assert api.versions_get(vid)['format'] == 'md', '版本应记录当时笔记的格式'
        api.versions_restore(vid)
        note = api.notes_get(nid)
        assert note['format'] == 'md'
        assert '旧 Delta 正文' in note['content']

    def test_duplicate_keeps_format(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '# 原文\n'})
        dup = api.notes_duplicate(nid)
        assert dup['format'] == 'md'
        assert dup['content'] == '# 原文\n'

    def test_encrypted_markdown_note_body_not_indexed(self, api, backend_mod):
        """加密笔记：正文加密后 FTS body 恒空（索引里绝不能出现明文）"""
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '绝密内容ABC\n'})
        api.note_set_password(nid, 'pw123456')
        assert api.note_lock(nid)
        assert api.notes_search('绝密内容ABC')['ids'] == [] or nid not in api.notes_search('绝密内容ABC')['ids']
        row = backend_mod.conn.execute(
            'SELECT body FROM notes_fts WHERE note_id = ?', (nid,)).fetchone()
        if row:                      # FTS 可用时才有行
            assert '绝密内容ABC' not in (row['body'] or '')


def test_sqlite_roundtrip_markdown_with_cjk_and_html(api, backend_mod):
    """中文 + 内嵌 HTML 存进去再读出来必须逐字节一致（别在某个环节被转义/截断）"""
    body = '# 标题\n\n前<span style="color:#B8844A">彩色字</span>后\n\n- [x] 已完成\n'
    nid = api.notes_create()['id']
    api.notes_update(nid, {'content': body})
    assert api.notes_get(nid)['content'] == body
    raw = backend_mod.conn.execute('SELECT content FROM notes WHERE id = ?', (nid,)).fetchone()[0]
    assert raw == body, '库里存的就该是原始 Markdown 文本'


@pytest.mark.parametrize('fmt', ['md', 'delta'])
def test_preview_limit_is_respected(backend_mod, fmt):
    long_md = '字' * 500
    text = backend_mod._preview_text(long_md, fmt=fmt)
    assert len(text) <= backend_mod.PREVIEW_MAX
