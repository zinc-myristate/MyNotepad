# -*- coding: utf-8 -*-
"""Markdown 导出/导入：Delta ↔ Markdown 的常用子集往返。

重点不是"格式全支持"，而是**不丢内容**与**常见结构能往返**：
标题/段落/粗斜删/行内码/链接/列表/待办/引用/代码块/图片相对路径。
"""
import json
import os

import pytest


def delta(*ops):
    return json.dumps({'ops': list(ops)}, ensure_ascii=False)


def to_delta_text(delta_json):
    """Delta → 纯文本（便于断言内容没丢）"""
    data = json.loads(delta_json)
    return ''.join(op['insert'] for op in data['ops']
                   if isinstance(op.get('insert'), str))


class TestExport:
    def test_headings_and_paragraph(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta(
            {'insert': '大标题'}, {'insert': '\n', 'attributes': {'header': 1}},
            {'insert': '正文一段。'}, {'insert': '\n'},
        ))
        assert md.startswith('# 大标题')
        assert '正文一段。' in md

    def test_inline_formats(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta(
            {'insert': '粗', 'attributes': {'bold': True}},
            {'insert': '斜', 'attributes': {'italic': True}},
            {'insert': '删', 'attributes': {'strike': True}},
            {'insert': '码', 'attributes': {'code': True}},
            {'insert': '链', 'attributes': {'link': 'https://x.com'}},
            {'insert': '\n'},
        ))
        assert '**粗**' in md and '*斜*' in md and '~~删~~' in md
        assert '`码`' in md and '[链](https://x.com)' in md

    def test_lists_and_todos(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta(
            {'insert': '第一'}, {'insert': '\n', 'attributes': {'list': 'bullet'}},
            {'insert': '第二'}, {'insert': '\n', 'attributes': {'list': 'ordered'}},
            {'insert': '做完了'}, {'insert': '\n', 'attributes': {'list': 'checked'}},
            {'insert': '还没做'}, {'insert': '\n', 'attributes': {'list': 'unchecked'}},
            {'insert': '引用'}, {'insert': '\n', 'attributes': {'blockquote': True}},
        ))
        assert '- 第一' in md and '1. 第二' in md
        assert '- [x] 做完了' in md and '- [ ] 还没做' in md
        assert '> 引用' in md

    def test_multiline_code_block_is_one_fence(self, backend_mod):
        """多行代码块在 Quill 里是**一个** op（内部含换行）带 code-block 属性，
        转换时必须合并成一个 ``` 块，而不是每行一个围栏。"""
        md = backend_mod._delta_to_markdown(delta(
            {'insert': 'a = [1, 2]\nb = "*x*"\n', 'attributes': {'code-block': 'plain'}},
        ))
        assert md.count('```') == 2, '应只有一对围栏：%r' % md
        assert 'a = [1, 2]' in md and 'b = "*x*"' in md
        assert '\\[' not in md and '\\*' not in md, '代码块内不该转义'

    def test_single_line_code_block(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta(
            {'insert': 'print(1)'}, {'insert': '\n', 'attributes': {'code-block': 'plain'}},
        ))
        assert '```\nprint(1)\n```' in md

    def test_metadata_chars_escaped_in_text(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta({'insert': '价格 *5* 元_特价'}, {'insert': '\n'}))
        assert '\\*5\\*' in md and '\\_特价' in md, '正文里的 * _ 必须转义，否则编辑后格式会变'

    def test_formula_and_attachment(self, backend_mod):
        md = backend_mod._delta_to_markdown(delta(
            {'insert': '公式'}, {'insert': '\n'},
            {'insert': {'math-formula': {'latex': 'E=mc^2'}}}, {'insert': '\n'},
            {'insert': {'attachment': {'originalName': '讲义.pdf', 'filename': 'a.pdf'}}},
            {'insert': '\n'},
        ))
        assert '$E=mc^2$' in md and '讲义.pdf' in md

    def test_image_uses_resolver(self, backend_mod):
        md = backend_mod._delta_to_markdown(
            delta({'insert': {'image': {'filename': 'p.png'}}}, {'insert': '\n'}),
            resolve_image=lambda name: 'note.assets/' + name)
        assert '![](note.assets/p.png)' in md

    def test_image_without_resolver_degrades_to_text(self, backend_mod):
        md = backend_mod._delta_to_markdown(
            delta({'insert': {'image': {'filename': 'p.png'}}}, {'insert': '\n'}))
        assert 'p\\.png' in md or 'p.png' in md
        assert '![]' not in md, '拿不到真实路径就别写一个断掉的图片语法'


class TestExportToFile:
    def test_writes_title_and_copies_images(self, api, tmp_path):
        png = tmp_path / 'p.png'
        import base64
        png.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))
        nid = api.notes_create()['id']
        info = api.file_copy_to_note(str(png), nid, 'image')
        body = delta({'insert': '看这张图：'}, {'insert': '\n'},
                     {'insert': {'image': {'filename': info['filename']}}}, {'insert': '\n'})
        api.notes_update(nid, {'title': '带图的笔记', 'content': body})

        out = str(tmp_path / 'note.md')
        assert api.export_note_markdown(nid, out) is True
        text = open(out, encoding='utf-8').read()
        assert text.startswith('# 带图的笔记'), text[:40]
        assert 'note.assets/' + info['filename'] in text
        assert os.path.isfile(str(tmp_path / 'note.assets' / info['filename'])), \
            '图片必须复制到同级 assets 目录，否则换机器就断图'

    def test_encrypted_locked_refused(self, api, tmp_path):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '机密', 'content': delta({'insert': '秘密'}, {'insert': '\n'})})
        api.note_set_password(nid, 'pass123456')
        api.note_lock(nid)
        assert api.export_note_markdown(nid, str(tmp_path / 'x.md')) is False
        assert not os.path.exists(str(tmp_path / 'x.md'))

    def test_encrypted_unlocked_exports_plaintext(self, api, tmp_path):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '机密', 'content': delta({'insert': '秘密内容'}, {'insert': '\n'})})
        api.note_set_password(nid, 'pass123456')
        out = str(tmp_path / 'x.md')
        assert api.export_note_markdown(nid, out) is True
        assert '秘密内容' in open(out, encoding='utf-8').read()

    def test_missing_note(self, api, tmp_path):
        assert api.export_note_markdown('nope', str(tmp_path / 'x.md')) is False


class TestImport:
    def test_headings_paragraphs_lists(self, api):
        md = ('# 标题\n\n正文一段\n\n## 小节\n\n- 项目一\n- 项目二\n\n1. 第一\n'
              '2. 第二\n\n- [x] 完成\n- [ ] 未完成\n\n> 引用\n\n```\ncode here\n```\n')
        note = api.import_markdown(md)
        assert note and note['title'] == '标题', '标题应取自第一个非空行'
        ops = json.loads(note['content'])['ops']
        attrs = [op.get('attributes', {}) for op in ops if isinstance(op.get('insert'), str)]
        assert any(a.get('header') == 1 for a in attrs)
        assert any(a.get('header') == 2 for a in attrs)
        assert any(a.get('list') == 'bullet' for a in attrs)
        assert any(a.get('list') == 'ordered' for a in attrs)
        assert any(a.get('list') == 'checked' for a in attrs)
        assert any(a.get('list') == 'unchecked' for a in attrs)
        assert any(a.get('blockquote') for a in attrs)
        assert any(a.get('code-block') for a in attrs)
        assert 'code here' in to_delta_text(note['content']), '代码块内容不能丢'

    def test_inline_formats(self, api):
        note = api.import_markdown('有 **粗** 和 *斜* 和 ~~删~~ 和 `码` 和 [链接](https://a.b)\n')
        ops = json.loads(note['content'])['ops']
        flags = [op.get('attributes', {}) for op in ops]
        assert any(f.get('bold') for f in flags)
        assert any(f.get('italic') for f in flags)
        assert any(f.get('strike') for f in flags)
        assert any(f.get('code') for f in flags)
        assert any(f.get('link') == 'https://a.b' for f in flags)

    def test_escaped_chars_survive(self, api):
        note = api.import_markdown('价格 \\*5\\* 元\n')
        assert '价格 *5* 元' in to_delta_text(note['content'])

    def test_plain_text_lines_kept(self, api):
        note = api.import_markdown('第一行\n第二行\n\n第四行\n')
        text = to_delta_text(note['content'])
        for part in ('第一行', '第二行', '第四行'):
            assert part in text

    def test_imported_note_is_searchable_and_indexed(self, api):
        note = api.import_markdown('# 导入测试\n\n独特的检索词甲\n')
        assert note['id'] in set(api.notes_search('独特的检索词')['ids'])

    def test_import_into_notebook(self, api):
        nb = api.notebooks_create('资料')['id']
        note = api.import_markdown('内容', title='手动标题', notebook_id=nb)
        assert note['title'] == '手动标题' and note['notebook_id'] == nb

    def test_empty_input_still_makes_a_note(self, api):
        note = api.import_markdown('')
        assert note and note['title'] == '导入的笔记'


class TestRoundTrip:
    @pytest.mark.parametrize('md', [
        '# 标题\n\n正文\n',
        '# 标题\n\n- 甲\n- 乙\n',
        '> 引用一句\n',
        '有 **粗** 和 `码`\n',
    ])
    def test_export_import_export_is_stable(self, api, tmp_path, md):
        """导出 → 导入 → 再导出，Markdown 应该基本稳定（格式不退化）"""
        note = api.import_markdown(md)
        f1 = str(tmp_path / 'a.md')
        assert api.export_note_markdown(note['id'], f1)
        first = open(f1, encoding='utf-8').read()

        note2 = api.import_markdown(first, title='二次导入')
        f2 = str(tmp_path / 'b.md')
        assert api.export_note_markdown(note2['id'], f2)
        second = open(f2, encoding='utf-8').read()

        # 第二次导出的正文（去掉标题行）应与第一次一致
        strip = lambda s: '\n'.join(ln for ln in s.splitlines() if not ln.startswith('# ')).strip()
        assert strip(second) == strip(first), '往返后格式发生了变化：\n%r\n---\n%r' % (first, second)
