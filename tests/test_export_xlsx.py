# -*- coding: utf-8 -*-
"""导出 XLSX / TXT：直接喂构造的 HTML（结构照抄两条路的真实输出），打开文件看格子和文字。

以前的 XLSX 按正则找不带属性的 `<table>` / `<tr>` / `<td>`：富文本表格（`<td data-row>`）一格也认不到，
导出的表是空的；带对齐的 Markdown 表格只剩没对齐的那一列；只要有表格，表格以外的内容整个丢掉。
没有表格时把标签一删：公式写成三份字连在一起，`&lt;` 原样留着，`=` 开头的行被 openpyxl 写成公式；
笔记标题里有 `/`、`:` 时整个导不出来（工作表名不许有这些字）。TXT 也是删标签。
现在两者都用 docx_export.py 的解析树按行写（xlsx_export.py）。实机回归在 test_export_formats.py。
"""
import pytest
from test_export_docx import PNG_1PX, katex, quill_li


def sheet(api, tmp_path, html, title='标题'):
    from openpyxl import load_workbook
    path = str(tmp_path / 'out.xlsx')
    assert api.export_note(title, html, 'xlsx', path) is True
    return load_workbook(path, rich_text=True).active


def text_of(value):
    from openpyxl.cell.rich_text import CellRichText
    if isinstance(value, CellRichText):
        return ''.join(getattr(part, 'text', part) for part in value)
    return value


def cells(ws):
    """有内容的格子：[(坐标, 文字)]，按行从上到下"""
    return [(c.coordinate, text_of(c.value)) for row in ws.iter_rows() for c in row
            if c.value is not None and text_of(c.value) != '']


def txt(api, tmp_path, html):
    path = str(tmp_path / 'out.txt')
    assert api.export_note('标题', html, 'txt', path) is True
    with open(path, encoding='utf-8') as f:
        return f.read()


QUILL_TABLE = ('<p>表格前</p><table><tbody><tr><td data-row="row-1">甲</td><td data-row="row-1">乙</td></tr>'
               '<tr><td data-row="row-2">丙</td><td data-row="row-2">丁</td></tr></tbody></table><p>表格后</p>')
MD_TABLE = ('<table>\n<thead>\n<tr>\n<th style="text-align: center">列一</th>\n<th>列二</th>\n</tr>\n</thead>\n'
            '<tbody>\n<tr>\n<td style="text-align: center">1</td>\n<td><strong>2</strong></td>\n</tr>\n</tbody>\n'
            '</table>\n<p>表格外</p>\n')


# ---------------- 表格 ----------------

def test_rich_text_table_keeps_every_cell_and_the_text_around_it(api, tmp_path):
    """富文本表格的格子带 data-row 属性：以前一格也认不到，表格前后的段落也一起丢了"""
    ws = sheet(api, tmp_path, QUILL_TABLE)
    assert cells(ws) == [('A1', '表格前'), ('A3', '甲'), ('B3', '乙'), ('A4', '丙'), ('B4', '丁'), ('A6', '表格后')]
    assert all(ws[c].border.left.style == 'thin' for c in ('A3', 'B3', 'A4', 'B4'))
    assert ws['A3'].fill.fill_type is None, '富文本表格没有表头'


def test_markdown_table_keeps_aligned_columns_and_its_header(api, tmp_path):
    """带对齐的列，`<th style="text-align: center">` 以前认不到：只剩另一列，还挪到了 A 列"""
    ws = sheet(api, tmp_path, MD_TABLE)
    assert cells(ws) == [('A1', '列一'), ('B1', '列二'), ('A2', '1'), ('B2', '2'), ('A4', '表格外')]
    assert ws['A1'].font.b and ws['B1'].font.b and ws['A1'].fill.fgColor.rgb.endswith('E8EFFF')
    assert ws['A1'].alignment.horizontal == 'center' and ws['A2'].alignment.horizontal == 'center'
    assert ws['B2'].font.b, '格子里的加粗要留着'
    assert ws['A2'].fill.fill_type is None


def test_merged_cells_are_merged(api, tmp_path):
    html = ('<table><tr><td rowspan="2">甲</td><td colspan="2">乙丙</td></tr>'
            '<tr><td>丁</td><td>戊</td></tr></table>')
    ws = sheet(api, tmp_path, html)
    assert sorted(str(r) for r in ws.merged_cells.ranges) == ['A1:A2', 'B1:C1']
    assert cells(ws) == [('A1', '甲'), ('B1', '乙丙'), ('B2', '丁'), ('C2', '戊')]


def test_cell_with_several_paragraphs_keeps_them_on_lines(api, tmp_path):
    ws = sheet(api, tmp_path, '<table><tr><td><p>第一段</p><p>第二段</p></td></tr></table>')
    assert ws['A1'].value == '第一段\n第二段' and ws['A1'].alignment.wrap_text


# ---------------- 文字 ----------------

def test_formula_like_text_stays_text(api, tmp_path):
    """`=` 开头的字以前被写成公式；表格里的会多出一个 `'`（防注入的写法被原样写了进去）"""
    ws = sheet(api, tmp_path, '<p>=1+1</p><p>=HYPERLINK("http://x","点")</p>'
                              '<table><tr><td>=SUM(A1)</td><td>+cmd</td></tr></table>')
    assert cells(ws) == [('A1', '=1+1'), ('A2', '=HYPERLINK("http://x","点")'), ('A4', '=SUM(A1)'), ('B4', '+cmd')]
    assert {ws[c].data_type for c in ('A1', 'A2', 'A4', 'B4')} == {'s'}


def test_formulas_are_written_as_tex_once(api, tmp_path):
    """以前 MathML、TeX 源码、排版用的字三份连在一起（`x\\sqrt{x}x+1`）"""
    html = ('<p>开头 %s 尾巴</p><p>%s</p><table><tr><td>%s</td></tr></table>'
            % (katex(r'\sqrt{x}+1'), katex(r'\frac{a}{b}', display=True), katex('y_1')))
    ws = sheet(api, tmp_path, html)
    assert cells(ws) == [('A1', '开头 $\\sqrt{x}+1$ 尾巴'), ('A2', '$$\\frac{a}{b}$$'), ('A4', '$y_1$')]


def test_entities_controls_attachments_and_images(api, tmp_path):
    html = ('<p>a &lt; b &amp;&amp; c&nbsp;d</p>'
            '<div class="ql-code-block-container"><select class="ql-ui"><option>Plain</option></select>'
            '<div class="ql-code-block">x = 1</div></div>'
            '<p><div class="attachment-card" data-original-name="报告.pdf"><span class="attachment-name">报告.pdf</span>'
            '<span class="attachment-meta">2 KB</span><button class="attachment-remove">×</button></div></p>'
            '<p>前<img alt="图注" data-md-src="attachments/n1/x.png">后<img src="data:image/png;base64,%s"></p>'
            % PNG_1PX)
    ws = sheet(api, tmp_path, html)
    assert cells(ws) == [('A1', 'a < b && c\xa0d'), ('A2', 'x = 1'), ('A3', '[附件] 报告.pdf（2 KB）'),
                         ('A4', '前[图片: 图注]后[图片]')]


def test_images_are_not_read_from_disk(api, tmp_path, monkeypatch):
    """表格和纯文本放不下图，也就不该去读图片文件：地址写成网络共享（`file:///\\\\主机\\共享\\x.png`）时，
    读一下就会连过去。名字只取文件名，不把整条路径写进去"""
    import backend.docx_export as docx_export
    opened = []
    monkeypatch.setattr(docx_export, 'open', lambda path, *a, **k: opened.append(path), raising=False)
    html = ('<p><img src="file:///\\\\attacker\\share\\x.png"> 和 <img src="file:///C:/no/such/y.png" alt="图注">'
            ' 和 <img src="https://example.com/a/%E5%9B%BE.png?v=1"></p>')
    assert cells(sheet(api, tmp_path, html)) == [('A1', '[图片: x.png] 和 [图片: 图注] 和 [图片: 图.png]')]
    assert txt(api, tmp_path, html) == '[图片: x.png] 和 [图片: 图注] 和 [图片: 图.png]'
    assert opened == [], '导出 XLSX / TXT 去读了图片文件：%r' % opened


def test_lists_have_markers_and_indentation(api, tmp_path):
    """Markdown 的列表由导出补上符号；富文本的符号页面已经写成文字了（list-style-type: none），不能再画一个"""
    html = ('<ul>\n<li>外层\n<ul>\n<li>内层</li>\n</ul>\n</li>\n</ul>\n<ol start="3">\n<li>丙</li>\n</ol>\n'
            '<ul><li class="md-task md-task-done" style="list-style-type: none;">☑ 已做</li></ul>'
            '<ol>' + quill_li('1. 第一步').replace('data-list="ordered"', 'data-list="ordered" style="list-style-type: none;"')
            + '</ol>')
    ws = sheet(api, tmp_path, html)
    assert cells(ws) == [('A1', '• 外层'), ('A2', '◦ 内层'), ('A3', '3. 丙'), ('A4', '☑ 已做'), ('A5', '1. 第一步')]
    assert [ws['A%d' % i].alignment.indent for i in range(1, 6)] == [0, 1, 0, 0, 0]
    assert ws['A4'].font.strike, '做完的待办划掉'


def test_headings_code_and_rich_text(api, tmp_path):
    from openpyxl.cell.rich_text import CellRichText
    html = ('<h1>一级</h1><h3>三级 <em>斜</em></h3>'
            '<pre><code>def f(x):\n\n    return x\n</code></pre>'
            '<p><strong>粗</strong>平<span style="color: rgb(230, 0, 0);">红</span></p><p><strong>整段粗体</strong></p>')
    ws = sheet(api, tmp_path, html)
    assert cells(ws) == [('A1', '一级'), ('A2', '三级 斜'), ('A3', 'def f(x):'), ('A5', '    return x'),
                         ('A6', '粗平红'), ('A7', '整段粗体')]
    assert ws['A1'].font.b and ws['A1'].font.sz == 16
    assert isinstance(ws['A2'].value, CellRichText), '标题里还有斜体：几种格式混在一起写富文本'
    assert ws['A3'].font.name == 'Consolas' and ws['A5'].font.name == 'Consolas'
    assert ws['A4'].value is None, '代码块里的空行留着'
    rich = ws['A6'].value
    assert isinstance(rich, CellRichText)
    fonts = {part.text: part.font for part in rich if hasattr(part, 'font')}
    assert fonts['粗'].b and str(fonts['红'].color.rgb).endswith('E60000')
    assert ws['A7'].font.b and not isinstance(ws['A7'].value, CellRichText), '只有一种格式就整格设字体'


# ---------------- 工作表名、坏字符、空内容 ----------------

@pytest.mark.parametrize('title, expected', [
    ('会议: 周一', '会议_ 周一'), ('2026/11/17', '2026_11_17'), ('a[b]*?\\c', 'a_b____c'),
    ("'引号'", '引号'), ('长' * 40, '长' * 31), ('', '笔记'), ('History', 'History_'), ('标\x01题', '标题'),
], ids=['colon', 'slash', 'brackets', 'quotes', 'long', 'empty', 'reserved', 'control'])
def test_sheet_title_is_made_valid(api, tmp_path, title, expected):
    """工作表名不许有 \\ / * ? : [ ]：以前标题里有这些字，导出直接失败"""
    assert sheet(api, tmp_path, '<p>正文</p>', title=title).title == expected


def test_bad_characters_and_huge_cells_do_not_break_export(api, tmp_path):
    big = '长' * 40000
    ws = sheet(api, tmp_path, '<p>a\x00b\x0bc\ud800d</p><p>%s</p>' % big)
    assert ws['A1'].value == 'abcd'
    assert len(ws['A2'].value) == 32767 and ws['A2'].value.endswith('…'), 'Excel 一格最多 32767 个字'


def test_empty_note(api, tmp_path):
    assert cells(sheet(api, tmp_path, '')) == []
    assert txt(api, tmp_path, '') == ''


def test_failed_export_returns_false(api, tmp_path):
    assert api.export_note('标题', '<p>x</p>', 'xlsx', str(tmp_path)) is False     # 路径是个目录
    assert api.export_note('标题', '<p>x</p>', 'txt', str(tmp_path)) is False


# ---------------- TXT ----------------

def test_txt_writes_blocks_lists_tables_and_formulas(api, tmp_path):
    """块和块之间空一行，列表项、表格的行挨着写，表格的格子用制表符隔开；标题不再和下一段连成一行"""
    html = ('<h2>标题二</h2>\n<p>段落 a &lt; b <strong>粗</strong></p>\n'
            '<ul>\n<li>外层\n<ul>\n<li>内层</li>\n</ul>\n</li>\n</ul>\n'
            '<table>\n<thead>\n<tr>\n<th>列一</th>\n<th>列二</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>甲</td>\n'
            '<td>乙</td>\n</tr>\n</tbody>\n</table>\n<pre><code>def f(x):\n    return x\n</code></pre>\n'
            '<p>开头 %s 尾巴</p>' % katex(r'\sqrt{x}+1'))
    assert txt(api, tmp_path, html) == (
        '标题二\n\n段落 a < b 粗\n\n• 外层\n  ◦ 内层\n\n列一\t列二\n甲\t乙\n\n'
        'def f(x):\n    return x\n\n开头 $\\sqrt{x}+1$ 尾巴')


def test_txt_of_rich_text_lines_and_lists(api, tmp_path):
    """富文本一行一个 <p>：和以前一样隔一行空行；列表（同一个 <ol>）挨着写，层级用缩进"""
    html = ('<p>第一行</p><p>第二行</p><ol>'
            + quill_li('1. 第一步').replace('data-list="ordered"', 'data-list="ordered" style="list-style-type: none;"')
            + quill_li('a. 子步骤', indent=1).replace('data-list="ordered"',
                                                     'data-list="ordered" style="list-style-type: none;"')
            + '</ol>')
    assert txt(api, tmp_path, html) == '第一行\n\n第二行\n\n1. 第一步\n  a. 子步骤'
