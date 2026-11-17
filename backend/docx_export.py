# -*- coding: utf-8 -*-
"""导出用的 HTML → DOCX。

输入是前端交给 `export_note(..., 'docx', ...)` 的那份 HTML：Markdown 笔记来自
`14-markdown-render.js` 的 `markdownExportHtml`，富文本笔记来自 `quill.root.innerHTML`。

以前按正则把 HTML 切成块，再看一块"是不是以 < 开头、以 > 结尾"猜它是不是标签。
丢内容的有三种：整块都是标签的正文（只含一个公式的段落、整段粗体）被当成标签扔掉；
`</li>` 开头的片段被当成标签扔掉，后面几项跟着没了；不在切分正则里的标签
（`<li>`、`<table>`、`<s>`、`<a href>`……）原样写进正文。现在先用标准库的 HTMLParser
解析成一棵小树，再照着树写段落：标签只当结构，文字只当文字，不用再猜。

写法上和页面看到的对齐：

* 块级元素（段落、标题、列表项、引用、代码块……）各成一段，空白按 HTML 的规则折叠；
* 列表符号写成文字放在项首、悬挂缩进。不用 Word 的自动编号：同一个编号定义会跨列表连号，
  每个列表重新编号要各建一份定义，不值得；
* 表格是 Word 表格；代码块保留换行和缩进，用等宽字体；
* 公式写 TeX 源码（行内 `$…$`，块级 `$$…$$` 单独一段居中）。KaTeX 排版用的 HTML 拆不出
  能读的文字：分母排在分子前面，根号、长箭头是 SVG；MathML 那份 Word 又不认。源码不丢信息，
  Word 的公式编辑器也认 LaTeX，选中后能转成公式。

只依赖标准库和 python-docx，不碰数据库（和 text.py 一样是依赖图上的叶子）。
python-docx 在函数里才导入：它连带 lxml，模块级导入会拖慢程序启动。
"""
import base64
import binascii
import io
import os
import re
import urllib.parse
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import NamedTuple

import applog

# ---------------- 解析：HTML → 小树 ----------------

# 没有结束标签的元素（HTML 规范的 void elements）
_VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
                   'param', 'source', 'track', 'wbr'})


class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent', '_css')

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.children = []           # str（文字）或 _Node
        self.parent = parent
        self._css = None

    def classes(self):
        return self.attrs.get('class', '').split()

    def css(self):
        """style 属性 → {属性名: 值}（属性名小写）"""
        if self._css is None:
            self._css = {}
            for decl in self.attrs.get('style', '').split(';'):
                name, sep, value = decl.partition(':')
                if sep and name.strip():
                    self._css[name.strip().lower()] = value.strip()
        return self._css


class _TreeBuilder(HTMLParser):
    """只建树，不做 HTML5 的纠错。输入是浏览器序列化出来的 innerHTML，标签都是配对的；
    遇到没打开过的结束标签就忽略，没闭合的标签在文档结尾自然收尾，文字不会丢。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)       # &lt;、&nbsp; 这类实体在这里就还原
        self.root = _Node('#root', {}, None)
        self._cur = self.root

    def _node(self, tag, attrs):
        values = {}
        for name, value in attrs:
            values.setdefault(name, value or '')       # 重复的属性取第一个，和浏览器一样
        node = _Node(tag, values, self._cur)
        self._cur.children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in _VOID:
            self._cur = node

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)                        # <br/>、<path/>：没有内容

    def handle_endtag(self, tag):
        node = self._cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self._cur = node.parent

    def handle_data(self, data):
        self._cur.children.append(data)


def parse_html(html):
    builder = _TreeBuilder()
    builder.feed(html or '')
    builder.close()
    return builder.root


def _iter_nodes(node):
    """node 和它的所有后代元素（先序，不递归：嵌套再深也不会爆栈）"""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed([c for c in cur.children if isinstance(c, _Node)]))


def _all_text(node, skip=lambda n: False):
    """后代文字连起来；skip(n) 为真的子树不算"""
    out = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif cur is node or not skip(cur):
            stack.extend(reversed(cur.children))
    return ''.join(out)


# ---------------- 分类：哪些是块、哪些不是正文 ----------------

# 不是正文：脚本样式、图标（SVG）、表单控件（Quill 代码块的语言下拉框、附件卡片的删除按钮）
_SKIP = frozenset({'script', 'style', 'template', 'svg', 'select', 'option', 'button', 'input',
                   'textarea', 'head', 'title', 'meta', 'link', 'base', 'noscript', 'iframe', 'object',
                   'embed', 'audio', 'video', 'canvas', 'map', 'colgroup', 'col', 'datalist'})

# 各成一段的元素（列表、表格、代码块、标题另有专门的写法）
_BLOCKS = frozenset({'p', 'div', 'blockquote', 'li', 'dl', 'dt', 'dd', 'details', 'summary',
                     'section', 'article', 'header', 'footer', 'nav', 'aside', 'main', 'figure',
                     'figcaption', 'address', 'center', 'fieldset', 'legend', 'form', 'html', 'body',
                     'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption',
                     'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})

_HEADINGS = {'h%d' % i: i for i in range(1, 7)}
_ALIGNS = {'center': 'center', 'right': 'right', 'end': 'right', 'justify': 'justify'}
_MAX_DEPTH = 150          # 再深的嵌套只取文字：不靠递归走到底，防爆栈
_INDENT_PT = 22           # 每级缩进两个汉字宽（11pt 正文）
_MAX_INDENT = 12          # 缩进最多 12 级（约 3.7 英寸），再深字就被挤出版心了
_MAX_COLS = 63            # Word 一张表最多 63 列
_BASE_PT = 11             # 正文字号，和以前一样显式写进每个 run
_MAX_IMAGE_IN = 5         # 图片最宽 5 英寸（以前一律拉到 5 英寸，小图会被放大）
_TEX = 'application/x-tex'


def _hidden(node):
    if 'hidden' in node.attrs:
        return True
    css = node.css()
    # 贴纸在正文里只是个藏起来的占位（visibility:hidden），图案画在编辑器的浮层上
    return (css.get('display', '').lower() == 'none'
            or css.get('visibility', '').lower() in ('hidden', 'collapse'))


def _list_style_none(node):
    css = node.css()
    value = css.get('list-style-type', css.get('list-style', '')).lower()
    return 'none' in value.split()


def _align(node):
    css_align = node.css().get('text-align', '').lower()
    if css_align in _ALIGNS:
        return _ALIGNS[css_align]
    attr = node.attrs.get('align', '').lower()
    if attr in _ALIGNS:
        return _ALIGNS[attr]
    for cls in node.classes():                         # Quill：ql-align-center / right / justify
        if cls.startswith('ql-align-') and cls[9:] in _ALIGNS:
            return _ALIGNS[cls[9:]]
    return None


def _ql_indent(node):
    for cls in node.classes():
        m = re.fullmatch(r'ql-indent-(\d+)', cls)
        if m:
            return min(int(m.group(1)), 8)
    return 0


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# ---------------- 公式 ----------------

def _annotation_tex(node):
    """KaTeX 输出里给读屏软件的 MathML 带着原始 TeX（<annotation encoding="application/x-tex">）"""
    for n in _iter_nodes(node):
        if n.tag == 'annotation' and n.attrs.get('encoding', '').lower() == _TEX:
            return _all_text(n)
    return None


def _formula(node):
    """公式的根节点 → (文字, 是否块级, 是否 TeX)；不是公式返回 None。

    外层先认：富文本的公式是 span.math-inline[data-latex] 里套着一份 KaTeX 输出，
    Markdown 的块级公式是 .katex-display 里套着 .katex。
    """
    cls = node.classes()
    if 'data-latex' in node.attrs and ('math-inline' in cls or 'math-block' in cls):
        display = node.attrs.get('data-display') == 'block' or 'math-block' in cls
        return node.attrs['data-latex'], display, True
    if 'katex-display' in cls or 'katex' in cls:
        display = 'katex-display' in cls
        tex = _annotation_tex(node)
        if tex is not None:
            return tex, display, True
        # 没有源码（用户自己写的 <span class="katex">）：取看得见的那一份，MathML 不再写一遍
        text = _all_text(node, lambda n: 'katex-mathml' in n.classes() or n.tag == 'svg')
        return text, display, False
    if 'katex-error' in cls:                           # KaTeX 解析失败：里面就是源码
        return _all_text(node), False, True
    if node.tag == 'math':                             # 用户直接写的 MathML
        tex = _annotation_tex(node)
        display = node.attrs.get('display') == 'block'
        if tex is not None:
            return tex, display, True
        return _all_text(node, lambda n: n.tag == 'annotation'), display, False
    return None


# ---------------- 列表符号 ----------------

_ROMAN = ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'), (90, 'xc'),
          (50, 'l'), (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i'))
_TYPE_ATTR = {'1': 'decimal', 'a': 'lower-alpha', 'A': 'upper-alpha', 'i': 'lower-roman', 'I': 'upper-roman'}
_BULLETS = {'disc': '•', 'circle': '◦', 'square': '▪'}
_QUILL_LEVEL_STYLES = ('decimal', 'lower-alpha', 'lower-roman')     # quill.snow.css：各层依次轮换
_QUILL_SYMBOLS = {'bullet': '•', 'checked': '☑', 'unchecked': '☐'}


def _number(n, kind):
    if kind in ('lower-alpha', 'upper-alpha', 'lower-latin', 'upper-latin') and n > 0:
        out = ''
        while n > 0:
            n, rem = divmod(n - 1, 26)
            out = chr(97 + rem) + out
        return out.upper() if kind.startswith('upper') else out
    if kind in ('lower-roman', 'upper-roman') and 0 < n < 4000:
        out = ''
        for value, digits in _ROMAN:
            while n >= value:
                out += digits
                n -= value
        return out.upper() if kind == 'upper-roman' else out
    return str(n)


def _list_kind(node):
    """list-style-type（内联样式优先，其次 type 属性）；没写返回 None"""
    css = node.css()
    for value in (css.get('list-style-type', ''), css.get('list-style', '')):
        for word in value.lower().split():
            if word in _BULLETS or word in _TYPE_ATTR.values() or word in ('lower-latin', 'upper-latin'):
                return word
    t = node.attrs.get('type', '')
    return _TYPE_ATTR.get(t) or (t.lower() if t.lower() in _BULLETS else None)


# ---------------- 写 DOCX ----------------

@dataclass(frozen=True)
class _Style:
    """行内格式（一段文字的 run 属性）"""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    sub: bool = False
    sup: bool = False
    code: bool = False
    highlight: bool = False
    size: float | None = None          # pt
    color: str | None = None           # 'RRGGBB'


@dataclass(frozen=True)
class _Block:
    """段落属性（一段属于哪种块）"""
    heading: int = 0                   # 1–6
    indent: int = 0                    # 缩进级数：列表、引用、Quill 的 ql-indent
    align: str | None = None
    pre: bool = False                  # 代码块：空白和换行原样保留
    list_level: int = 0                # 套在几层 ul/ol 里（决定圆点的样式）


class _Seg(NamedTuple):
    text: str
    style: _Style
    pre: bool


class _Image(NamedTuple):
    blob: bytes | None
    width_px: float | None
    label: str


_BREAK = object()                      # <br>
_WS = re.compile(r'[ \t\n\r\f]+')      # HTML 会折叠的空白（不含 &nbsp; 和全角空格）
# XML 1.0 里不允许的字符：混进一个，python-docx 就整篇写不出来（lxml 报 ValueError）
_XML_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')


def _xml_safe(text):
    return _XML_BAD.sub('', text)
_NAMED_COLORS = {
    'black': '000000', 'white': 'FFFFFF', 'red': 'FF0000', 'green': '008000', 'blue': '0000FF',
    'yellow': 'FFFF00', 'orange': 'FFA500', 'purple': '800080', 'gray': '808080', 'grey': '808080',
    'silver': 'C0C0C0', 'maroon': '800000', 'olive': '808000', 'lime': '00FF00', 'aqua': '00FFFF',
    'teal': '008080', 'navy': '000080', 'fuchsia': 'FF00FF', 'pink': 'FFC0CB', 'brown': 'A52A2A',
}


def _color(value):
    value = value.strip().lower()
    if value in _NAMED_COLORS:
        return _NAMED_COLORS[value]
    m = re.fullmatch(r'#([0-9a-f]{3}|[0-9a-f]{6})', value)
    if m:
        hexa = m.group(1)
        return (''.join(c * 2 for c in hexa) if len(hexa) == 3 else hexa).upper()
    m = re.fullmatch(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)', value)
    if m:
        if m.group(4) is not None and float(m.group(4)) == 0:
            return None                                # 全透明：等于没写
        return '%02X%02X%02X' % tuple(min(int(m.group(i)), 255) for i in (1, 2, 3))
    return None


def _font_size(value, current):
    """CSS 字号 → pt。px 按 0.75 折算（和以前一样）；em / % 相对当前字号"""
    m = re.fullmatch(r'\s*([\d.]+)\s*(px|pt|em|rem|%)\s*', value.lower())
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit == 'px':
        return n * 0.75
    if unit == 'pt':
        return n
    if unit == 'rem':
        return n * 12                                  # 根字号 16px
    return (current or _BASE_PT) * (n / 100 if unit == '%' else n)


def _inline_style(node, style):
    tag = node.tag
    if tag in ('b', 'strong', 'th'):
        style = replace(style, bold=True)
    elif tag in ('i', 'em', 'cite', 'var', 'dfn'):
        style = replace(style, italic=True)
    elif tag in ('u', 'ins'):
        style = replace(style, underline=True)
    elif tag in ('s', 'del', 'strike'):
        style = replace(style, strike=True)
    elif tag == 'sub':
        style = replace(style, sub=True, sup=False)
    elif tag == 'sup':
        style = replace(style, sup=True, sub=False)
    elif tag in ('code', 'kbd', 'samp', 'tt'):
        style = replace(style, code=True)
    elif tag == 'mark':
        style = replace(style, highlight=True)
    css = node.css()
    if not css:
        return style
    weight = css.get('font-weight', '').lower()
    if weight in ('bold', 'bolder') or (weight.isdigit() and int(weight) >= 600):
        style = replace(style, bold=True)
    elif weight in ('normal', 'lighter') or (weight.isdigit() and int(weight) < 600):
        style = replace(style, bold=False)
    font_style = css.get('font-style', '').lower()
    if font_style in ('italic', 'oblique'):
        style = replace(style, italic=True)
    elif font_style == 'normal':
        style = replace(style, italic=False)
    deco = (css.get('text-decoration', '') + ' ' + css.get('text-decoration-line', '')).lower()
    if 'underline' in deco:
        style = replace(style, underline=True)
    if 'line-through' in deco:
        style = replace(style, strike=True)
    if 'font-size' in css:
        size = _font_size(css['font-size'], style.size)
        if size:                                       # Word 的字号范围是 1–1638 磅，按半磅取整
            style = replace(style, size=min(max(round(size * 2) / 2, 1), 1638))
    if 'color' in css:
        color = _color(css['color'])
        if color:
            style = replace(style, color=color)
    valign = css.get('vertical-align', '').lower()
    if valign == 'sub':
        style = replace(style, sub=True, sup=False)
    elif valign in ('super', 'sup'):
        style = replace(style, sup=True, sub=False)
    return style


def _normalize(items):
    """按 HTML 的规则处理空白：连续空白折成一个空格，段首段尾、换行前后的空白不显示。
    代码块里的空白原样保留。相邻同格式的文字并成一段。"""
    out = []
    space = True                       # 前面是不是空白（段首、换行之后都算）

    def rstrip():
        while out and isinstance(out[-1], _Seg) and not out[-1].pre and out[-1].text.endswith(' '):
            text = out[-1].text.rstrip(' ')
            if text:
                out[-1] = out[-1]._replace(text=text)
                break
            out.pop()

    for item in items:
        if item is _BREAK:
            rstrip()
            out.append(_BREAK)
            space = True
            continue
        if isinstance(item, _Image):
            out.append(item)
            space = False
            continue
        text = item.text
        if not item.pre:
            text = _WS.sub(' ', text)
            if space and text.startswith(' '):
                text = text[1:]
            if not text:
                continue
            space = text.endswith(' ')
        elif not text:
            continue
        else:
            space = False
        last = out[-1] if out else None
        if isinstance(last, _Seg) and last.style == item.style and last.pre == item.pre:
            out[-1] = last._replace(text=last.text + text)
        else:
            out.append(_Seg(text, item.style, item.pre))
    rstrip()
    if out and out[-1] is _BREAK:      # 段尾的一个 <br> 在页面上不占一行
        out.pop()
        rstrip()
    if out and isinstance(out[-1], _Seg) and out[-1].pre and out[-1].text.endswith('\n'):
        text = out[-1].text[:-1]       # 代码块末尾的换行同理（markdown-it 的 `…\n</code>`）
        if text:
            out[-1] = out[-1]._replace(text=text)
        else:
            out.pop()
    return out


class _Container:
    """段落写到哪：正文，或表格的某一格（新格子自带一个空段落，先用它）"""

    def __init__(self, obj, fresh):
        self.obj = obj
        self.fresh = fresh

    def paragraph(self):
        if self.fresh:
            self.fresh = False
            return self.obj.paragraphs[0]
        return self.obj.add_paragraph()

    def table(self, rows, cols):
        self.fresh = False
        return self.obj.add_table(rows, cols)


class _Writer:
    def __init__(self, doc):
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Emu, Inches, Pt, RGBColor
        self.Emu, self.Pt, self.RGBColor = Emu, Pt, RGBColor
        self.OxmlElement, self.qn = OxmlElement, qn
        self.yellow = WD_COLOR_INDEX.YELLOW
        self.jc = {'center': WD_ALIGN_PARAGRAPH.CENTER, 'right': WD_ALIGN_PARAGRAPH.RIGHT,
                   'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}
        self.max_image = Inches(_MAX_IMAGE_IN)
        self.doc = doc
        self.containers = [_Container(doc, fresh=False)]
        self.pending = []              # 正在攒的这一段：_Seg / _Image / _BREAK
        self.block = None              # 这一段属于哪个块（第一样东西进来时定下）
        self.marker = None             # (符号, 块)：留给列表项的第一段
        self.quill_counters = []       # 富文本列表各层的编号

    # ---- 入口与分派 ----

    def write(self, root):
        self._children(root, _Block(), _Style(), 0)
        self._flush()

    def _children(self, node, block, style, depth):
        for child in node.children:
            if isinstance(child, str):
                self._add(_Seg(child.replace('\ufeff', ''), style, block.pre), block)
            else:
                self._element(child, block, style, depth + 1)

    def _element(self, node, block, style, depth):
        tag = node.tag
        if tag in _SKIP or _hidden(node):
            return
        if depth > _MAX_DEPTH:
            self._add(_Seg(_all_text(node, lambda n: n.tag in _SKIP), style, block.pre), block)
            return
        formula = _formula(node)
        if formula:
            self._formula(*formula, block, style)
            return
        cls = node.classes()
        if tag == 'br':
            self._add(_BREAK, block)
        elif tag == 'img':
            self._add(self._image_item(node), block)
        elif tag == 'hr' or 'divider-blot' in cls:
            self._rule(block)
        elif tag == 'table':
            self._table(node, block, style, depth)
        elif tag in ('ul', 'ol'):
            self._list(node, block, style, depth)
        elif tag == 'pre' or 'ql-code-block-container' in cls:
            self._pre(node, block, style, depth)
        elif 'attachment-card' in cls:
            self._attachment(node, block, style)
        elif tag in _BLOCKS:
            self._flush()
            if node.parent is not None and node.parent.tag == '#root' and (tag == 'p' or tag in _HEADINGS):
                self.quill_counters = []       # quill.snow.css：段落和标题让列表重新编号
            inner = replace(block, heading=_HEADINGS.get(tag, block.heading),
                            indent=block.indent + (tag in ('blockquote', 'dd')) + _ql_indent(node),
                            align=_align(node) or block.align)
            self._children(node, inner, _inline_style(node, style), depth)
            self._flush()
        else:
            self._children(node, block, _inline_style(node, style), depth)

    def _add(self, item, block):
        if self.block is None:
            self.block = block
        self.pending.append(item)

    # ---- 各种块 ----

    def _formula(self, text, display, is_tex, block, style):
        if not display:
            text = _WS.sub(' ', text).strip()
            if text:
                self._add(_Seg('$%s$' % text if is_tex else text, style, False), block)
            return
        lines = [line.rstrip() for line in text.strip().split('\n')]
        text = '\n'.join(line for line in lines if line.strip())
        if not text:
            return
        self._flush()
        # 块级公式单独一段、居中，和页面一样。项首的公式不居中：符号是悬挂在左边的
        centered = block if self.marker is not None else replace(block, align='center')
        self._add(_Seg('$$%s$$' % text if is_tex else text, style, True), centered)
        self._flush()

    def _rule(self, block):
        """分隔线：一个只有下边框的空段落"""
        self._flush(force_marker=True)
        para = self.containers[-1].paragraph()
        self._format(para, block, hanging=False)
        border = self.OxmlElement('w:pBdr')
        bottom = self.OxmlElement('w:bottom')
        for name, value in (('val', 'single'), ('sz', '6'), ('space', '1'), ('color', 'auto')):
            bottom.set(self.qn('w:' + name), value)
        border.append(bottom)
        # 子元素顺序是 schema 定的（Word 对顺序较真）：pBdr 排在这些之前
        para._p.get_or_add_pPr().insert_element_before(border, *_PPR_AFTER_PBDR)

    def _attachment(self, node, block, style):
        """富文本的附件卡片：名字和大小是两个挨着的 span，直接取字会连成 `报告.pdf2 KB`"""
        name = meta = ''
        for n in _iter_nodes(node):
            if 'attachment-name' in n.classes():
                name = _all_text(n).strip()
            elif 'attachment-meta' in n.classes():
                meta = _all_text(n).strip()
        name = name or node.attrs.get('data-original-name', '') or '附件'
        self._flush()
        self._add(_Seg('[附件] %s%s' % (name, '（%s）' % meta if meta else ''), style, False), block)
        self._flush()

    def _pre(self, node, block, style, depth):
        self._flush()
        code_block = replace(block, pre=True)
        code_style = replace(_inline_style(node, style), code=True)
        if 'ql-code-block-container' in node.classes():
            # Quill 2 的代码块：每一行是一个 div.ql-code-block，空行里只有一个占位的 <br>
            first = True
            for child in node.children:
                if not isinstance(child, _Node) or 'ql-code-block' not in child.classes():
                    continue
                if not first:
                    self._add(_Seg('\n', code_style, True), code_block)
                first = False
                kids = [c for c in child.children if not (isinstance(c, str) and not c)]
                if not (len(kids) == 1 and isinstance(kids[0], _Node) and kids[0].tag == 'br'):
                    self._children(child, code_block, code_style, depth)
        else:
            kids = node.children
            if kids and isinstance(kids[0], str) and kids[0].startswith('\n'):
                kids = [kids[0][1:]] + kids[1:]        # <pre> 后紧跟的一个换行不算内容（HTML 解析规则）
            holder = _Node('#pre', {}, node.parent)
            holder.children = kids
            self._children(holder, code_block, code_style, depth)
        self._flush()

    def _list(self, node, block, style, depth):
        self._flush()
        ordered = node.tag == 'ol'
        level = block.list_level + 1
        item_block = replace(block, indent=block.indent + 1, list_level=level, heading=0)
        number = _int(node.attrs.get('start'), 1)
        style = _inline_style(node, style)
        for child in node.children:
            if isinstance(child, str):
                if child.strip():                          # ul 里直接写的字（不规范，但浏览器照样显示）
                    self._add(_Seg(child, style, False), item_block)
                continue
            if child.tag != 'li':
                self._element(child, item_block, style, depth + 1)    # <ul><li>…</li><ul>…</ul></ul>
                continue
            if 'value' in child.attrs:
                number = _int(child.attrs['value'], number)
            marker, extra_style = self._marker(child, node, ordered, number, level)
            li_block = item_block
            if 'data-list' in child.attrs:                 # Quill：层级写在 class 上，不靠嵌套
                li_block = replace(item_block, indent=item_block.indent + _ql_indent(child))
            self._list_item(child, li_block, replace(style, **extra_style), depth + 1, marker)
            if ordered and 'data-list' not in child.attrs:
                number += 1
        self._flush()

    def _marker(self, li, list_node, ordered, number, level):
        """列表项的符号（None 表示不画），以及这一项要额外加的格式。

        做完的待办和编辑器里一样划掉、变灰（预览的 .md-task-done、编辑器的 li[data-list=checked]）。
        """
        cls = li.classes()
        quill_kind = li.attrs.get('data-list')
        done = 'md-task-done' in cls or quill_kind == 'checked'
        extra = {'strike': True, 'color': '999999'} if done else {}
        if _list_style_none(li) or _list_style_none(list_node):
            return None, extra                             # 符号已经作为文字写在项里了，不再画一个
        if 'md-task' in cls:                               # Markdown 待办：勾选框是个空 span，靠样式画
            return ('☑' if done else '☐'), extra
        if quill_kind:
            return self._quill_marker(li, quill_kind), extra
        kind = _list_kind(li) or _list_kind(list_node)
        if kind in _BULLETS:
            return _BULLETS[kind], {}
        if ordered or kind:
            return _number(number, kind or 'decimal') + '.', {}
        return ('•', '◦', '▪')[min(level, 3) - 1], {}     # 浏览器的默认：每深一层换一种圆点

    def _quill_marker(self, li, kind):
        """Quill 2 的列表是一个扁平的 <ol>，编号由 quill.snow.css 的计数器画：
        任何一项都让更深的层重新编号，有序项给自己这一层加一；段落和标题让所有层重新编号
        （见 _element），中间夹着引用、代码块都不断。"""
        level = _ql_indent(li)
        del self.quill_counters[level + 1:]
        if kind != 'ordered':
            return _QUILL_SYMBOLS.get(kind, '•')
        while len(self.quill_counters) <= level:
            self.quill_counters.append(0)
        self.quill_counters[level] += 1
        return _number(self.quill_counters[level], _QUILL_LEVEL_STYLES[level % 3]) + '.'

    def _list_item(self, li, block, style, depth, marker):
        if self.marker is not None:                        # 外层项一个字没有就开了内层列表
            self._flush(force_marker=True)
        self._flush()
        self.marker = (marker, block) if marker else None
        inner = replace(block, align=_align(li) or block.align)
        self._children(li, inner, _inline_style(li, style), depth)
        self._flush(force_marker=True)                     # 空的列表项也有符号
        self.marker = None

    def _table(self, node, block, style, depth):
        self._flush(force_marker=True)
        rows, captions = _table_parts(node)
        for caption in captions:
            self._children(caption, replace(block, align='center'), style, depth)
            self._flush()
        cells, ncols = _layout(rows)
        if not cells:                          # 没有格子的"表格"：里面有字就当普通的块
            self._children(node, block, style, depth)
            self._flush()
            return
        table = self.containers[-1].table(len(rows), ncols)
        table.style = self.doc.styles['Table Grid']
        grid = table._cells                    # 一次取齐：table.cell() 每次都把整张表的格子重算一遍
        filled = set()
        for r, c, _rs, _cs, cell_node in cells:
            self.containers.append(_Container(grid[r * ncols + c], fresh=(r, c) not in filled))
            filled.add((r, c))
            cell_block = _Block(align=_align(cell_node))
            self._children(cell_node, cell_block, _inline_style(cell_node, style), depth)
            self._flush(force_marker=True)
            self.containers.pop()
        for r, c, rs, cs, _cell in cells:       # 内容写完再合并：合并会改动格子的排布
            if rs > 1 or cs > 1:
                table.cell(r, c).merge(table.cell(r + rs - 1, c + cs - 1))

    # ---- 段落输出 ----

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
        para = self.containers[-1].paragraph()
        self._format(para, block, hanging=marker is not None)
        if marker is not None:
            self._run(para, marker + '\t', _Style(), block)    # 符号不跟着这一项的格式走
        for item in items:
            if item is _BREAK:
                para.add_run().add_break()
            elif isinstance(item, _Image):
                self._picture(para, item, block)
            else:
                self._run(para, item.text, item.style, block)

    def _format(self, para, block, hanging):
        if block.heading:
            para.style = self.doc.styles['Heading %d' % block.heading]
        fmt = para.paragraph_format
        if block.indent:
            fmt.left_indent = self.Pt(_INDENT_PT * min(block.indent, _MAX_INDENT))
            if hanging:
                fmt.first_line_indent = self.Pt(-_INDENT_PT)   # 符号悬挂在左边，折行和文字对齐
        if block.align:
            fmt.alignment = self.jc[block.align]

    def _run(self, para, text, style, block):
        run = para.add_run(_xml_safe(text))    # python-docx 把 \n 写成换行、\t 写成制表符
        if block.heading:              # 标题的粗细字号归标题样式管，只写额外加的
            if style.bold:
                run.bold = True
            if style.italic:
                run.italic = True
            if style.underline:
                run.underline = True
        else:
            run.bold, run.italic, run.underline = style.bold, style.italic, style.underline
        font = run.font
        if style.strike:
            font.strike = True
        if style.sub:
            font.subscript = True
        elif style.sup:
            font.superscript = True
        if style.code:
            font.name = 'Consolas'
        if style.highlight:
            font.highlight_color = self.yellow
        if style.color:
            font.color.rgb = self.RGBColor.from_string(style.color)
        if style.size:
            font.size = self.Pt(style.size)
        elif not block.heading:
            font.size = self.Pt(_BASE_PT)
        return run

    # ---- 图片 ----

    def _image_item(self, node):
        src = node.attrs.get('src', '').strip()
        alt = node.attrs.get('alt', '').strip()
        blob = None
        name = ''
        if src.startswith('data:'):
            header, _, data = src.partition(',')
            if ';base64' in header.lower():
                try:
                    blob = base64.b64decode(data)
                except (binascii.Error, ValueError) as exc:
                    applog.get_logger().warning('DOCX 导出内嵌图片失败（%s）：%s', src[:80], exc)
        elif src.startswith('file:///'):
            # Windows 下 file:///C:/... 要先 unquote 再剥掉根斜杠，否则带空格的路径打不开
            path = urllib.parse.unquote(src[len('file:///'):]).lstrip('/')
            name = os.path.basename(path)
            try:
                with open(path, 'rb') as f:
                    blob = f.read()
            except OSError as exc:
                applog.get_logger().warning('DOCX 导出读不到图片（%s）：%s', path, exc)
        elif src:
            name = urllib.parse.unquote(src.rsplit('/', 1)[-1].split('?')[0]) or src
        width = None
        for value in (node.attrs.get('data-w'), node.attrs.get('width'), node.css().get('width')):
            m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*', value or '')
            if m and float(m.group(1)) > 0:
                width = float(m.group(1))
                break
        return _Image(blob, width, alt or name or (src if src.startswith(('http:', 'https:')) else ''))

    def _picture(self, para, item, block):
        if item.blob:
            try:
                shape = para.add_run().add_picture(io.BytesIO(item.blob))
            except Exception as exc:                       # noqa: BLE001  格式不认识（SVG 之类）
                applog.get_logger().warning('DOCX 导出内嵌图片失败（%s）：%s', item.label[:80], exc)
            else:
                width = self.Emu(int(item.width_px * 9525)) if item.width_px else shape.width
                width = min(width, self.max_image)
                if shape.width and width != shape.width:
                    shape.height = self.Emu(int(shape.height * width / shape.width))
                    shape.width = width
                return
        # 放不进去的图（缺失、网上的、格式不认识）留一行字：单张图失败不该毁掉整篇导出
        self._run(para, '[图片: %s]' % (item.label or '无法读取'), _Style(), block)


# pPr 里排在 pBdr 后面的子元素（python-docx 的 CT_PPr 用完就删了这张表，只好抄一份）
_PPR_AFTER_PBDR = (
    'w:shd', 'w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap', 'w:overflowPunct',
    'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi', 'w:adjustRightInd', 'w:snapToGrid',
    'w:spacing', 'w:ind', 'w:contextualSpacing', 'w:mirrorIndents', 'w:suppressOverlap', 'w:jc',
    'w:textDirection', 'w:textAlignment', 'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle',
    'w:rPr', 'w:sectPr', 'w:pPrChange')


def _table_parts(node):
    """表格的行（直接写的、thead / tbody / tfoot 里的都算）和标题（caption）"""
    rows, captions = [], []
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        if child.tag == 'tr':
            rows.append(child)
        elif child.tag in ('thead', 'tbody', 'tfoot'):
            rows.extend(c for c in child.children if isinstance(c, _Node) and c.tag == 'tr')
        elif child.tag == 'caption':
            captions.append(child)
    return rows, captions


def _layout(rows, max_cols=_MAX_COLS):
    """表格的行 → [(行, 列, 跨行, 跨列, 格子节点)] 和列数。rowspan / colspan 按浏览器的规则排位。

    Word 一张表最多 63 列（max_cols 默认值）：再往右的格子并进最后一列（同一格可能出现多次），字不丢。
    """
    taken = set()
    cells = []
    ncols = 0
    for r, tr in enumerate(rows):
        c = 0
        for td in tr.children:
            if not isinstance(td, _Node) or td.tag not in ('td', 'th'):
                continue
            while (r, c) in taken:
                c += 1
            if c >= max_cols:
                cells.append((r, max_cols - 1, 1, 1, td))
                continue
            rs = min(max(_int(td.attrs.get('rowspan'), 1), 1), len(rows) - r)
            cs = min(max(_int(td.attrs.get('colspan'), 1), 1), max_cols - c)
            for dr in range(rs):
                for dc in range(cs):
                    taken.add((r + dr, c + dc))
            cells.append((r, c, rs, cs, td))
            c += cs
            ncols = max(ncols, c)
        while (r, c) in taken:
            c += 1
        ncols = max(ncols, c)
    return cells, min(ncols, max_cols)


def html_to_docx(title, html, save_path):
    """把导出 HTML 写成 .docx（标题居中在最前）"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    title_para = doc.add_heading(_xml_safe(title or ''), level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _Writer(doc).write(parse_html(html))
    doc.save(save_path)
