# -*- coding: utf-8 -*-
"""Delta ↔ Markdown 转换器（第 6 轮第 2 步）：**保内容**优先，格式尽力而为。

为什么这一层要测得这么细：它要处理用户**已有的老笔记**。老笔记里可能有贴纸、12 种分割线、
彩色字号、表格、附件卡片——转换一旦丢字，用户是看不见的（打开还在，但内容少了）。
所以这里的断言分两类：
  · 保内容：所有文字必须都能在转换结果里找到（含未知 embed 的兜底抽文本）
  · 保格式：能无损往返的（加粗/列表/待办/公式/表格）必须逐字符稳定
"""
import json


def d(*ops):
    return json.dumps({'ops': list(ops)}, ensure_ascii=False)


def line(text, **attrs):
    op = {'insert': text + '\n'}
    if attrs:
        op['attributes'] = attrs
    return op


def embed(kind, value):
    return {'insert': {kind: value}}


class TestDeltaToMarkdown:
    def test_basic_blocks(self, backend_mod):
        md = backend_mod._delta_to_markdown(d(
            line('标题', header=2),
            line('普通段落'),
            line('项目一', list='bullet'),
            line('待办', list='unchecked'),
            line('做完的', list='checked'),
            line('引用', blockquote=True),
            line('代码', **{'code-block': True}),
        ))
        assert '## 标题' in md
        assert '普通段落' in md
        assert '- 项目一' in md
        assert '- [ ] 待办' in md
        assert '- [x] 做完的' in md
        assert '> 引用' in md
        assert '```' in md and '代码' in md

    def test_inline_formats(self, backend_mod):
        md = backend_mod._delta_to_markdown(d(
            {'insert': '粗', 'attributes': {'bold': True}},
            {'insert': '斜', 'attributes': {'italic': True}},
            {'insert': '删', 'attributes': {'strike': True}},
            {'insert': '码', 'attributes': {'code': True}},
            {'insert': '链', 'attributes': {'link': 'https://x'}},
            line(''),
        ))
        assert '**粗**' in md and '*斜*' in md and '~~删~~' in md
        assert '`码`' in md and '[链](https://x)' in md

    def test_rich_text_becomes_whitelisted_html(self, backend_mod):
        """颜色/字号/字体/下划线/上下标 → 白名单内嵌 HTML（Obsidian 能渲染）"""
        md = backend_mod._delta_to_markdown(d(
            {'insert': '红', 'attributes': {'color': '#B8844A'}},
            {'insert': '大', 'attributes': {'size': '24px'}},
            {'insert': '手写', 'attributes': {'myfont': 'cursive'}},
            {'insert': '下划线', 'attributes': {'underline': True}},
            {'insert': '上标', 'attributes': {'script': 'super'}},
            {'insert': '下标', 'attributes': {'script': 'sub'}},
            line(''),
        ))
        assert '<span style="color: #B8844A">红</span>' in md
        assert '<span style="font-size: 24px">大</span>' in md
        assert '<span class="md-font-hand">手写</span>' in md
        assert '<u>下划线</u>' in md
        assert '<sup>上标</sup>' in md and '<sub>下标</sub>' in md

    def test_dangerous_values_are_dropped(self, backend_mod):
        """非白名单取值（伪协议颜色、巨大字号、未知字体）一律不写进 Markdown"""
        md = backend_mod._delta_to_markdown(d(
            {'insert': 'x', 'attributes': {'color': 'javascript:alert(1)'}},
            {'insert': 'y', 'attributes': {'size': '99999px;position:fixed'}},
            {'insert': 'z', 'attributes': {'myfont': 'evil'}},
            line(''),
        ))
        assert 'javascript' not in md and 'position' not in md and 'evil' not in md
        assert 'x' in md and 'y' in md and 'z' in md

    def test_embeds(self, backend_mod):
        md = backend_mod._delta_to_markdown(
            d(embed('math-formula', {'latex': 'a^2', 'display': False}),
              line(''),
              embed('divider', {'style': 3}),
              line(''),
              embed('attachment', {'filename': 'f.pdf', 'originalName': '报告.pdf'}),
              line(''),
              embed('sticker', {'emoji': '🎉'}),
              line('')),
            note_id='N1')
        assert '$a^2$' in md
        assert '<hr class="divider-3">' in md
        assert '[📎 报告.pdf](attachments/N1/f.pdf)' in md
        assert '<span class="md-sticker">🎉</span>' in md

    def test_block_math_uses_double_dollar(self, backend_mod):
        md = backend_mod._delta_to_markdown(d(
            embed('math-formula', {'latex': '\\int x', 'display': True}), line('')))
        assert '$$\\int x$$' in md

    def test_unknown_embed_keeps_text(self, backend_mod):
        """未知 embed 绝不能静默丢字（宁可丢结构）"""
        md = backend_mod._delta_to_markdown(d(
            embed('some-future-blot', {'text': '重要内容', 'meta': {'label': '附注'}}), line('')))
        assert '重要内容' in md and '附注' in md

    def test_table_becomes_gfm(self, backend_mod):
        md = backend_mod._delta_to_markdown(d(
            line('列A', **{'table': 'r1', 'table-cell': 'r1-0', 'table-cell-line': 'last'}),
            line('列B', **{'table': 'r1', 'table-cell': 'r1-1', 'table-cell-line': 'last'}),
            line('1', **{'table': 'r2', 'table-cell': 'r2-0', 'table-cell-line': 'last'}),
            line('2', **{'table': 'r2', 'table-cell': 'r2-1', 'table-cell-line': 'last'}),
        ))
        assert '| 列A | 列B |' in md
        assert '| --- | --- |' in md
        assert '| 1 | 2 |' in md

    def test_image_uses_resolver(self, backend_mod):
        md = backend_mod._delta_to_markdown(
            d(embed('image', {'filename': 'p.png'}), line('')),
            resolve_image=lambda name: 'x.assets/' + name)
        assert '![](x.assets/p.png)' in md

    def test_image_without_resolver_keeps_filename(self, backend_mod):
        md = backend_mod._delta_to_markdown(d(embed('image', {'filename': 'p.png'}), line('')))
        # 没有 resolver 时退化成文件名**文本**：点号被转义（渲染出来仍是 p.png）
        assert 'p.png' in md.replace('\\', '')


class TestMarkdownToDelta:
    def test_blocks(self, backend_mod):
        delta = json.loads(backend_mod.markdown_to_delta(
            '# 一级\n\n段落\n\n- 项\n- [ ] 待办\n- [x] 完成\n\n> 引用\n\n```\ncode\n```\n'))
        attrs = [op.get('attributes', {}) for op in delta['ops']]
        assert {'header': 1} in attrs
        assert {'list': 'bullet'} in attrs
        assert {'list': 'unchecked'} in attrs and {'list': 'checked'} in attrs
        assert {'blockquote': True} in attrs
        assert any(a.get('code-block') for a in attrs)

    def test_inline_html_restored(self, backend_mod):
        delta = json.loads(backend_mod.markdown_to_delta(
            '<span style="color: #B8844A">红</span><span style="font-size: 24px">大</span>'
            '<span class="md-font-hand">手写</span><u>下划线</u><sup>2</sup><sub>3</sub>\n'))
        attrs = [op.get('attributes', {}) for op in delta['ops'] if op.get('insert') != '\n']
        merged = {k: v for a in attrs for k, v in a.items()}
        assert merged.get('color') == '#B8844A'
        assert merged.get('size') == '24px'
        assert merged.get('myfont') == 'cursive'
        assert merged.get('underline') is True
        assert merged.get('script') in ('super', 'sub')

    def test_unsafe_style_ignored(self, backend_mod):
        delta = json.loads(backend_mod.markdown_to_delta(
            '<span style="position:fixed;color:#000">x</span>\n'))
        attrs = [op.get('attributes', {}) for op in delta['ops'] if op.get('insert') != '\n']
        merged = {k: v for a in attrs for k, v in a.items()}
        assert 'position' not in merged
        assert merged.get('color') == '#000'

    def test_math_and_embeds(self, backend_mod):
        delta = json.loads(backend_mod.markdown_to_delta(
            '行内 $a^2$\n\n$$\\int x$$\n\n![](attachments/N1/p.png)\n\n'
            '[📎 报告.pdf](attachments/N1/f.pdf)\n\n<hr class="divider-3">\n'))
        kinds = [next(iter(op['insert'])) for op in delta['ops'] if isinstance(op['insert'], dict)]
        assert kinds.count('math-formula') == 2
        assert 'image' in kinds and 'attachment' in kinds and 'divider' in kinds

    def test_gfm_table_becomes_table_attrs(self, backend_mod):
        delta = json.loads(backend_mod.markdown_to_delta(
            '| A | B |\n| --- | --- |\n| 1 | 2 |\n'))
        rows = {op['attributes']['table'] for op in delta['ops']
                if op.get('attributes', {}).get('table')}
        assert len(rows) == 2, '表头行 + 数据行应各成一行'

    def test_latex_standard_delims_become_formulas(self, backend_mod):
        r"""`\(…\)` / `\[…\]` 必须被认成公式 —— 而不是当成普通文本把反斜杠吃掉。

        实测踩到：原实现里 `\(C_0\)` 会变成 `(C_0)`、`\(\alpha+\beta\)` 变成
        `(alpha+beta)`，**公式被静默破坏**。用户那篇 33683 字的
        《Lecture 2.2：相图与相变》里 `\(` 出现 165 次、`$` 出现 0 次 —— 整篇都是这么写的，
        所以在预览里全是原始 LaTeX 文本、转成 delta 也全丢。
        """
        delta = json.loads(backend_mod.markdown_to_delta(
            '相成分 \\(C_\\alpha\\) 与块级：\n\n\\[E=mc^2\\]\n'))
        maths = [op['insert']['math-formula'] for op in delta['ops']
                 if isinstance(op['insert'], dict) and 'math-formula' in op['insert']]
        assert len(maths) == 2, '应识别 1 个行内 + 1 个块级，实际 %r' % (maths,)
        inline, block = maths[0], maths[1]
        assert inline['latex'] == 'C_\\alpha' and inline['display'] is False
        assert block['latex'] == 'E=mc^2' and block['display'] is True
        # 反斜杠不能被吃掉（这是"静默破坏"的核心症状）
        texts = ''.join(op['insert'] for op in delta['ops']
                        if isinstance(op['insert'], str))
        assert '\\alpha' not in texts, '命令不该以裸文本形式残留'

    def test_latex_delims_inside_table_cells(self, backend_mod):
        r"""表格单元格里的 `\(…\)` 同样要变成公式（用户那篇笔记的公式大量在表格里）。"""
        delta = json.loads(backend_mod.markdown_to_delta(
            '| 中文 | English |\n| --- | --- |\n'
            '| 如 \\(L\\)、\\(\\alpha\\) | e.g. \\(L\\) |\n'))
        n = sum(1 for op in delta['ops']
                if isinstance(op['insert'], dict) and 'math-formula' in op['insert'])
        assert n == 3, '单元格内应有 3 个公式，实际 %d' % n

    def test_code_fence_keeps_backslash_paren_literal(self, backend_mod):
        r"""代码块里的 `\(` 是**字面量**，不能当公式改写。

        （踩过：归一化只判"这行是不是围栏"，于是代码块**内部**的行照样被改写成 `$x$`。）
        """
        delta = json.loads(backend_mod.markdown_to_delta(
            '```\n正则 \\(x\\) 的写法\n```\n'))
        n = sum(1 for op in delta['ops']
                if isinstance(op['insert'], dict) and 'math-formula' in op['insert'])
        assert n == 0, '代码块里不该有公式'
        code = ''.join(op['insert'] for op in delta['ops']
                       if isinstance(op.get('insert'), str))
        assert '\\(x\\)' in code, '代码块内容应逐字保留，实际 %r' % code

    def test_escaped_pipe_stays_in_cell(self, backend_mod):
        r"""表格单元格里的 `\|` 是**内容**（GFM 转义），不该被当成列分隔符。

        原来直接 `split('|')`，于是单元格里含竖线的公式（`$|x|$` 那种）会把表格切坏、
        公式整个失效（实测：`$|x|$` 所在表格的公式数为 0）。
        现在 md→delta 认 `\|`、delta→md 写回 `\|`，往返闭合。
        """
        delta = json.loads(backend_mod.markdown_to_delta(
            '| A | B |\n| --- | --- |\n| 含竖线 a \\| b | 2 |\n'))
        cells = [op['insert'] for op in delta['ops']
                 if isinstance(op.get('insert'), str) and op['insert'].strip() != '\n']
        assert any('a | b' in c for c in cells), \
            '转义竖线应还原成普通竖线留在单元格里，实际 %r' % (cells,)


class TestRoundTrip:
    """能无损往返的必须稳定：delta → md → delta → md 两次 Markdown 必须逐字符相同"""

    CASES = [
        d(line('标题', header=1), line('正文')),
        d(line('项一', list='bullet'), line('项二', list='bullet')),
        d(line('待办', list='unchecked'), line('完成', list='checked')),
        d(line('引用', blockquote=True)),
        d(embed('math-formula', {'latex': 'x^2+y^2', 'display': False}), line('')),
        d(embed('math-formula', {'latex': '\\sum_i x_i', 'display': True}), line('')),
        d({'insert': '粗', 'attributes': {'bold': True}},
          {'insert': '斜', 'attributes': {'italic': True}},
          {'insert': '删', 'attributes': {'strike': True}}, line('')),
        d({'insert': '红', 'attributes': {'color': '#B8844A'}},
          {'insert': '大', 'attributes': {'size': '24px'}}, line('')),
        d({'insert': '下划线', 'attributes': {'underline': True}},
          {'insert': '上', 'attributes': {'script': 'super'}}, line('')),
        d(embed('divider', {'style': 5}), line('')),
        d(line('列A', **{'table': 'r1', 'table-cell': 'r1-0', 'table-cell-line': 'last'}),
          line('列B', **{'table': 'r1', 'table-cell': 'r1-1', 'table-cell-line': 'last'})),
    ]

    def test_stable(self, backend_mod):
        for case in self.CASES:
            md1 = backend_mod._delta_to_markdown(case, note_id='N1')
            back = backend_mod.markdown_to_delta(md1)
            md2 = backend_mod._delta_to_markdown(back, note_id='N1')
            assert md1 == md2, '往返不稳定：\n--- 第一次 ---\n%s\n--- 第二次 ---\n%s' % (md1, md2)

    def test_text_never_lost(self, backend_mod):
        """保内容优先：把各种零散文本混在一起转一圈，每个词都必须还在"""
        words = ['甲', '乙丙', '丁戊己', '表格单元', '附件名']
        src = d(
            line(words[0], header=3),
            line(words[1], list='bullet'),
            line(words[2], blockquote=True),
            embed('math-formula', {'latex': 'E=mc^2', 'display': False}), line(''),
            embed('attachment', {'filename': 'a.pdf', 'originalName': words[4]}), line(''),
            embed('sticker', {'emoji': '🎈'}), line(''),
            line(words[3], **{'table': 't1', 'table-cell': 't1-0', 'table-cell-line': 'last'}),
            line('第四格', **{'table': 't1', 'table-cell': 't1-1', 'table-cell-line': 'last'}),
        )
        md = backend_mod._delta_to_markdown(src, note_id='N1')
        for w in words + ['E=mc^2', '第四格', '🎈']:
            assert w in md, '转换后丢了内容：%s\n%s' % (w, md)
