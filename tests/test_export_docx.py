# -*- coding: utf-8 -*-
"""导出 DOCX：直接喂构造的 HTML，看写出来的段落、表格和格式。

HTML 的结构照抄两条路的真实输出（Markdown：markdownExportHtml，富文本：quill.root.innerHTML）。
以前的转换按正则切块、再猜"这一块是不是标签"，下面这些都丢过内容：只含一个公式的段落、
以加粗或公式结尾的列表项后面的项、表格、缩进代码、有序列表；`<li>`、`<a href>`、`&nbsp;`
还会原样写进正文。所以断言都是"每样东西都在、只有字没有标签"，不只看"没报错"。
实机走真实渲染器的回归在 test_markdown_editor.py 和本文件末尾。
"""
import base64
import json
import os
import re

import pytest
from conftest import load_app_partial, make_delta_note, wait_for_js

PNG_1PX = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

# 正文里不该出现的东西：结构标签、没还原的实体
LEAKS = re.compile(r'</?(?:p|div|li|ul|ol|table|thead|tbody|tr|td|th|span|a|strong|em|s|del|code|pre|'
                   r'sub|sup|hr|br|img|blockquote|details|summary|h[1-6]|math|annotation|svg|path|select|'
                   r'option|button)\b|&(?:lt|gt|amp|nbsp|quot|#\d+);')


def katex(tex, display=False, svg=True):
    """与 KaTeX 的输出同构：MathML（带 TeX 源码）+ 排版用的 .katex-html。

    .katex-html 里的字按排版的需要排列，不是阅读顺序：分式的分母在分子前面，根号是 SVG。
    这里故意照样写（`b`、`a`），看导出有没有拿它当正文。
    """
    escaped = tex.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    tail = '<span class="hide-tail"><svg width="400em"><path d="M95,702"></path></svg></span>' if svg else ''
    core = ('<span class="katex"><span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<semantics><mrow><mi>b</mi><mi>a</mi></mrow><annotation encoding="application/x-tex">%s'
            '</annotation></semantics></math></span><span aria-hidden="true" class="katex-html">'
            '<span class="base"><span class="strut" style="height:1em;"></span><span class="mord">b</span>'
            '%s<span class="vlist-s">\u200b</span><span class="mord">a</span></span></span></span>'
            % (escaped, tail))
    return '<span class="katex-display">%s</span>' % core if display else core


def export(api, tmp_path, html, name='out.docx', title='标题'):
    from docx import Document
    path = str(tmp_path / name)
    assert api.export_note(title, html, 'docx', path) == {'ok': True}
    return Document(path)


def blocks(container):
    """按文档顺序：段落 → 文字；表格 → [[格子的文字, ...], ...]（格子里几段用换行连起来）"""
    from docx.text.paragraph import Paragraph
    out = []
    for item in container.iter_inner_content():
        if isinstance(item, Paragraph):
            out.append(item.text)
        else:
            out.append([['\n'.join(str(b) for b in blocks(cell)) for cell in row.cells] for row in item.rows])
    return out


def body(api, tmp_path, html):
    """正文的块（去掉第一段的标题）"""
    return blocks(export(api, tmp_path, html))[1:]


def paragraphs(api, tmp_path, html):
    return export(api, tmp_path, html).paragraphs[1:]


def all_text(doc):
    def walk(items):
        for item in items:
            if isinstance(item, str):
                yield item
            else:
                for row in item:
                    yield from row
    return '\n'.join(walk(blocks(doc)))


# ---------------- 任务里点名的三类丢失 ----------------

def test_paragraph_holding_only_a_block_formula_is_kept(api, tmp_path):
    """`$$…$$` 从行首写起就单独成段：<p> 和 </p> 之间全是标签，以前整段被当成标签扔掉"""
    paras = paragraphs(api, tmp_path, '<p>%s</p>\n<p>后面</p>' % katex(r'\frac{a}{\sqrt{b}}', display=True))
    assert [p.text for p in paras] == ['$$\\frac{a}{\\sqrt{b}}$$', '后面']
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    assert paras[0].alignment == WD_ALIGN_PARAGRAPH.CENTER, '块级公式和页面上一样居中'


@pytest.mark.parametrize('html, expected', [
    ('<ul>\n<li>行内 %s</li>\n<li>第二项</li>\n</ul>\n' % katex('x'), ['•\t行内 $x$', '•\t第二项']),
    ('<ul>\n<li><strong>粗体</strong></li>\n<li>第三项</li>\n</ul>\n', ['•\t粗体', '•\t第三项']),
    ('<ul><li>斜 <em>体</em></li><li>红 <span style="color: red">字</span></li><li>末项</li></ul>',
     ['•\t斜 体', '•\t红 字', '•\t末项']),
    ('<ol>\n<li>一</li>\n<li>二</li>\n</ol>\n', ['1.\t一', '2.\t二']),
], ids=['after-formula', 'after-bold', 'after-em-and-span', 'ordered'])
def test_list_items_after_a_formatted_ending_are_kept(api, tmp_path, html, expected):
    """某一项以加粗、公式、span 结尾时，`</li>` 开头的片段以前被整段跳过，后面的项一起没了"""
    assert body(api, tmp_path, html) == expected


def test_table_becomes_a_word_table(api, tmp_path):
    """<table>/<th>/<td> 以前原样写进正文；现在是 Word 表格，表头加粗，对齐跟着走"""
    html = ('<table>\n<thead>\n<tr>\n<th>甲</th>\n<th style="text-align: center">乙</th>\n</tr>\n</thead>\n'
            '<tbody>\n<tr>\n<td>1</td>\n<td style="text-align: center"><strong>2</strong></td>\n</tr>\n'
            '<tr>\n<td>%s</td>\n<td style="text-align: center">尾</td>\n</tr>\n</tbody>\n</table>\n'
            % katex('x^2'))
    doc = export(api, tmp_path, html)
    assert blocks(doc)[1:] == [[['甲', '乙'], ['1', '2'], ['$x^2$', '尾']]]
    table = doc.tables[0]
    assert table.style.name == 'Table Grid'
    head = table.rows[0].cells
    assert all(r.bold for c in head for r in c.paragraphs[0].runs), '表头要加粗'
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    assert table.rows[2].cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert len(table.rows[2].cells[1].paragraphs) == 1, '格子自带的空段落要用上，不能多出一行'


@pytest.mark.parametrize('html, expected', [
    # 4 空格缩进的代码块：<pre> 和 </pre> 之间是 <code>…</code>，以前整块被当成标签扔掉
    ('<p>段落</p>\n<pre><code>缩进代码 1\n缩进代码 2\n</code></pre>\n<p>后面</p>\n',
     ['段落', '缩进代码 1\n缩进代码 2', '后面']),
    ('<pre class="hljs"><code><span class="hljs-keyword">def</span> <span class="hljs-title">f</span>'
     '(<span class="hljs-params">x</span>):\n    <span class="hljs-keyword">return</span> x  '
     '<span class="hljs-comment"># 注释</span>\n</code></pre>\n',
     ['def f(x):\n    return x  # 注释']),
    # Quill 2：每行一个 div，空行里只有占位的 <br>；语言下拉框不是正文
    ('<div class="ql-code-block-container" spellcheck="false"><select class="ql-ui" contenteditable="false">'
     '<option value="plain">Plain</option><option value="bash">Bash</option></select>'
     '<div class="ql-code-block" data-language="plain">code line 1</div>'
     '<div class="ql-code-block" data-language="plain"><br></div>'
     '<div class="ql-code-block" data-language="plain">    code line 3</div></div>',
     ['code line 1\n\n    code line 3']),
    # 紧跟 <pre> 的一个换行不是内容（HTML 的解析规则），浏览器里不显示
    ('<pre>\n首行\n第二行</pre>', ['首行\n第二行']),
], ids=['indented', 'fenced-highlighted', 'quill', 'leading-newline'])
def test_code_blocks_keep_lines_and_indentation(api, tmp_path, html, expected):
    doc = export(api, tmp_path, html)
    assert blocks(doc)[1:] == expected
    code = [p for p in doc.paragraphs if '\n' in p.text][0]
    assert {r.font.name for r in code.runs if r.text.strip()} == {'Consolas'}, '代码用等宽字体'


def test_no_markup_or_entities_leak_into_text(api, tmp_path):
    """不在以前切分正则里的标签都原样进了正文：<s>、<code>、<sub>、<a href>、<hr>、<details>、&nbsp;"""
    html = ('<p>普通 <em>斜</em> <strong>粗</strong> <s>删</s> <code>代码</code> <u>下划线</u> '
            'H<sub>2</sub>O x<sup>2</sup> <a href="https://example.com">链接</a> a &lt; b &amp; c</p>\n'
            '<p>见 <a data-alias="别名" data-heading="小节" data-wikilink="另一篇" class="md-wikilink">别名</a>'
            ' 和 <a data-md-attach="attachments/n1/%E6%8A%A5.pdf">报告.pdf</a></p>\n'
            '<hr>\n<details><summary>摘要</summary>\n<p>详情</p>\n</details>\n'
            '<p>a&nbsp;&nbsp;b</p>\n<h4>四级</h4><h5>五级</h5><h6>六级</h6>')
    doc = export(api, tmp_path, html)
    assert blocks(doc)[1:] == [
        '普通 斜 粗 删 代码 下划线 H2O x2 链接 a < b & c', '见 别名 和 报告.pdf', '',
        '摘要', '详情', 'a\xa0\xa0b', '四级', '五级', '六级']
    assert not LEAKS.search(all_text(doc)), all_text(doc)


def test_inline_formatting_follows_the_right_text(api, tmp_path):
    html = ('<p><strong>粗</strong><em>斜</em><u>线</u><s>删</s>H<sub>下</sub>x<sup>上</sup>'
            '<code>码</code><mark>亮</mark><span style="font-size: 24px;">大</span>'
            '<span style="color: rgb(230, 0, 0);">红</span><span style="font-weight: bold">样式粗</span>'
            '<a href="https://x">链</a>平</p>')
    runs = {r.text: r for r in paragraphs(api, tmp_path, html)[0].runs}
    assert runs['粗'].bold and not runs['斜'].bold and runs['斜'].italic and runs['线'].underline
    assert runs['删'].font.strike and runs['下'].font.subscript and runs['上'].font.superscript
    assert runs['码'].font.name == 'Consolas' and runs['亮'].font.highlight_color is not None
    assert runs['大'].font.size.pt == 18, '24px 按 0.75 折成 18pt，和以前一样'
    assert str(runs['红'].font.color.rgb) == 'E60000'
    assert runs['样式粗'].bold
    assert runs['链平'].font.size.pt == 11 and runs['链平'].bold is False, '其余文字照旧 11pt、不加粗'


def test_headings_use_heading_styles(api, tmp_path):
    """h4–h6 以前不分段，会和下一段连在一起；标题的字号交给标题样式，不再压成 11pt"""
    html = ''.join('<h%d id="md-h-%d">标题%d</h%d>\n' % (i, i, i, i) for i in range(1, 7)) + '<p>正文</p>'
    paras = paragraphs(api, tmp_path, html)
    assert [(p.style.name, p.text) for p in paras] == [
        ('Heading %d' % i, '标题%d' % i) for i in range(1, 7)] + [('Normal', '正文')]
    assert all(r.font.size is None for p in paras[:6] for r in p.runs)


# ---------------- 列表 ----------------

def test_nested_lists_markers_and_indentation(api, tmp_path):
    from docx.shared import Pt
    html = ('<ul>\n<li>外层\n<ul>\n<li>内层\n<ul>\n<li>第三层</li>\n</ul>\n</li>\n</ul>\n</li>\n'
            '<li>\n<p>松散项第一段</p>\n<p>松散项第二段</p>\n</li>\n</ul>\n'
            '<ol type="a" start="3">\n<li>丙</li>\n<li>丁</li>\n</ol>\n')
    paras = paragraphs(api, tmp_path, html)
    assert [p.text for p in paras] == ['•\t外层', '◦\t内层', '▪\t第三层', '•\t松散项第一段', '松散项第二段',
                                       'c.\t丙', 'd.\t丁']
    fmt = [(p.paragraph_format.left_indent, p.paragraph_format.first_line_indent) for p in paras]
    assert fmt[:5] == [(Pt(22), Pt(-22)), (Pt(44), Pt(-22)), (Pt(66), Pt(-22)),
                       (Pt(22), Pt(-22)), (Pt(22), None)], '符号悬挂在左边；同一项的后续段落和文字对齐'


def test_markers_of_items_without_their_own_text_are_kept(api, tmp_path):
    """外层项一个字没有就开了内层列表、或者整项是空的：页面上照样有符号"""
    html = '<ul><li><ul><li>内层</li></ul></li><li></li><li>末项</li></ul>'
    assert body(api, tmp_path, html) == ['•\t', '◦\t内层', '•\t', '•\t末项']


def test_task_lists(api, tmp_path):
    html = ('<ul>\n<li class="md-task"><span aria-hidden="true" class="md-task-box"></span>待办未做</li>\n'
            '<li class="md-task md-task-done"><span aria-hidden="true" class="md-task-box"></span>'
            '待办已做 <strong>粗</strong></li>\n</ul>\n')
    paras = paragraphs(api, tmp_path, html)
    assert [p.text for p in paras] == ['☐\t待办未做', '☑\t待办已做 粗']
    done = [r for r in paras[1].runs if r.text.strip() and r.text != '☑\t']
    assert all(r.font.strike and str(r.font.color.rgb) == '999999' for r in done), '做完的划掉、变灰'
    assert not any(r.font.strike for r in paras[0].runs)


# Quill 2 的列表是一个扁平的 <ol>：类型在 data-list 上，层级在 ql-indent-N 上
def quill_li(text, kind='ordered', indent=0):
    cls = ' class="ql-indent-%d"' % indent if indent else ''
    return '<li data-list="%s"%s><span class="ql-ui" contenteditable="false"></span>%s</li>' % (kind, cls, text)


def test_quill_lists_number_like_the_editor(api, tmp_path):
    """编号规则照 quill.snow.css 的计数器：同一层接着编（中间夹着无序项、待办、引用都不断），
    出现更浅一层的项时更深层重新编，段落和标题让所有层重新编，各层依次 1. a. i."""
    html = ('<ol>' + quill_li('第一步') + quill_li('子步骤甲', indent=1) + quill_li('子步骤乙', indent=1)
            + quill_li('插一项', 'bullet') + quill_li('第二步') + quill_li('子步骤丙', indent=1)
            + quill_li('待办未做', 'unchecked') + quill_li('待办已做', 'checked') + '</ol>'
            + '<blockquote>引用</blockquote><ol>' + quill_li('引用后接着编') + '</ol>'
            + '<p>段落</p><ol>' + quill_li('重新开始') + quill_li('第三层', indent=2) + '</ol>')
    paras = paragraphs(api, tmp_path, html)
    assert [p.text for p in paras] == [
        '1.\t第一步', 'a.\t子步骤甲', 'b.\t子步骤乙', '•\t插一项', '2.\t第二步', 'a.\t子步骤丙',
        '☐\t待办未做', '☑\t待办已做', '引用', '3.\t引用后接着编', '段落', '1.\t重新开始', 'i.\t第三层']
    from docx.shared import Pt
    assert paras[1].paragraph_format.left_indent == Pt(44), 'ql-indent-1 比第一层多缩一级'
    assert all(r.font.strike for r in paras[7].runs if r.text == '待办已做')


def test_markers_already_written_as_text_are_not_doubled(api, tmp_path):
    """导出前页面自己把符号写成文字、去掉列表样式（list-style-type: none）时，不再画第二个"""
    html = ('<ol><li data-list="ordered" style="list-style-type: none;">1. 第一步</li>'
            '<li data-list="checked" style="list-style-type: none; text-decoration: line-through;">☑ 做完</li></ol>'
            '<ul><li class="md-task" style="list-style-type: none;">☐ 待办</li></ul>')
    assert body(api, tmp_path, html) == ['1. 第一步', '☑ 做完', '☐ 待办']


# ---------------- 公式 ----------------

@pytest.mark.parametrize('html, expected', [
    ('<p>开头 %s 尾巴</p>' % katex(r'\sqrt{x}+1'), ['开头 $\\sqrt{x}+1$ 尾巴']),
    ('<p>%s 尾巴<strong>粗</strong></p>' % katex('y'), ['$y$ 尾巴粗']),
    ('<p>段尾 %s</p>' % katex('a<b', svg=False), ['段尾 $a<b$']),
    # 块级公式写在行中（`文字 $$x$$ 文字`）：和页面一样单独一段
    ('<p>前 %s 后</p>' % katex(r'\int_0^1 f', display=True), ['前', '$$\\int_0^1 f$$', '后']),
    ('<ul>\n<li>项 %s</li>\n<li>下一项</li>\n</ul>\n' % katex('z', display=True),
     ['•\t项', '$$z$$', '•\t下一项']),
    ('<p>%s</p>' % katex('\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n', display=True),
     ['$$\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}$$']),
    # 富文本的公式：span.math-inline[data-latex] 里套着 KaTeX 的输出，两边是 Quill 的占位字符
    ('<p>开头 <span class="math-inline" data-latex="\\sqrt{x}+1" data-display="inline" contenteditable="false">'
     '\ufeff<span contenteditable="false">%s</span>\ufeff</span> 尾巴</p>' % katex('ignored'),
     ['开头 $\\sqrt{x}+1$ 尾巴']),
    ('<p><span class="math-inline math-block" data-latex="\\frac{a}{b}" data-display="block">'
     '\ufeff<span contenteditable="false">%s</span>\ufeff</span></p>' % katex('ignored', display=True),
     ['$$\\frac{a}{b}$$']),
    ('<p><span class="katex-error" title="ParseError" style="color:#cc0000">\\frac{a</span> 后</p>',
     ['$\\frac{a$ 后']),
    # 用户自己写的 MathML（没有 TeX 源码）和 <span class="katex">：取看得见的字，不加 $
    ('<p><math><mi>m</mi><mo>+</mo><mn>1</mn></math> 后</p>', ['m+1 后']),
    ('<p><span class="katex">普通字</span></p>', ['普通字']),
], ids=['inline', 'formula-first', 'escaped-tex', 'display-mid-paragraph', 'display-in-list', 'multiline',
        'quill-inline', 'quill-block', 'katex-error', 'raw-mathml', 'fake-katex'])
def test_formulas_are_written_as_tex(api, tmp_path, html, expected):
    """KaTeX 排版用的那份拆不出能读的字（这里故意写成分母 b 在前），MathML 是给读屏的另一份；
    都不写进 DOCX，写 TeX 源码"""
    assert body(api, tmp_path, html) == expected


# ---------------- 富文本的块 ----------------

def test_rich_text_blocks(api, tmp_path):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    html = ('<h1>标题一</h1><p class="ql-align-center">居中</p><p class="ql-indent-2">缩进两级</p>'
            '<blockquote>引用文字</blockquote>'
            '<p><div class="attachment-card" data-original-name="报告.pdf" contenteditable="false">\ufeff'
            '<span contenteditable="false"><span class="attachment-icon"><svg width="28"><path d="M14 2H6"/>'
            '</svg></span><span class="attachment-info"><span class="attachment-name">报告.pdf</span>'
            '<span class="attachment-meta">2 KB</span></span><button class="attachment-remove" title="移除附件">'
            '<svg width="12"><line x1="18" y1="6"/></svg></button></span>\ufeff</div></p>'
            '<div class="divider-blot div-wave" data-divider-type="wave" contenteditable="false"></div>'
            '<p>贴纸<span class="sticker-blot" data-sticker-html="&lt;span&gt;★&lt;/span&gt;" '
            'style="display:inline;font-size:0;line-height:0;visibility:hidden;">\u200b</span>之后</p>'
            '<p><br></p><p>空行之后</p>')
    doc = export(api, tmp_path, html)
    paras = doc.paragraphs[1:]
    assert [p.text for p in paras] == ['标题一', '居中', '缩进两级', '引用文字', '[附件] 报告.pdf（2 KB）', '',
                                       '贴纸之后', '空行之后']
    assert paras[0].style.name == 'Heading 1'
    assert paras[1].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert paras[2].paragraph_format.left_indent == Pt(44)
    assert paras[3].paragraph_format.left_indent == Pt(22), '引用缩进一级'
    from docx.oxml.ns import qn
    assert paras[5]._p.pPr.find(qn('w:pBdr')) is not None, '分隔线是带下边框的空段落'
    assert '\ufeff' not in all_text(doc) and '\u200b' not in all_text(doc)


def test_editor_controls_and_placeholders_are_not_text(api, tmp_path):
    """下拉框、按钮是编辑器的控件；\\ufeff 是 Quill 嵌入物两边的占位字符，都不是正文"""
    html = ('<p>前<select class="ql-ui"><option>Plain</option></select><button title="移除">×</button>'
            '<span class="some-embed">﻿<span>嵌入</span>﻿</span>后</p>')
    assert body(api, tmp_path, html) == ['前嵌入后']


def test_merged_cells_keep_every_cell_text(api, tmp_path):
    html = ('<table><tr><td rowspan="2">甲</td><td colspan="2">乙丙</td></tr>'
            '<tr><td>丁</td><td>戊</td></tr><tr><td>己</td><td>庚</td><td>辛</td></tr></table>')
    doc = export(api, tmp_path, html)
    table = doc.tables[0]
    assert (len(table.rows), len(table.columns)) == (3, 3)
    assert [[c.text for c in row.cells] for row in table.rows] == [
        ['甲', '乙丙', '乙丙'], ['甲', '丁', '戊'], ['己', '庚', '辛']]


def test_tables_wider_than_word_allows_keep_every_cell(api, tmp_path):
    """Word 一张表最多 63 列：再往右的格子并进最后一列，字不丢"""
    html = '<table><tr>%s</tr></table>' % ''.join('<td>格%d</td>' % i for i in range(1, 66))
    table = export(api, tmp_path, html).tables[0]
    assert len(table.columns) == 63
    assert [p.text for p in table.rows[0].cells[62].paragraphs] == ['格63', '格64', '格65']


def test_out_of_range_sizes_and_indents_are_clamped(api, tmp_path):
    """字号超出 Word 的范围写不进去；缩进太深字会被挤出版心"""
    from docx.shared import Pt
    html = '<p><span style="font-size: 99999px">巨</span></p>' + '<ul><li>' * 20 + '深' + '</li></ul>' * 20
    paras = paragraphs(api, tmp_path, html)
    assert paras[0].runs[0].font.size.pt == 1638
    assert paras[-1].text == '▪\t深' and paras[-1].paragraph_format.left_indent == Pt(22 * 12)


def test_front_matter_table(api, tmp_path):
    html = ('<table class="md-props"><tbody><tr><th>title</th><td>标题</td></tr>'
            '<tr><th>tags</th><td>a、b</td></tr></tbody></table>\n<p>正文</p>')
    assert body(api, tmp_path, html) == [[['title', '标题'], ['tags', 'a、b']], '正文']


# ---------------- 图片 ----------------

def test_images_stay_inline_and_are_not_blown_up(api, tmp_path):
    """图片留在原来那一段里；小图不再一律拉到 5 英寸宽，大图最宽 5 英寸"""
    from docx.shared import Inches
    src = 'data:image/png;base64,' + PNG_1PX
    html = ('<p>前<img src="%s" alt="小图">后</p><p><img src="%s" width="2000"></p>' % (src, src))
    doc = export(api, tmp_path, html)
    assert [p.text for p in doc.paragraphs[1:]] == ['前后', '']
    small, big = doc.inline_shapes
    assert small.width < Inches(1)
    assert big.width == Inches(5) and big.height == Inches(5), '按宽度等比缩到 5 英寸'


def test_local_image_file_is_embedded(api, tmp_path):
    folder = tmp_path / '带 空格'
    folder.mkdir()
    (folder / 'x.png').write_bytes(base64.b64decode(PNG_1PX))
    from urllib.parse import quote
    url = 'file:///' + quote(str(folder / 'x.png').replace('\\', '/'))
    assert len(export(api, tmp_path, '<p><img src="%s"></p>' % url).inline_shapes) == 1


@pytest.mark.parametrize('img, expected', [
    ('<img alt="图注" data-md-src="attachments/n1/x.png">', '[图片: 图注]'),        # Markdown 里找不到的附件
    ('<img src="data:image/png;base64,@@@" alt="坏图">', '[图片: 坏图]'),
    ('<img src="data:image/svg+xml;base64,%s" alt="矢量图">' % base64.b64encode(b'<svg/>').decode(),
     '[图片: 矢量图]'),
    ('<img src="https://example.com/a/pic.png">', '[图片: pic.png]'),
    ('<img src="file:///Z:/no/such/dir/x.png">', '[图片: x.png]'),
], ids=['missing-attachment', 'bad-base64', 'svg', 'remote', 'missing-file'])
def test_images_that_cannot_be_embedded_leave_a_note(api, tmp_path, img, expected):
    """单张图失败不该毁掉整篇导出，也不该一声不响地没了"""
    assert body(api, tmp_path, '<p>前 %s 后</p>' % img) == ['前 %s 后' % expected]


# ---------------- 空白与健壮性 ----------------

@pytest.mark.parametrize('html, expected', [
    ('<p>\n  a\n  <b>b</b>  c \n</p>', ['a b c']),
    ('<p>第一行<br>\n第二行<br>\n第三行</p>', ['第一行\n第二行\n第三行']),
    ('<p>段尾换行<br></p><p><br></p><p>下一段</p>', ['段尾换行', '下一段']),
    ('<p>中文\n换行</p>', ['中文 换行']),
], ids=['collapse', 'hard-breaks', 'trailing-br', 'soft-break'])
def test_whitespace_collapses_like_the_page(api, tmp_path, html, expected):
    assert body(api, tmp_path, html) == expected


def test_malformed_and_deeply_nested_html_still_exports(api, tmp_path):
    deep = '<div>' * 3000 + '深处' + '</div>' * 3000      # 一层一层递归下去的话早就爆栈了
    html = '<p>没闭合<b>粗</div></li>后面' + deep + '<ul><li>最后'
    text = all_text(export(api, tmp_path, html))
    for piece in ('没闭合', '粗', '后面', '深处', '最后'):
        assert piece in text


def test_characters_xml_does_not_allow_do_not_break_export(api, tmp_path):
    """混进一个控制字符，python-docx 就整篇写不出来"""
    doc = export(api, tmp_path, '<p>a\x00b\x0bc\ud800d</p>', title='标\x01题')
    assert blocks(doc) == ['标题', 'abcd']


def test_failed_export_says_why(api, tmp_path):
    assert api.export_note('标题', '<p>x</p>', 'docx', str(tmp_path)) == {     # 路径是个目录
        'ok': False, 'error': '文件可能正在别的程序里打开，或者这个位置不允许写入'}


# ---------------- e2e：富文本的真实 innerHTML ----------------

def _item(text, kind, indent=0):
    attrs = {'list': kind, **({'indent': indent} if indent else {})}
    return [{'insert': text}, {'insert': '\n', 'attributes': attrs}]


RICH_NOTE = {'ops': [
    {'insert': '标题一'}, {'insert': '\n', 'attributes': {'header': 1}},
    {'insert': '粗', 'attributes': {'bold': True}}, {'insert': '删', 'attributes': {'strike': True}},
    {'insert': '链', 'attributes': {'link': 'https://x.y'}}, {'insert': '平\n'},
    *_item('第一步', 'ordered'), *_item('子步骤甲', 'ordered', 1), *_item('插一项', 'bullet'),
    *_item('第二步', 'ordered'), *_item('待办未做', 'unchecked'), *_item('待办已做', 'checked'),
    {'insert': '引用文字'}, {'insert': '\n', 'attributes': {'blockquote': True}},
    {'insert': '段落\n'},
    *_item('重新开始', 'ordered'),
    {'insert': 'code line 1'}, {'insert': '\n', 'attributes': {'code-block': 'plain'}},
    {'insert': '    code line 2'}, {'insert': '\n', 'attributes': {'code-block': 'plain'}},
    {'insert': '开头 '}, {'insert': {'math-formula': {'latex': '\\sqrt{x}+1', 'display': 'inline'}}},
    {'insert': ' 尾巴\n'},
    {'insert': {'math-formula': {'latex': '\\frac{a}{b}', 'display': 'block'}}}, {'insert': '\n'},
    {'insert': '甲格'}, {'insert': '\n', 'attributes': {'table': 'row-1'}},
    {'insert': '乙格'}, {'insert': '\n', 'attributes': {'table': 'row-1'}},
    {'insert': {'attachment': {'id': 'a1', 'filename': 'x.pdf', 'originalName': '报告.pdf', 'fileSize': 2048,
                               'mimeType': 'application/pdf', 'storedPath': ''}}},
    {'insert': '\n'},
    {'insert': {'divider': {'type': 'wave'}}},
    {'insert': '分割线后\n'},
]}


@pytest.mark.e2e
def test_rich_text_note_exports_every_block(tmp_path, monkeypatch):
    """富文本笔记的 HTML 来自 quill.root.innerHTML：扁平的列表、一行一个 div 的代码块、
    带控件的附件卡片和公式都是编辑器自己的结构，构造的 HTML 难免和它有出入，这里用真的"""
    from test_markdown_editor import _run
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    note_id = make_delta_note(backend, title='富文本', content=json.dumps(RICH_NOTE, ensure_ascii=False))
    path = str(tmp_path / 'rich.docx')

    def actions(window, result):
        item = "document.querySelector('#note-list .note-item[data-note-id=\"%s\"]')" % note_id
        wait_for_js(window, "!!" + item, True, timeout=40)
        window.evaluate_js(item + ".click()")
        wait_for_js(window, "!!(window.__app && window.__app.state.activeNoteId === %s"
                            " && window.__app.state.noteFormat === 'delta' && !window.__app.state.isLoading)"
                    % json.dumps(note_id), True, timeout=40)
        wait_for_js(window, "document.querySelectorAll('.ql-editor .katex').length", 2)
        html = window.evaluate_js("window.__app.state.quill.root.innerHTML")
        result['html'] = html
        result['ok'] = backend.api.export_note('富文本', html, 'docx', path)

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['ok'] == {'ok': True}
    from docx import Document
    got = [b.replace('\t', ' ') if isinstance(b, str) else b for b in blocks(Document(path))[1:]]
    assert got == [
        '标题一', '粗删链平', '1. 第一步', 'a. 子步骤甲', '• 插一项', '2. 第二步', '☐ 待办未做', '☑ 待办已做',
        '引用文字', '段落', '1. 重新开始', 'code line 1\n    code line 2', '开头 $\\sqrt{x}+1$ 尾巴',
        '$$\\frac{a}{b}$$', [['甲格', '乙格']], '[附件] 报告.pdf（2 KB）', '', '分割线后'], got
    assert os.path.getsize(path) > 0
