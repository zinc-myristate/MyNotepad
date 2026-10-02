# -*- coding: utf-8 -*-
"""文本与格式转换：Quill Delta ⟷ Markdown ⟷ 纯文本，外加属性 / 待办 / 双链的**行解析**。

这一层的共同点：**输入是正文文本，输出还是文本或纯数据，完全不碰数据库。**
所以它不需要 conn 参数、不依赖 backend 包的任何其它子模块 —— 是依赖图上的叶子，
拿真实数据就能单独测（`tests/test_format_convert.py` 已经在这么做）。

里面有几个不显眼但必须记住的口径：

* **同一个内容、两种格式算出的指标必须一致** —— `_todo_lines_md`（行扫描）与
  `_todo_lines_delta`（读 `list` 行属性）是同一件事的两个实现，由测试锁死一致性。
* **属性（front-matter）不算正文** —— `_markdown_to_text` 与状态栏镜像都要剥掉它，
  否则摘要与字数会把它算进去。
* **`derive_metrics` 是"可查询侧面"的唯一计算入口** —— 派生索引、待办面板、表格视图、
  字数统计全用它，改这里等于改所有下游。
* **`_filter_props` / `_load_props` 刻意不在这里**：名字像文本处理，实际是搜索范围语法
  （`prop:键=值`）的执行者、要查库，归 fts 域。
"""
import json
import re

# ⚠️ 必须是 `from datetime import datetime`（**类**），不是 `import datetime`（模块）：
# 这几个函数是从 backend/__init__.py 搬过来的，那里就是 `from datetime import datetime`，
# 函数体里写的是 `datetime.now()`。搬过来时按"名字出现过"生成 `import datetime`，
# 于是 `datetime.now()` 变成"模块没有 now 属性" —— 5 条模板测试当场红。
from datetime import datetime


def _md_inline_escape(text):
    """把纯文本里的 Markdown 元字符转义，避免正文里的 * 和 _ 被当成格式"""
    out = []
    for ch in text or '':
        if ch in '\\`*_{}[]()#+-.!|':
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


# 字体/字号/颜色在 Markdown 里靠**白名单内嵌 HTML** 保留（第 6 轮决策）：
# Obsidian/Typora 能渲染；Joplin 官方说明会丢 HTML —— 这是刻意接受的取舍（见 README「已知限制」）。
_MD_FONT_CLASSES = {'serif': 'md-font-serif', 'monospace': 'md-font-mono', 'cursive': 'md-font-hand'}
_MD_FONT_BY_CLASS = {v: k for k, v in _MD_FONT_CLASSES.items()}
_MD_COLOR_RE = re.compile(r'^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$')
_MD_SIZE_RE = re.compile(r'^\d{1,3}(px|em|rem|%)$')
_MD_SPAN_RE = re.compile(r'^<span([^>]*)>', re.I)
_MD_STYLE_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.I)
_MD_CLASS_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.I)
_MD_HR_RE = re.compile(r'^<hr[^>]*class\s*=\s*"([^"]*)"', re.I)
_MD_TAG_RE = re.compile(r'</?(?:u|sup|sub|mark|span|hr)\b[^>]*>', re.I)
_MD_ATX_IMAGE = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)$')
_MD_MATH_BLOCK_RE = re.compile(r'^\$\$(.+?)\$\$$')
_MD_MATH_INLINE_RE = re.compile(r'\$([^$\n]+?)\$')

# LaTeX 的**标准**定界符：`\(…\)` 行内、`\[…\]` 块级。
# 为什么必须支持（实测踩到）：这两者在原实现里既不是公式、也不是"普通文本"——
# `_md_parse_inline` 的转义分支会把反斜杠吃掉，于是 `\(C_0\)` 变成 `(C_0)`、
# `\(\alpha+\beta\)` 变成 `(alpha+beta)`：**公式被静默破坏**。
# 用户那篇 33683 字的《Lecture 2.2：相图与相变》里 `\(` 出现 165 次、`$` 出现 **0** 次
# —— 整篇的公式都是这么写的，所以在预览里全是原始 LaTeX 文本。
_MD_PAREN_INLINE_RE = re.compile(r'\\\((.+?)\\\)')
_MD_BRACKET_BLOCK_RE = re.compile(r'\\\[(.+?)\\\]')
_MD_FENCE_RE = re.compile(r'^\s*(```|~~~)')


def normalize_line_math(line):
    r"""把**一行**里的 `\(…\)` / `\[…\]` 归一成 `$…$` / `$$…$$`。

    ## 为什么要归一，而不是"各自支持一遍"

    同一段内容只能有**一种**写法，否则「预览」「md→delta」「delta→md 回转」「导出」
    四条路径各认一套，迟早对不上（本仓库对"第二真相源"的既有原则）。
    归一之后下游只认 `$`，一处解析、处处一致。

    ## 为什么是"行级"

    块级公式（`\[…\]` / `$$…$$`）在 Markdown 里**必须独占一行**才成立，
    所以归一化天然是逐行的事；行级还顺带让"跳过代码块"变得简单
    （调用方按围栏状态决定要不要调它，不必搞占位符那一套）。

    ⚠️ **跨行的 `\[` … `\]` 处理不了**，那是另一件事 —— 见 `collapse_multiline_blocks`。
    实测踩到：用户笔记里有 2 处写成

        \[
        C_0=35\text{ wt\% Ni},\qquad
        C_L=32\text{ wt\% Ni}
        \]

    这种写法在这条行级路径上会**剩下未转换**（迁移脚本的守恒校验当场抓到）。

    ⚠️ 正则必须**惰性**（`.+?`）：否则 `\(a\) 和 \(b\)` 会被当成一个公式。
    """
    if not line or '\\' not in line:
        return line
    line = _MD_BRACKET_BLOCK_RE.sub(
        lambda m: '$$%s$$' % m.group(1).strip(), line)
    return _MD_PAREN_INLINE_RE.sub(
        lambda m: '$%s$' % m.group(1).strip(), line)


def collapse_multiline_blocks(md_text):
    r"""把**跨行**的 `\[` … `\]` 折成单行 `$$…$$`。

    ## 为什么要单独一趟

    行级归一化（`normalize_line_math`）只能处理定界符在同一行的情况。
    而 LaTeX 文档里很常见的写法是定界符各自独占一行：

        \[
        C_0=35\text{ wt\% Ni},\qquad
        C_L=32\text{ wt\% Ni}
        \]

    这在本应用的解析链路上有**两个**必须满足的前提，所以只能折成单行：

    1. `_MD_MATH_BLOCK_RE` 要求 `$$…$$` 独占一行（`.+?` 不跨行）；
    2. 前端 `MATH_BLOCK`（`/\$\$([\s\S]+?)\$\$/`）虽然跨行，但把原来的换行直接塞进
       KaTeX 会让 `\qquad` 之类的排版命令落在一个奇怪的位置。

    折行时**行间内容用空格连接**（不是 `\n`）：既保持单行，又不会把两段数学粘成一个命令。
    行数因此会减少（每个跨行块少一行），这是预期行为 —— 迁移脚本的守恒校验要按
    "减少了几个块"来核对，不能要求行数不变。

    ⚠️ 必须在**跳过代码块**的前提下调用（调用方保证），否则代码里的 `\[` 会被动到。
    """
    lines = (md_text or '').split('\n')
    out, i = [], 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == '\\[':
            # 找配对的 \]
            j = i + 1
            body = []
            while j < len(lines) and lines[j].strip() != '\\]':
                body.append(lines[j].strip())
                j += 1
            if j < len(lines):
                # 有配对：折成一行
                out.append('$$%s$$' % ' '.join(p for p in body if p))
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _collapse_blocks_outside_code(md_text):
    r"""只对**围栏代码块之外**的段落做跨行块折叠。

    为什么要单独拆出这一段：`collapse_multiline_blocks` 按行找 `\[` / `\]`，
    代码块里恰好出现这两行的话会被它误当成公式。围栏状态只有调用方知道，
    所以把"跳过代码块"和"折叠"组合在这里，两个入口（md→delta、迁移脚本）共用。
    """
    lines = (md_text or '').split('\n')
    chunks, buf, in_fence = [], [], False
    for ln in lines:
        if _MD_FENCE_RE.match(ln):
            if in_fence:
                chunks.append('\n'.join(buf))            # 代码段：原样
            else:
                chunks.append(collapse_multiline_blocks('\n'.join(buf)))
            buf = []
            in_fence = not in_fence
        buf.append(ln)
    chunks.append('\n'.join(buf) if in_fence else collapse_multiline_blocks('\n'.join(buf)))
    return '\n'.join(c for c in chunks if c != '')


def _style_attrs(style_text):
    """白名单 style → Delta 属性（只认 color / font-size，其余一律忽略）"""
    out = {}
    for decl in (style_text or '').split(';'):
        if ':' not in decl:
            continue
        prop, _, val = decl.partition(':')
        prop, val = prop.strip().lower(), val.strip()
        if prop == 'color' and _MD_COLOR_RE.match(val):
            out['color'] = val
        elif prop == 'font-size' and _MD_SIZE_RE.match(val):
            out['size'] = val
    return out


def _delta_to_md_attrs(attrs):
    """(文本, 行内属性) → Markdown 行内文本；富文本属性用白名单 HTML 包住"""
    text, attrs = attrs
    s = _md_inline_escape(text)
    if attrs.get('code'):
        s = '`' + text + '`'
    else:
        if attrs.get('bold'):
            s = '**' + s + '**'
        if attrs.get('italic'):
            s = '*' + s + '*'
        if attrs.get('strike'):
            s = '~~' + s + '~~'
        if attrs.get('underline'):
            s = '<u>' + s + '</u>'
        script = attrs.get('script')
        if script in ('super', 'sub'):
            tag = 'sup' if script == 'super' else 'sub'
            s = '<%s>%s</%s>' % (tag, s, tag)
        font = attrs.get('myfont') or attrs.get('font')
        if font in _MD_FONT_CLASSES:
            s = '<span class="%s">%s</span>' % (_MD_FONT_CLASSES[font], s)
        styles = []
        color = attrs.get('color')
        if color and _MD_COLOR_RE.match(str(color)):
            styles.append('color: %s' % color)
        size = attrs.get('size')
        if size and _MD_SIZE_RE.match(str(size)):
            styles.append('font-size: %s' % size)
        if attrs.get('background'):
            styles.append('background-color: %s' % attrs['background'])
        if styles:
            s = '<span style="%s">%s</span>' % ('; '.join(styles), s)
    if attrs.get('link'):
        s = '[%s](%s)' % (s, attrs['link'])
    return s


def _md_inline(pieces):
    """(文本, 行内属性) 列表 → Markdown 行内文本"""
    return ''.join(_delta_to_md_attrs(p) for p in pieces)


def _embed_text_fallback(value, depth=0):
    """未知 embed 的兜底：递归把里面的字符串抽出来，宁可丢结构也不能丢字"""
    if depth > 4:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ' '.join(_embed_text_fallback(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return ' '.join(_embed_text_fallback(v, depth + 1) for v in value)
    return ''


def _md_embed(kind, value, note_id=None, resolve_attachment=None):
    """嵌入对象 → Markdown 片段（图片用占位，真正的路径由调用方替换）"""
    value = value if isinstance(value, dict) else {}
    if kind == 'image':
        name = value.get('filename') or value.get('storedPath') or ''
        return '\x00IMG:%s\x00' % name
    if kind == 'math-formula':
        latex = value.get('latex') or value.get('formula') or ''
        return '$$%s$$' % latex if value.get('display') else '$%s$' % latex
    if kind == 'attachment':
        name = value.get('filename') or ''
        label = value.get('originalName') or name or '附件'
        if resolve_attachment:
            href = resolve_attachment(name) or ''
        elif note_id and name:
            href = 'attachments/%s/%s' % (note_id, name)
        else:
            href = ''
        return '[📎 %s](%s)' % (label, href) if href else '📎 %s' % label
    if kind == 'divider':
        style = value.get('style') or value.get('type') or 1
        try:
            cls = 'divider-%d' % max(1, min(12, int(style)))
        except (TypeError, ValueError):
            cls = 'divider-1'
        # 不要再包换行：行本身由 Delta 的 \n op 负责，自己加换行会在往返时每次多一个空行
        return '<hr class="%s">' % cls
    if kind in ('sticker', 'stamp'):
        emoji = value.get('emoji') or value.get('text') or value.get('char') or '🔖'
        return '<span class="md-sticker">%s</span>' % emoji
    guess = _embed_text_fallback(value).strip()
    return guess


# Quill 的表格是一组**带行/单元格属性的文本行**：同一行的多行共享 table=<行id>，
# 单元格之间用 table-cell 区分。这里按行分组拼成 GFM 表格。
_MD_TABLE_ROW_ATTR = 'table'
_MD_TABLE_CELL_ATTR = 'table-cell'
_MD_TABLE_LINE_ATTR = 'table-cell-line'


def _rows_to_gfm_table(rows):
    """[[cell, cell, ...], ...] → GFM 表格文本（首行当表头）"""
    if not rows:
        return ''
    width = max(len(r) for r in rows)
    def fmt(cells):
        padded = list(cells) + [''] * (width - len(cells))
        return '| ' + ' | '.join(c.replace('|', '\\|').replace('\n', ' ') for c in padded) + ' |'
    out = [fmt(rows[0]), '| ' + ' | '.join(['---'] * width) + ' |']
    out += [fmt(r) for r in rows[1:]]
    return '\n'.join(out)


def _delta_to_markdown(content, resolve_image=None, note_id=None, resolve_attachment=None):
    """Quill Delta JSON → Markdown（标题/列表/待办/引用/代码块/表格 + 行内格式 + 嵌入对象）。

    Quill 的行属性挂在**含换行符的那个 op** 上，所以按 op 切行、用换行所在 op 的属性
    作为整行属性。图片需要调用方提供 resolve_image(文件名) → 相对路径（导出时会把图复制到
    同级 assets 目录），拿不到路径就退化成文件名文本。
    """
    if not content:
        return ''
    try:
        data = json.loads(content)
    except Exception:
        return content or ''
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return ''

    lines, cur = [], {'pieces': [], 'attrs': {}}
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins, attrs = op.get('insert'), op.get('attributes') or {}
        if isinstance(ins, dict):
            kind = next(iter(ins))
            cur['pieces'].append(
                (_md_embed(kind, ins[kind], note_id, resolve_attachment), {'raw': True}))
            continue
        if not isinstance(ins, str):
            continue
        parts = ins.split('\n')
        for i, part in enumerate(parts):
            if part:
                cur['pieces'].append((part, attrs))
            if i < len(parts) - 1:
                cur['attrs'] = attrs
                lines.append(cur)
                cur = {'pieces': [], 'attrs': {}}
    if cur['pieces']:
        lines.append(cur)

    out = []
    code_buf = []          # 连续的多行代码块要合并成一个 ``` 块
    table_rows, table_cell, table_cell_lines = [], [], []

    def _flush_code():
        if code_buf:
            out.append('```\n' + '\n'.join(code_buf) + '\n```')
            code_buf.clear()

    def _flush_cell():
        if table_cell_lines:
            table_cell.append('\n'.join(table_cell_lines))
            table_cell_lines.clear()

    def _flush_table():
        _flush_cell()
        if table_cell:
            table_rows.append(list(table_cell))
            table_cell.clear()
        if table_rows:
            out.append(_rows_to_gfm_table(table_rows))
            table_rows.clear()

    for line in lines:
        attrs = line['attrs'] or {}
        body = ''.join(t if a.get('raw') else _md_inline([(t, a)])
                       for t, a in line['pieces'])
        if attrs.get(_MD_TABLE_ROW_ATTR):
            _flush_code()
            _flush_cell()
            cell_id = attrs.get(_MD_TABLE_CELL_ATTR)
            if table_rows and table_cell and cell_id != getattr(_flush_table, '_last_cell', None):
                pass
            table_cell_lines.append(body)
            # 同一行内换单元格：table-cell 变化即分格
            prev = getattr(_flush_table, '_cell', None)
            if prev is None:
                _flush_table._cell = cell_id
            elif cell_id != prev:
                _flush_cell()
                _flush_table._cell = cell_id
            continue
        _flush_table()
        _flush_table._cell = None
        if attrs.get('code-block'):
            # Quill 把多行代码块编码成**一个** op（内部含换行）并带 code-block 属性，
            # 所以按行看会看到连续多行都带该属性——必须合并成一个围栏块。
            code_buf.append(''.join(t for t, _ in line['pieces']))
            continue
        _flush_code()
        prefix = ''
        header = attrs.get('header')
        lst = attrs.get('list')
        if header:
            # header 来自 Delta 行属性，可能是**任意类型/任意内容**（手写 JSON、外部导入的
            # 库、被改坏的正文都会出现奇怪的值）。直接 int() 有两个后果：
            #   ① 非数字抛 ValueError，而异常消息里会带上**这串原始内容** ——
            #      applog 会把 message 记进 error.log，等于把正文写进日志；
            #   ② 数字很大时 `'#' * huge` 直接分配巨量字符串。
            # 与同文件 `_md_embed` 里 `int(style)` 的口径对齐：try + 夹紧到 [1, 6]。
            try:
                level = max(1, min(6, int(header)))
            except (TypeError, ValueError):
                level = 1
            prefix = '#' * level + ' '
        elif lst == 'ordered':
            prefix = '1. '
        elif lst == 'bullet':
            prefix = '- '
        elif lst == 'checked':
            prefix = '- [x] '
        elif lst == 'unchecked':
            prefix = '- [ ] '
        elif attrs.get('blockquote'):
            prefix = '> '
        out.append(prefix + body)
    _flush_table()
    _flush_code()
    md = '\n'.join(out).rstrip() + '\n'
    if resolve_image:
        def _sub(m):
            path = resolve_image(m.group(1))
            return ('![](%s)' % path) if path else _md_inline_escape(m.group(1))
        md = re.sub('\x00IMG:(.*?)\x00', _sub, md)
    else:
        md = re.sub('\x00IMG:(.*?)\x00', lambda m: _md_inline_escape(m.group(1)), md)
    return md


_MD_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
_MD_UL = re.compile(r'^\s*[-*+]\s+(.*)$')
_MD_TODO = re.compile(r'^\s*[-*+]\s+\[([ xX])\]\s*(.*)$')
_MD_OL = re.compile(r'^\s*\d+[.)]\s+(.*)$')
_MD_QUOTE = re.compile(r'^\s*>\s?(.*)$')
_MD_FENCE = re.compile(r'^\s*```')
_MD_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$')


def _md_split_table_row(line):
    r"""按 `|` 切一个 GFM 表格行；`\|` 是**转义**的竖线，属于单元格内容。

    ⚠️ 原来直接 `split('|')` —— 于是单元格里含竖线的公式（`$|x|$`、`$\lvert x\rvert$`
    写不出来的那种）会把表格结构切坏，那个公式整个失效（实测：`$|x|$` 所在表格的
    公式数为 0）。GFM 的标准写法就是 `\|`，所以这里按标准来：
    切分时忽略 `\|`，切完再把 `\|` 还原成 `|`（单元格内容里就是普通竖线）。
    """
    # 先把转义竖线换成不会出现的哨兵，切完再换回来
    SENTINEL = '\x00PIPE\x00'
    safe = line.replace('\\|', SENTINEL)
    cells = safe.strip().strip('|').split('|')
    return [c.strip().replace(SENTINEL, '|') for c in cells]


def _md_merge(base, extra):
    """合并属性（extra 覆盖 base）；值一律是标量，便于直接当 Delta attributes 用"""
    out = dict(base)
    out.update(extra)
    return out


def _md_parse_inline(text, base=None, _depth=0):
    """Markdown 行内 → Delta ops。

    支持：**粗** / *斜* / ~~删~~ / `码` / [文字](链接) / $公式$，
    以及本应用导出的白名单内嵌 HTML（<u>/<sup>/<sub>/<mark>/<span style|class>）。

    属性用**扁平字典**在递归里传递（不是就地压栈）：早期版本把 `**粗**` 交给
    递归调用后只在自己这层压栈，结果子调用产生的 ops 完全没带上 bold ——
    「加粗转一圈就没了」，且往返测试才发现。现在把当前属性直接传给子调用。
    """
    if _depth > 8:
        return [{'insert': text}]
    ops, i, n = [], 0, len(text)
    base_attrs = dict(base or {})
    buf = []

    def flush(extra=None):
        if not buf:
            return
        attrs = _md_merge(base_attrs, extra or {})
        ops.append({'insert': ''.join(buf), 'attributes': attrs} if attrs
                   else {'insert': ''.join(buf)})
        buf.clear()

    while i < n:
        ch = text[i]
        if ch == '\\' and i + 1 < n:
            buf.append(text[i + 1]); i += 2; continue
        if ch == '$':
            m = _MD_MATH_INLINE_RE.match(text, i)
            if m and m.group(1).strip():
                flush()
                ops.append({'insert': {'math-formula': {'latex': m.group(1).strip(),
                                                        'display': False}}})
                i = m.end(); continue
        close = re.match(r'</(u|sup|sub|mark|span)>', text[i:], re.I)
        if close:
            # 闭合标签由递归层处理（子调用不会看到它），这里只是兜底：当普通文本
            buf.append(text[i:i + close.end()]); i += close.end(); continue
        if text[i:i + 3].lower() == '<u>':
            end = re.search(r'</u>', text[i:], re.I)
            if end:
                flush()
                ops.extend(_md_parse_inline(text[i + 3:i + end.start()],
                                            _md_merge(base_attrs, {'underline': True}), _depth + 1))
                i += end.end(); continue
        m = re.match(r'<(sup|sub)>(.*?)</\1>', text[i:], re.I | re.S)
        if m:
            flush()
            tag = m.group(1).lower()
            ops.extend(_md_parse_inline(m.group(2), _md_merge(
                base_attrs, {'script': 'super' if tag == 'sup' else 'sub'}), _depth + 1))
            i += m.end(); continue
        m = re.match(r'<mark>(.*?)</mark>', text[i:], re.I | re.S)
        if m:
            flush()
            ops.extend(_md_parse_inline(m.group(1),
                                        _md_merge(base_attrs, {'background': '#FFF3A3'}), _depth + 1))
            i += m.end(); continue
        m = _MD_SPAN_RE.match(text[i:])
        if m:
            attrs = {}
            style = _MD_STYLE_RE.search(m.group(1) or '')
            if style:
                attrs.update(_style_attrs(style.group(1)))
            cls = _MD_CLASS_RE.search(m.group(1) or '')
            if cls:
                for name in cls.group(1).split():
                    if name in _MD_FONT_BY_CLASS:
                        attrs['myfont'] = _MD_FONT_BY_CLASS[name]
            end = re.search(r'</span>', text[i:], re.I)
            if end:
                flush()
                ops.extend(_md_parse_inline(text[i + m.end():i + end.start()],
                                            _md_merge(base_attrs, attrs), _depth + 1))
                i += end.end(); continue
            i += m.end(); continue
        if ch == '`':
            end = text.find('`', i + 1)
            if end > 0:
                flush()
                ops.append({'insert': text[i + 1:end],
                            'attributes': _md_merge(base_attrs, {'code': True})})
                i = end + 1; continue
        if text.startswith('~~', i):
            end = text.find('~~', i + 2)
            if end > 0:
                flush()
                ops.extend(_md_parse_inline(text[i + 2:end],
                                            _md_merge(base_attrs, {'strike': True}), _depth + 1))
                i = end + 2; continue
        if text.startswith('**', i):
            end = text.find('**', i + 2)
            if end > 0:
                flush()
                ops.extend(_md_parse_inline(text[i + 2:end],
                                            _md_merge(base_attrs, {'bold': True}), _depth + 1))
                i = end + 2; continue
        if ch in '*_':
            end = text.find(ch, i + 1)
            if end > i + 1:
                flush()
                ops.extend(_md_parse_inline(text[i + 1:end],
                                            _md_merge(base_attrs, {'italic': True}), _depth + 1))
                i = end + 1; continue
        m = _MD_ATX_IMAGE.match(text[i:])
        if m:
            flush()
            src = m.group(2)
            name = src.split('/')[-1]
            ops.append({'insert': {'image': {'filename': name, 'storedPath': src}}})
            i += m.end(); continue
        if ch == '[':
            m = re.match(r'\[(.*?)\]\((.*?)\)', text[i:])
            if m:
                flush()
                href, label = m.group(2), m.group(1)
                if href.startswith('attachments/') and label.startswith('📎'):
                    ops.append({'insert': {'attachment': {
                        'filename': href.split('/')[-1],
                        'originalName': label.replace('📎', '').strip() or href.split('/')[-1],
                        'storedPath': href}}})
                else:
                    ops.append({'insert': label,
                                'attributes': _md_merge(base_attrs, {'link': href})})
                i += m.end(); continue
        buf.append(ch); i += 1
    flush()
    return ops or [{'insert': ''}]


def markdown_to_delta(md_text):
    r"""Markdown → Quill Delta JSON（标题/列表/待办/引用/代码块/表格/公式 + 行内格式）。

    只做常用子集：解析不出来的行当普通段落，不会丢内容（宁可少格式，不可少字）。

    LaTeX 标准定界符 `\(…\)` / `\[…\]` 会先归一成 `$…$` / `$$…$$`
    （见 `normalize_line_math`）——**围栏代码块内一律不动**（那里 `\(` 是字面量）。
    """
    lines = (md_text or '').replace('\r\n', '\n').replace('\r', '\n')
    # ① 跨行块级公式先折成单行（`\[`…`\]` 各自独占一行的写法）
    lines = _collapse_blocks_outside_code(lines)
    lines = lines.split('\n')
    # ② 再把每行的 `\(…\)` / `\[…\]` 归一成 `$…$` / `$$…$$`（`normalize_line_math`）。
    # ⚠️ 两步都必须按围栏**状态**跳过代码块：只判"这一行是不是围栏"是不够的，
    # 代码块**内部**的行既不匹配围栏、也不该被改写（实测漏过一次，`\(x\)` 被改成 `$x$`）。
    normalized, _in_fence = [], False
    for _ln in lines:
        if _MD_FENCE_RE.match(_ln):
            _in_fence = not _in_fence
            normalized.append(_ln)          # 围栏行本身没有公式
        else:
            normalized.append(_ln if _in_fence else normalize_line_math(_ln))
    lines = normalized
    ops, i = [], 0
    while i < len(lines):
        line = lines[i]
        if _MD_FENCE.match(line):
            i += 1
            block = []
            while i < len(lines) and not _MD_FENCE.match(lines[i]):
                block.append(lines[i]); i += 1
            i += 1
            ops.append({'insert': '\n'.join(block) + '\n',
                        'attributes': {'code-block': True}})
            continue
        m = _MD_HR_RE.match(line.strip())
        if m:
            style = 1
            cm = re.match(r'divider-(\d+)', m.group(1) or '')
            if cm:
                style = int(cm.group(1))
            ops.append({'insert': {'divider': {'style': style}}})
            ops.append({'insert': '\n'})
            i += 1; continue
        if line.strip() in ('---', '***', '___'):
            ops.append({'insert': '\n'})       # 水平线：Quill 里退化为空行
            i += 1; continue
        # GFM 表格：表头 + 分隔行 + 数据行 → Quill 表格属性行
        if ('|' in line and i + 1 < len(lines) and _MD_TABLE_SEP.match(lines[i + 1])):
            header = _md_split_table_row(line)
            i += 2
            rows = [header]
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(_md_split_table_row(lines[i]))
                i += 1
            import uuid as _uuid
            for row in rows:
                row_id = _uuid.uuid4().hex[:8]
                for c, cell in enumerate(row):
                    cell_id = '%s-%d' % (row_id, c)
                    ops.extend(_md_parse_inline(cell))
                    ops.append({'insert': '\n', 'attributes': {
                        'table': row_id, 'table-cell': cell_id, 'table-cell-line': 'last'}})
            continue
        m = _MD_MATH_BLOCK_RE.match(line.strip())
        if m:
            ops.append({'insert': {'math-formula': {'latex': m.group(1).strip(), 'display': True}}})
            ops.append({'insert': '\n'})
            i += 1; continue
        m = _MD_HEADING.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(2)))
            ops.append({'insert': '\n', 'attributes': {'header': len(m.group(1))}})
            i += 1; continue
        m = _MD_TODO.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(2)))
            ops.append({'insert': '\n',
                        'attributes': {'list': 'checked' if m.group(1).lower() == 'x' else 'unchecked'}})
            i += 1; continue
        m = _MD_UL.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'list': 'bullet'}})
            i += 1; continue
        m = _MD_OL.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'list': 'ordered'}})
            i += 1; continue
        m = _MD_QUOTE.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'blockquote': True}})
            i += 1; continue
        if line.strip():
            ops.extend(_md_parse_inline(line))
        ops.append({'insert': '\n'})
        i += 1
    return json.dumps({'ops': ops}, ensure_ascii=False)

def _delta_to_text(content):
    """Quill Delta JSON → 纯文本（搜索索引用）；解析失败回退原串；截断 100KB"""
    if not content:
        return ''
    try:
        data = json.loads(content)
        ops = data.get('ops', []) if isinstance(data, dict) else data
        parts = []
        for op in ops:
            ins = op.get('insert') if isinstance(op, dict) else None
            if isinstance(ins, str):
                parts.append(ins)   # 跳过图片等 embed dict
        text = ''.join(parts)
    except Exception:
        text = content
    return text[:100_000]

_MD_SCRIPT = re.compile(r'<(script|style)\b.*?</\1>', re.I | re.S)
_MD_IMAGE = re.compile(r'!\[([^\]]*)\]\([^)]*\)')
_MD_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_MD_TAG = re.compile(r'<[^>]+>')
_MD_INLINE = re.compile(r'`{1,3}|~{2}|\*{1,3}')
# 行内标记**直接删掉**，不能替换成空格：`这是**正文**内容` 若变成「这是正文 内容」，
# 用户搜「正文内容」就永远搜不到（FTS trigram 匹配的是连续子串）。
# 下划线故意不处理：CommonMark 里词内下划线本就不是强调（`my_var` 不该变成 `myvar`）。
_MD_BLOCK = re.compile(
    r'^\s*>+\s*'                               # 引用
    r'|^\s*[-*+]\s+(\[[ xX]\]\s*)?'            # 列表 / 待办
    r'|^\s*\d+[.)]\s+'                         # 有序列表
    r'|^#{1,6}\s+', re.M)                      # 标题
_MD_ENTITIES = (('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'),
                ('&quot;', '"'), ('&#39;', "'"), ('&amp;', '&'))


def _markdown_to_text(md):
    """Markdown → 纯文本（搜索索引 / 列表摘要用）。

    顺序有讲究：先摘掉 script/style 整段，再把图片/链接换成它们的文字，最后才去标签——
    反过来会把 `<img alt="说明">` 的尖括号内容切成半截。
    """
    if not md:
        return ''
    # front-matter（属性）不是正文：摘要、FTS、字数都不该带上它。
    # 注意 _FM_RE 定义在下面一点（函数体里引用模块级名字，运行时才解析，没问题）。
    md = _FM_RE.sub('', md, count=1)
    text = _MD_SCRIPT.sub(' ', md)
    text = _MD_IMAGE.sub(lambda m: m.group(1), text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _MD_TAG.sub(' ', text)              # 白名单内嵌 HTML（span/u/hr…）只留文字
    text = _MD_INLINE.sub('', text)            # 行内标记不留空格（否则会切断连续子串）
    text = _MD_BLOCK.sub(' ', text)            # 块级前缀换成空格，避免把两行粘成一个词
    for ent, ch in _MD_ENTITIES:               # &amp; 必须最后解，否则 &amp;lt; 会解成 <
        text = text.replace(ent, ch)
    return text[:100_000]


def note_plain_text(content, fmt='delta'):
    """按 format 取正文纯文本 —— 所有需要「读正文」的地方都走这里，别再各自判断"""
    return _markdown_to_text(content) if fmt == 'md' else _delta_to_text(content)


PREVIEW_MAX = 120      # 列表摘要最长字符数（列表里只有一行，再多也是被省略号截掉）

def _preview_text(content, limit=PREVIEW_MAX, fmt='delta'):
    """正文 → 单行摘要（列表显示用）。Markdown 笔记剥掉标记，Delta 笔记走原逻辑。

    与 _delta_to_text 的区别：解析失败返回**空串**而不是原始 JSON——摘要位置显示一坨
    `{"ops":[...` 比不显示更糟。取够 limit*2 个字符就停，不必遍历全部 ops。
    """
    if not content:
        return ''
    if fmt == 'md':
        return ' '.join(_markdown_to_text(content).split())[:limit]
    try:
        data = json.loads(content)
    except Exception:
        return ''
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return ''
    parts, total = [], 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins = op.get('insert')
        if isinstance(ins, str):
            parts.append(ins)
            total += len(ins)
            if total >= limit * 2:
                break
    return ' '.join(''.join(parts).split())[:limit]   # 折叠换行与连续空白


# ====== 派生索引：一次解析算出所有"可查询侧面" ======
# 格式判据只有 notes.format；md 走行扫描，delta 走 ops 遍历，两者产出口径必须一致
# （测试里用"同一篇内容两种格式算出的指标应相同"来锁死这一点）。
_TODO_RE = re.compile(r'^\s*[-*+]\s+\[([ xX])\]\s+(.*)$')
# 待办日期标记：Obsidian Tasks 的 `📅 2026-09-25`，以及第 12 轮加的 ASCII 别名 `@2026-09-25`。
# 为什么要别名：界面提示里教用户"照抄 📅"等于在 UI 里塞一个 emoji（本项目 UI 图标一律 SVG），
# 可它又确实是语法本体、换成图标就没法照抄了 —— 别名让提示可以完全不带 emoji。
# 两种写法共用这一条正则（解析、剥离、勾选写回的文本校验全都走它），口径不会分叉。
_DUE_RE = re.compile(r'(?:📅|@)\s*(\d{4}-\d{2}-\d{2})')
_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
_FM_RE = re.compile(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)', re.S)
WORD_RE = re.compile(r'[\u4e00-\u9fff]|[A-Za-z0-9_]+')


def count_words(text):
    """混合中英文字数：中日韩字符按字计，拉丁/数字按词计（与主流编辑器的口径接近）"""
    return len(WORD_RE.findall(text or ''))


def _unquote(value):
    """去掉一层成对引号（YAML 值里最常见的修饰）"""
    v = (value or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def parse_front_matter(md_text):
    """极简 YAML front-matter 解析（只认 `key: value` 与 `- 列表`）。

    第 9 轮做完整属性系统时会扩展这里；现在先把 props_json 填上，表格视图/筛选才有的用。
    """
    m = _FM_RE.match(md_text or '')
    if not m:
        return {}
    props, key = {}, None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        if raw.lstrip().startswith('- ') and key:
            props.setdefault(key, [])
            if isinstance(props[key], list):
                props[key].append(_unquote(raw.lstrip()[2:].strip()))
            continue
        if ':' not in raw:
            continue
        k, _, v = raw.partition(':')
        key = k.strip()
        v = v.strip()
        if not v:
            props[key] = []          # 可能是块式列表，下一行开始收集
        elif v.startswith('[') and v.endswith(']'):
            props[key] = [_unquote(x) for x in v[1:-1].split(',') if x.strip()]
        else:
            props[key] = v.strip('"\'')
    return props


# ====== 双链 [[标题#小节|别名]]（第 9 轮）======
# 单独一条正则而不是复用 _LINK_RE：那条只管计数，口径已被第 7 轮的搜索测试锁死；
# 这里要把 标题 / 小节 / 别名 三段拆开，改 _LINK_RE 等于顺手改掉别人的口径。
_WIKILINK_RE = re.compile(r'\[\[([^\]\|#]*)(?:#([^\]\|]+))?(?:\|([^\]]*))?\]\]')


def extract_links(content, fmt):
    """正文 → [(序号, 标题原文, 标题归一, 小节, 别名)]（双链索引的数据源）。

    走 note_plain_text 而不是原始正文：md 与 delta 用同一条口径（与 link_count 一致）。
    `[[#小节]]`（同篇内跳转）标题为空 → target_key 记空串，反向链接查询会跳过它。
    """
    out = []
    for m in _WIKILINK_RE.finditer(note_plain_text(content, fmt)):
        raw = (m.group(1) or '').strip()
        heading = (m.group(2) or '').strip() or None
        alias = (m.group(3) or '').strip() or None
        if not raw and not heading:
            continue
        out.append((len(out), raw, raw.lower(), heading, alias))
    return out


def _link_context(content, fmt, title, heading, span=42):
    """反向链接的上下文片段（命中处 ±span 字，换行压成空格）。

    读的是**明文**正文；加密笔记由调用方挡住（根本读不到正文）。
    """
    plain = note_plain_text(content, fmt)
    needle = '[[' + (title or '') + (('#' + heading) if heading else '')
    at = plain.find(needle)
    if at < 0:
        return ''
    start, end = max(0, at - span), min(len(plain), at + len(needle) + span)
    body = plain[start:end].replace('\n', ' ').strip()
    return ('…' if start else '') + body + ('…' if end < len(plain) else '')


# ====== 属性（front-matter）查询（第 9 轮）======
def _prop_norm(key):
    return (key or '').strip().lower()


def _prop_match(props, key, op, val):
    """单个属性条件。键名大小写不敏感；值是列表时"任一项命中"即算命中。

    op 为 None 表示只要求键存在（`prop:状态`）。比较运算在"两边都能转成数字"时按数字，
    否则按字符串——ISO 日期的字典序就是时间序，所以 `prop:截止>2026-10-01` 天然成立。
    """
    if not props:
        return False
    target = None
    for k, v in props.items():
        if _prop_norm(k) == _prop_norm(key):
            target = v
            break
    else:
        return False
    if op is None:
        return True
    values = target if isinstance(target, list) else [target]
    if op == '~':
        return any(val.lower() in str(v).lower() for v in values)
    if op == '=':
        return any(str(v).strip().lower() == val.lower() for v in values)
    for v in values:
        a, b = str(v).strip(), val
        try:
            x, y = float(a), float(b)
        except ValueError:
            x, y = a, b
        if op == '>' and x > y:
            return True
        if op == '<' and x < y:
            return True
        if op == '>=' and x >= y:
            return True
        if op == '<=' and x <= y:
            return True
    return False


# ====== 模板与快速捕获（第 10 轮）======
WEEKDAY_CN = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
TEMPLATE_VAR_RE = re.compile(r'\{\{\s*(date|time|weekday|title|datetime)\s*\}\}')


def render_template(text, title=''):
    """把模板里的变量换掉（只认这几个，不认识的 `{{...}}` 原样留着）。

    为什么不做通用模板引擎：模板是用户手写的 Markdown，语法越少越不容易和正文打架；
    替换只在"用模板建笔记"的那一刻发生一次，之后就是普通正文。
    """
    now = datetime.now()
    values = {
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M'),
        'weekday': WEEKDAY_CN[now.weekday()],
        'datetime': now.strftime('%Y-%m-%d %H:%M'),
        'title': title or '',
    }
    return TEMPLATE_VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text or '')


def _first_line_title(text, limit=50):
    """捕获内容 → 标题：取第一行非空文本，太长就截断（列表里显示得下）"""
    for line in (text or '').splitlines():
        s = line.strip().lstrip('#').strip()
        if s:
            return s[:limit] + ('…' if len(s) > limit else '')
    return '未命名笔记'


def _todo_lines_md(md_text):
    """Markdown → [(idx, text, due, done)]"""
    out = []
    for line in (md_text or '').splitlines():
        m = _TODO_RE.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        dm = _DUE_RE.search(body)
        due = dm.group(1) if dm else None
        text = _DUE_RE.sub('', body).strip()
        out.append((len(out), text, due, 1 if m.group(1).lower() == 'x' else 0))
    return out


def _todo_lines_delta(content):
    """Quill Delta → [(idx, text, due, done)]（行属性挂在含换行符的 op 上）"""
    try:
        data = json.loads(content or '{}')
    except Exception:
        return []
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return []
    lines, buf = [], []
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins, attrs = op.get('insert'), op.get('attributes') or {}
        if not isinstance(ins, str):
            continue
        parts = ins.split('\n')
        for i, part in enumerate(parts):
            buf.append(part)
            if i < len(parts) - 1:
                lines.append((''.join(buf), attrs))
                buf = []
    out = []
    for text, attrs in lines:
        lst = attrs.get('list')
        if lst not in ('checked', 'unchecked'):
            continue
        body = text.strip()
        dm = _DUE_RE.search(body)
        due = dm.group(1) if dm else None
        out.append((len(out), _DUE_RE.sub('', body).strip(), due, 1 if lst == 'checked' else 0))
    return out


def derive_metrics(content, fmt, note_id=None):
    """正文 → 派生指标（不含附件/提醒这两个要查表的字段，由 _refresh_derived 补）"""
    plain = note_plain_text(content, fmt)
    # 统计前去掉日期标记（📅 或 @）：否则 md（剥标记）与 delta（保留标记）算出的字数会不一致
    plain_for_count = _DUE_RE.sub('', plain)
    todos = _todo_lines_md(content) if fmt == 'md' else _todo_lines_delta(content)
    open_todos = [t for t in todos if not t[3]]
    dues = sorted(t[2] for t in open_todos if t[2])
    # `\(` / `\[` 也算公式：用户那篇《Lecture 2.2》整篇用标准定界符、一个 `$` 都没有，
    # 只认 `$` 的话 `has:formula` 搜索过滤会对它整个失效。
    has_formula = (('$' in (content or '')) or ('\\(' in (content or ''))
                   or ('\\[' in (content or ''))) if fmt == 'md' else ('math-formula' in (content or ''))
    has_code = ('```' in (content or '')) if fmt == 'md' else ('code-block' in (content or ''))
    return {
        'word_count': count_words(plain_for_count),
        # 字符数**不含空白**：否则 md 里被剥掉的 `- [ ] ` 标记会留下空格，
        # 与同内容的 delta 笔记算出的字符数对不上（两种格式的口径必须统一）
        'char_count': len(re.sub(r'\s+', '', plain_for_count)),
        'todo_total': len(todos),
        'todo_open': len(open_todos),
        'todo_next_due': dues[0] if dues else None,
        'has_formula': 1 if has_formula else 0,
        'has_code': 1 if has_code else 0,
        'link_count': len(_LINK_RE.findall(plain)),
        'props_json': json.dumps(parse_front_matter(content) if fmt == 'md' else {},
                                 ensure_ascii=False),
        # 列表摘要也在这里算：列表每次刷新都要给**每一篇**算一行摘要，而 _markdown_to_text
        # 是六趟正则扫全文 —— 800 篇 2.7KB 的库实测光算摘就要 223ms（占 notes_list 总耗时 74%）。
        # 保存时算一次（内容已经在手上），列表就只是读一列。
        'preview': _preview_text(content, fmt=fmt),
        'todos': todos,
    }
