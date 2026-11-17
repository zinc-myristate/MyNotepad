# -*- coding: utf-8 -*-
"""导出用的 HTML → XLSX，以及同一套写法的纯文本（TXT）。

输入和 DOCX 一样，是前端交给 `export_note` 的那份 HTML。解析和遍历也用 docx_export.py 的那一套
（公式写 TeX 源码、列表符号、编辑器控件不算正文、实体还原、空白按页面的规则折叠……），
只是写出去的不是 Word 段落，而是"行"：

* XLSX：一段一行，都写在 A 列（`<br>` 和代码块按行拆开）；表格照原样占几行几列，表头加粗加底色，
  合并的格子照样合并；表格和前后的内容之间空一行。
* TXT：同样的行一行一行写；块和块之间空一行，列表项、表格的行挨着写，表格的格子用制表符隔开。

以前的 XLSX 按正则找 `<table>` / `<tr>` / `<td>`，只认不带属性的标签：富文本表格的 `<td data-row>`
一格也认不到，导出的表是空的；带对齐的 Markdown 表格只剩没对齐的那一列。只要有表格，表格以外的内容
整个丢掉。没有表格时就把标签一删了事：公式写成三份字连在一起（MathML、TeX 源码、排版用的字），
`&lt;` 原样留着。TXT 也是这样删标签的，标题还会和下一段连成一行。

格子一律按文字写（data_type 's'）：笔记里 `=1+1`、`=HYPERLINK(…)` 这样的行，openpyxl 默认会当成公式写进去，
以前防注入的写法是在前面加 `'`，openpyxl 会把这个 `'` 原样写进格子里。

只依赖标准库、openpyxl 和 docx_export.py（openpyxl 在函数里才导入），不碰数据库。
"""
import re
import unicodedata
import urllib.parse
from dataclasses import replace
from typing import NamedTuple

from .docx_export import (
    _BLOCKS,
    _BREAK,
    _align,
    _Block,
    _Image,
    _inline_style,
    _layout,
    _normalize,
    _Seg,
    _Style,
    _table_parts,
    _Writer,
    _xml_safe,
    parse_html,
)

_EXCEL_MAX_COLS = 16384
_EXCEL_MAX_CHARS = 32767          # 一格最多这么多字，再多 Excel 打开时会报文件有问题
_HEADING_PT = {1: 16, 2: 14, 3: 13, 4: 12, 5: 11, 6: 11}
_HEADER_FILL = 'E8EFFF'           # 表头底色，和以前一样
_TEXT_COLUMN_WIDTH = 80           # 没有表格时 A 列的宽度，和以前一样
_TABLE_MAX_WIDTH = 50
_SHEET_BAD = re.compile(r'[\\/*?:\[\]]')

# 根上的这些各算一块（TXT 在块和块之间空一行）；挨着的行内元素合起来是同一段
_BLOCK_TAGS = _BLOCKS | {'ul', 'ol', 'pre', 'table', 'hr'}
_BLOCK_CLASSES = ('ql-code-block-container', 'divider-blot', 'attachment-card', 'katex-display')


def _is_block(node):
    return node.tag in _BLOCK_TAGS or any(c in _BLOCK_CLASSES for c in node.classes())


class _Row(NamedTuple):
    """一段：lines 是几行片段（代码块、`<br>` 拆开的），marker 是列表符号（没有就是 None）"""
    lines: list
    block: _Block
    marker: str | None
    group: int


class _Table(NamedTuple):
    """一张表：cells 是 {(行, 列): {'rs', 'cs', 'lines', 'header', 'align'}}"""
    cells: dict
    nrows: int
    ncols: int
    group: int


class _RowWriter(_Writer):
    """遍历全用 _Writer 的规则（列表、公式、代码块、附件卡片、空白折叠……），只换掉"写出去"的那几步"""

    def __init__(self, max_cols=_EXCEL_MAX_COLS):
        # 不调 _Writer.__init__：那里要导入 python-docx，这里用不着
        self.pending = []
        self.block = None
        self.marker = None
        self.quill_counters = []
        self.records = []
        self.group = 0
        self.max_cols = max_cols

    def write(self, root):
        for child in root.children:
            if isinstance(child, str):
                self._add(_Seg(child.replace('﻿', ''), _Style(), False), _Block())
                continue
            block = _is_block(child)
            if block:
                self._flush()
                self.group += 1
            self._element(child, _Block(), _Style(), 1)
            if block:
                self._flush()
                self.group += 1
        self._flush()

    def _flush(self, force_marker=False):
        items = _normalize(self.pending)
        block = self.block
        self.pending, self.block = [], None
        marker = None
        if self.marker is not None and (items or force_marker):
            (marker, marker_block), self.marker = self.marker, None
            block = block or marker_block
        if not items and marker is None:
            return
        block = block or _Block()
        lines, line = [], []
        for item in items:
            if item is _BREAK:
                lines.append(line)
                line = []
            elif isinstance(item, _Image):
                line.append(_Seg('[图片: %s]' % item.label if item.label else '[图片]', _Style(), False))
            elif block.pre:
                # 代码块按行拆开；块级公式里的换行留在同一格里（它的块不是 pre，只是片段带着换行）
                for k, part in enumerate(item.text.split('\n')):
                    if k:
                        lines.append(line)
                        line = []
                    if part:
                        line.append(item._replace(text=part))
            else:
                line.append(item)
        lines.append(line)
        self.records.append(_Row(lines, block, marker, self.group))

    def _rule(self, block):
        """分隔线：表格和纯文本里没有对应的东西，只当一个段落边界"""
        self._flush(force_marker=True)

    def _image_item(self, node):
        """图片写成 `[图片: 名字]`。表格和纯文本放不下图，所以也不去读图片文件"""
        alt = node.attrs.get('alt', '').strip()
        src = node.attrs.get('src', '').strip() or node.attrs.get('data-md-src', '').strip()
        # 只要文件名：`/` 和 `\` 都是分隔符（`file:///\\主机\共享\x.png` 这种写法不能把整条路径带出来）
        name = '' if src.startswith('data:') else re.split(r'[\\/]', urllib.parse.unquote(src.split('?')[0]))[-1]
        return _Image(None, None, alt or name)

    def _table(self, node, block, style, depth):
        self._flush(force_marker=True)
        rows, captions = _table_parts(node)
        for caption in captions:
            self._children(caption, replace(block, align='center'), style, depth)
            self._flush()
        cells, ncols = _layout(rows, self.max_cols)
        if not cells:                      # 没有格子的"表格"：里面有字就当普通的块
            self._children(node, block, style, depth)
            self._flush()
            return
        grid = {}
        for r, c, rs, cs, td in cells:
            sub = _RowWriter(self.max_cols)
            sub._children(td, _Block(align=_align(td)), _inline_style(td, style), depth)
            sub._flush(force_marker=True)
            lines = _plain_lines(sub.records)
            if (r, c) in grid:             # 超出列数上限的格子并进最后一列：接在后面，字不丢
                grid[(r, c)]['lines'].extend(lines)
                continue
            tr = td.parent
            header = td.tag == 'th' or (tr is not None and tr.parent is not None and tr.parent.tag == 'thead')
            grid[(r, c)] = {'rs': rs, 'cs': cs, 'lines': lines, 'header': header, 'align': _align(td)}
        self.records.append(_Table(grid, len(rows), ncols, self.group))


# ---------------- 行 → 文字 ----------------

def _text(line):
    return ''.join(s.text for s in line)


def _indent(block):
    """缩进几级：列表项按套了几层算（最外一层不缩），引用、富文本的 ql-indent 照原样"""
    return max(block.indent - (1 if block.list_level else 0), 0)


def _row_lines(rec):
    """一段的几行：列表符号接在第一行前面；代码块以外的空行不要"""
    lines = [list(line) for line in rec.lines]
    if rec.marker:
        lines[0] = [_Seg(rec.marker + ' ', _Style(), False)] + lines[0]
    if not rec.block.pre:
        lines = [line for line in lines if _text(line).strip()]
    return lines


def _grid_text(table):
    """表格 → 每行每格的文字（合并的格子字在左上角那一格，被盖住的格子是空的）"""
    grid = [[''] * table.ncols for _ in range(table.nrows)]
    for (r, c), cell in table.cells.items():
        grid[r][c] = ' '.join(_text(line).strip() for line in cell['lines'] if _text(line).strip())
    return grid


def _plain_lines(records):
    """几条记录 → 几行片段（表格里套的表格写成一行一行的字，格子之间空两格）"""
    out = []
    for rec in records:
        if isinstance(rec, _Table):
            out.extend([_Seg('  '.join(row).strip(), _Style(), False)] for row in _grid_text(rec))
        else:
            out.extend(_row_lines(rec))
    return out


def html_to_text(html):
    """导出 HTML → 纯文本（TXT）"""
    writer = _RowWriter()
    writer.write(parse_html(html))
    out, last = [], None
    for rec in writer.records:
        if isinstance(rec, _Table):
            lines = ['\t'.join(row).rstrip('\t') for row in _grid_text(rec)]
        else:
            pad = '  ' * _indent(rec.block)
            lines = []
            for k, line in enumerate(_row_lines(rec)):
                lead = pad + ('  ' if k and rec.marker and not rec.block.pre else '')
                lines.append((lead + _text(line)).rstrip())
        if not lines:
            continue
        if last is not None and rec.group != last:
            out.append('')
        out.extend(lines)
        last = rec.group
    return _xml_safe('\n'.join(out).strip('\n'))


# ---------------- 行 → 工作表 ----------------

def _width(text):
    """显示宽度：中日韩的字按两个算"""
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _sheet_title(title):
    """笔记标题 → 工作表名。Excel 不许有 \\ / * ? : [ ]，不能以 ' 开头或结尾，最长 31 个字。
    以前直接拿标题用：标题里有 `/` 或 `:`（"会议: 周一"）就整个导不出来"""
    name = _SHEET_BAD.sub('_', _xml_safe(title or '')).strip().strip("'").strip()[:31].strip().strip("'")
    if name.lower() == 'history':          # Excel 留给自己用的名字
        name += '_'
    return name or '笔记'


def _font_kwargs(style):
    return {
        'name': 'Consolas' if style.code else None,
        'bold': style.bold, 'italic': style.italic,
        'underline': 'single' if style.underline else None, 'strike': style.strike,
        'color': ('FF' + style.color) if style.color else None,
        'size': style.size or 11,
        'vertAlign': 'superscript' if style.sup else ('subscript' if style.sub else None),
    }


def _inline_font_kwargs(style):
    kw = {'sz': style.size or 11}
    if style.code:
        kw['rFont'] = 'Consolas'
    if style.bold:
        kw['b'] = True
    if style.italic:
        kw['i'] = True
    if style.underline:
        kw['u'] = 'single'
    if style.strike:
        kw['strike'] = True
    if style.color:
        kw['color'] = 'FF' + style.color
    if style.sup:
        kw['vertAlign'] = 'superscript'
    elif style.sub:
        kw['vertAlign'] = 'subscript'
    return kw


def _put(cell, segs):
    """一格的内容：只有一种格式时整格设字体，几种格式混在一起时写富文本。一律按文字写，不当公式"""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Font
    segs = [s._replace(text=_xml_safe(s.text)) for s in segs]
    segs = [s for s in segs if s.text]
    if not segs:
        return
    styles = {s.style for s in segs if s.text.strip()}
    text = ''.join(s.text for s in segs)
    if len(styles) <= 1 or len(text) > _EXCEL_MAX_CHARS:
        style = next(iter(styles)) if len(styles) == 1 else _Style()
        if len(text) > _EXCEL_MAX_CHARS:
            text = text[:_EXCEL_MAX_CHARS - 1] + '…'
        cell.value = text
        if style != _Style():
            cell.font = Font(**_font_kwargs(style))
    else:
        cell.value = CellRichText([TextBlock(InlineFont(**_inline_font_kwargs(s.style)), s.text)
                                   if s.style != _Style() else s.text for s in segs])
    if cell.data_type == 'f':              # `=1+1` 这样的字照字写：不能在表格软件里变成公式
        cell.data_type = 's'


def _join_lines(lines):
    """几行片段 → 一格里的片段（行和行之间是换行）"""
    out = []
    for k, line in enumerate(lines):
        if k:
            out.append(_Seg('\n', _Style(), False))
        out.extend(line)
    return out


def html_to_xlsx(title, html, save_path):
    """把导出 HTML 写成 .xlsx：一张工作表，名字取笔记标题"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from openpyxl.utils import get_column_letter
    writer = _RowWriter()
    writer.write(parse_html(html))
    wb = Workbook()
    ws = wb.active
    ws.title = _sheet_title(title)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill(start_color=_HEADER_FILL, end_color=_HEADER_FILL, fill_type='solid')
    widths = {}
    row, last = 1, None
    for rec in writer.records:
        is_table = isinstance(rec, _Table)
        if last is not None and (is_table or last == 'table'):
            row += 1                                   # 表格和前后的内容之间空一行
        if is_table:
            for r in range(rec.nrows):
                for c in range(rec.ncols):
                    ws.cell(row=row + r, column=1 + c).border = border
            for (r, c), info in rec.cells.items():
                cell = ws.cell(row=row + r, column=1 + c)
                segs = _join_lines(info['lines'])
                if info['header']:
                    segs = [s._replace(style=replace(s.style, bold=True)) for s in segs]
                    cell.fill = fill
                _put(cell, segs)
                cell.alignment = Alignment(wrap_text=True, vertical='top', horizontal=info['align'])
                if info['rs'] > 1 or info['cs'] > 1:
                    ws.merge_cells(start_row=row + r, start_column=1 + c,
                                   end_row=row + r + info['rs'] - 1, end_column=c + info['cs'])
                elif info['lines']:
                    width = max(_width(_text(line)) for line in info['lines'])
                    widths[1 + c] = max(widths.get(1 + c, 0), width)
            row += rec.nrows
            last = 'table'
            continue
        heading = _HEADING_PT.get(rec.block.heading)
        indent = min(_indent(rec.block), 15)
        for line in _row_lines(rec):
            if heading:
                line = [s._replace(style=replace(s.style, bold=True, size=s.style.size or heading)) for s in line]
            cell = ws.cell(row=row, column=1)
            _put(cell, line)
            if rec.block.align or indent or '\n' in _text(line):
                cell.alignment = Alignment(horizontal=rec.block.align, indent=indent,
                                           wrap_text='\n' in _text(line))
            row += 1
        last = 'text'
    # 有表格时列宽按表格里的字算（段落在 A 列，比列宽长的部分会显示在右边的空格子里）；没表格时 A 列放宽
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 8), _TABLE_MAX_WIDTH)
    if not widths:
        ws.column_dimensions['A'].width = _TEXT_COLUMN_WIDTH
    wb.save(save_path)
