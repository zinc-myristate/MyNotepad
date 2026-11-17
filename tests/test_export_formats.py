# -*- coding: utf-8 -*-
"""单篇导出（HTML / TXT / XLSX）：单测管文件内容，实机回归走真实导出按钮、把文件开起来量。

公式的 HTML 有三份东西：藏给读屏软件的 MathML、排版用的 .katex-html、根号等处的 SVG。
前者靠 KaTeX 样式表藏起来，SVG 靠它裁切，所以导出模板不带样式表时，字符串里什么都不缺，
打开却是公式显示两遍、页面被撑到 6000 多 px 宽。这类问题只有真的打开文件才看得见。

DOCX 的转换（docx_export.py）在 test_export_docx.py 里测；这里的实机回归只看
"真实导出按钮交出去的 HTML"写成 DOCX 后，公式、列表符号、代码块都还在。
"""
import base64
import importlib
import json
import os
import re
import shutil
import threading
import time

import pytest
from conftest import PROJECT_ROOT, _purge_backend_modules, load_app_partial, make_delta_note, wait_for_js

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
KATEX_DIR = os.path.join(RENDERER, 'katex')

# 与 quill.root.innerHTML 同样的结构：根号的 <svg><path> 夹在 .katex-html 中间
FORMULA = ('<span class="katex"><span class="katex-mathml"><math><semantics><mrow><mi>x</mi></mrow>'
           '<annotation encoding="application/x-tex">\\sqrt{x}</annotation></semantics></math></span>'
           '<span class="katex-html" aria-hidden="true"><span class="mord">x</span>'
           '<span class="hide-tail"><svg width="400em"><path d="M95,702"></path></svg></span>'
           '<span class="mord">+1</span></span></span>')


def _export_text(api, tmp_path, html, name='out.html'):
    path = str(tmp_path / name)
    assert api.export_note('标题', html, 'html', path) is True
    with open(path, encoding='utf-8') as f:
        return f.read()


# ---------------- 单测：导出文件的内容 ----------------

def test_html_without_formula_carries_no_katex(api, tmp_path):
    """样式表和字体有几百 KB，没有公式的笔记不该为它们买单"""
    page = _export_text(api, tmp_path, '<p>普通文字</p>')
    assert '@font-face' not in page and 'katex' not in page.lower()
    assert len(page) < 2048


def test_html_with_formula_embeds_every_katex_font(api, tmp_path):
    """字体必须全部内嵌：留一个 url(fonts/...) 在别的机器上就是 404，公式悄悄换成系统字体"""
    page = _export_text(api, tmp_path, '<p>%s</p>' % FORMULA)
    with open(os.path.join(KATEX_DIR, 'katex.min.css'), encoding='utf-8') as f:
        expected = f.read().count('@font-face')
    fonts = re.findall(r'src:url\(data:font/woff2;base64,([A-Za-z0-9+/=]+)\) format\("woff2"\)', page)
    assert expected > 0 and len(fonts) == expected, '内嵌了 %d 个字体，样式表里有 %d 个' % (len(fonts), expected)
    assert 'url(fonts/' not in page, '还有字体引用着相对路径'
    assert all(base64.b64decode(data)[:4] == b'wOF2' for data in fonts)
    assert 'Khan Academy' in page and 'OFL' in page, 'KaTeX 与字体的授权声明不能丢'


def test_katex_assets_come_from_the_bundle_when_frozen(tmp_path, monkeypatch):
    """打包版的 renderer/ 在 bundle 里，不在 exe 旁边：路径按 _MEIPASS 算，与 app.pyw 的 BASE_DIR 一致"""
    bundle = tmp_path / 'bundle'
    shutil.copytree(KATEX_DIR, bundle / 'renderer' / 'katex')
    css = bundle / 'renderer' / 'katex' / 'katex.min.css'
    css.write_text(css.read_text(encoding='utf-8') + '/* 来自 bundle */', encoding='utf-8', newline='')
    monkeypatch.setattr('sys._MEIPASS', str(bundle), raising=False)
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    _purge_backend_modules()
    backend = importlib.import_module('backend')
    try:
        assert '/* 来自 bundle */' in _export_text(backend.api, tmp_path, FORMULA)
    finally:
        backend.conn.close()
        _purge_backend_modules()


def test_txt_and_xlsx_put_list_items_on_their_own_lines(api, tmp_path):
    """列表项以前连成一串（`项目一项目二`），带上符号也读不了：TXT 一项一行，XLSX 一项一格。
    HTML 照抄富文本导出前的样子：符号已经写成文字，列表样式是 none（02-editor.js 的 writeListMarkers）"""
    from openpyxl import load_workbook
    html = ('<ol><li data-list="bullet" style="list-style-type: none;">• 项目一</li>'
            '<li data-list="checked" style="list-style-type: none; text-decoration: line-through;">☑ 待办</li>'
            '</ol><p>段落</p>')
    txt = str(tmp_path / 'out.txt')
    assert api.export_note('标题', html, 'txt', txt) is True
    with open(txt, encoding='utf-8') as f:
        assert f.read() == '• 项目一\n☑ 待办\n\n段落'
    xlsx = str(tmp_path / 'out.xlsx')
    assert api.export_note('标题', html, 'xlsx', xlsx) is True
    assert [cell.value for cell in load_workbook(xlsx).active['A']] == ['• 项目一', '☑ 待办', '段落']


# ---------------- e2e：导出再打开 ----------------

_SQRT = r'\sqrt{x}+1'
_TAIL = r'\sqrt{y}+2'          # 段尾的根号：`<path` 切开后，后半段以标签开头结尾，整块被 DOCX 丢掉
_FRAC = r'\frac{a}{b}'
_BB = r'\mathbb{R}'
_LONG = '+'.join('x_{%d}' % i for i in range(200))
FORMULA_COUNT = 5

MARKDOWN_NOTE = ('开头 $%s$ 根号后面的文字\n\n段尾公式 $%s$\n\n$$%s$$\n\n$%s$\n\n$$%s$$\n\n```\ncode line\n```\n\n'
                 '- [ ] 待办未做\n- [x] 待办已做\n' % (_SQRT, _TAIL, _FRAC, _BB, _LONG))


def _list_item(text, kind, indent=0):
    attrs = {'list': kind, **({'indent': indent} if indent else {})}
    return [{'insert': text}, {'insert': '\n', 'attributes': attrs}]


# 编号要和编辑器里一样：同一层接着编（中间夹着无序项也不断）、出现更浅的项时子层重新编、
# 段落之后重新编、各层依次 1. a. i.
RICH_LIST = (_list_item('第一步', 'ordered') + _list_item('子步骤甲', 'ordered', 1)
             + _list_item('子步骤乙', 'ordered', 1) + _list_item('插一项', 'bullet')
             + _list_item('第二步', 'ordered') + _list_item('子步骤丙', 'ordered', 1)
             + _list_item('待办未做', 'unchecked') + _list_item('待办已做', 'checked')
             + [{'insert': '列表之后的段落\n'}]
             + _list_item('重新开始', 'ordered') + _list_item('第三层', 'ordered', 2))
RICH_LIST_LINES = ['1. 第一步', 'a. 子步骤甲', 'b. 子步骤乙', '• 插一项', '2. 第二步', 'a. 子步骤丙',
                   '☐ 待办未做', '☑ 待办已做', '1. 重新开始', 'i. 第三层']
# 代码块和附件卡片在编辑器里带着控件（语言下拉框、删除按钮），导出时要去掉
RICH_TEXT_NOTE = json.dumps({'ops': [
    {'insert': 'code line'},
    {'insert': '\n', 'attributes': {'code-block': 'plain'}},
    {'insert': {'attachment': {'id': 'a1', 'filename': 'x.pdf', 'originalName': '报告.pdf', 'fileSize': 2048,
                               'mimeType': 'application/pdf', 'storedPath': ''}}},
    {'insert': '\n'},
    {'insert': '开头 '},
    {'insert': {'math-formula': {'latex': _SQRT, 'display': 'inline'}}},
    {'insert': ' 根号后面的文字\n段尾公式 '},
    {'insert': {'math-formula': {'latex': _TAIL, 'display': 'inline'}}},
    {'insert': '\n'},
    {'insert': {'math-formula': {'latex': _FRAC, 'display': 'block'}}},
    {'insert': '\n'},
    {'insert': {'math-formula': {'latex': _BB, 'display': 'inline'}}},
    {'insert': '\n'},
    {'insert': {'math-formula': {'latex': _LONG, 'display': 'block'}}},
    {'insert': '\n'},
    *RICH_LIST,
]}, ensure_ascii=False)

# 只看屏幕上有没有：KaTeX 藏 MathML 是裁成 1×1，不是 display: none，所以 innerText 和 computed
# display 都判断不了。
_MEASURE_JS = r"""
(function () {
  function shown(el) {
    var r = el ? el.getBoundingClientRect() : {width: 0, height: 0};
    return r.width > 1 && r.height > 1;
  }
  function leaf(root, text) {
    return Array.prototype.filter.call(root.querySelectorAll('span'), function (el) {
      return !el.children.length && el.textContent === text;
    })[0];
  }
  function top(el) { return el ? el.getBoundingClientRect().top : null; }
  var all = document.querySelectorAll('.katex');
  var bb = Array.prototype.filter.call(all, function (k) {
    return k.querySelector('annotation').textContent === %s;
  })[0];
  var frac = Array.prototype.filter.call(all, function (k) {
    return k.querySelector('annotation').textContent === %s;
  })[0];
  var bbLeaf = bb ? leaf(bb.querySelector('.katex-html'), 'R') : null;
  return JSON.stringify({
    scrollWidth: document.documentElement.scrollWidth,
    viewWidth: document.documentElement.clientWidth,
    formulas: all.length,
    copies: Array.prototype.map.call(all, function (k) {
      return [k.querySelector('.katex-mathml'), k.querySelector('.katex-html')].filter(shown).length;
    }),
    fracGap: frac ? top(leaf(frac, 'b')) - top(leaf(frac, 'a')) : null,
    bbFont: bbLeaf ? getComputedStyle(bbLeaf).fontFamily : '',
    rootMarks: document.querySelectorAll('.katex-html svg').length,
    controls: document.querySelectorAll('select, button, input, .ql-ui').length,
    items: Array.prototype.map.call(document.querySelectorAll('li[data-list], li.md-task'), function (li) {
      var cs = getComputedStyle(li);
      return [li.textContent.trim(), cs.listStyleType, cs.textDecorationLine];
    }),
    body: document.body.innerText
  });
})()
"""

# \mathbb 用的字体。内嵌字体是 data URI，也要等解码完；相对路径的话在别处 404，永远等不到
_AMS_LOADED = ("Array.from(document.fonts).some(function (f) {"
               " return f.family.indexOf('KaTeX_AMS') >= 0 && f.status === 'loaded'; })")


def _run(ns, actions):
    import webview
    result = {}
    window = webview.create_window('导出回归', os.path.join(RENDERER, 'index.html'),
                                   js_api=ns['api'], width=1200, height=800)

    def runner():
        try:
            wait_for_js(window, "!!(window.__app && window.__app.state.activeNoteId"
                                " && !window.__app.state.isLoading)", timeout=40)
            actions(window, result)
        except Exception as exc:                     # noqa: BLE001
            result['error'] = repr(exc)
        finally:
            try:
                window.destroy()
            except Exception:                        # noqa: BLE001
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


def _wait_written(path, complete, timeout=20.0):
    """保存走桥接、前端拿不到完成信号，只能看文件：写到 complete 成立才算导出完"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) and complete(path):
            return
        time.sleep(0.1)
    raise AssertionError('等不到导出文件：%s' % path)


def _html_complete(path):
    with open(path, encoding='utf-8') as f:
        return f.read().rstrip().endswith('</html>')


def _docx_complete(path):
    from docx import Document
    try:
        Document(path)
    except Exception:                                # noqa: BLE001  还没写完
        return False
    return True


def _seed_notes(backend):
    md_id = backend.api.notes_create()['id']
    backend.api.notes_update(md_id, {'title': 'Markdown 公式', 'content': MARKDOWN_NOTE})
    return md_id, make_delta_note(backend, title='富文本公式', content=RICH_TEXT_NOTE)


def _click_export(window, note_id, fmt, editor_selector, export_format, formulas=FORMULA_COUNT):
    """点列表里的笔记 → 打开导出面板 → 点导出格式，全程走真实界面"""
    item = "document.querySelector('#note-list .note-item[data-note-id=\"%s\"]')" % note_id
    wait_for_js(window, "!!" + item, True)
    window.evaluate_js(item + ".click()")
    wait_for_js(window, "window.__app.state.activeNoteId === %s && window.__app.state.noteFormat === %s"
                        " && !window.__app.state.isLoading" % (json.dumps(note_id), json.dumps(fmt)), True)
    wait_for_js(window, "document.querySelectorAll(%s).length" % json.dumps(editor_selector + ' .katex'),
                formulas)
    window.evaluate_js("document.getElementById('btn-export').click()")
    wait_for_js(window, "getComputedStyle(document.getElementById('export-panel')).display !== 'none'", True)
    window.evaluate_js("document.querySelector('.export-option[data-format=\"%s\"]').click()" % export_format)


def _open_and_measure(window, path):
    window.load_url('file:///' + path.replace('\\', '/'))
    wait_for_js(window, "location.protocol === 'file:' && document.readyState === 'complete'"
                        " && location.pathname.endsWith(%s)" % json.dumps(os.path.basename(path)), True)
    fonts_loaded = wait_for_js(window, _AMS_LOADED, True, timeout=10)
    page = json.loads(window.evaluate_js(_MEASURE_JS % (json.dumps(_BB), json.dumps(_FRAC))))
    page['fontsLoaded'] = fonts_loaded
    return page


def _page_problems(page):
    """一次把看得见的问题列全：几个症状常常同时出现，只报第一条会看不出是哪种坏法"""
    lines = [line.strip() for line in page['body'].split('\n')]
    done = [deco for text, _, deco in page['items'] if text.endswith('待办已做')]
    checks = [
        (page['formulas'] == FORMULA_COUNT, '公式数量不对：%r' % page['formulas']),
        (page['scrollWidth'] <= page['viewWidth'],
         '页面被撑宽：%spx > 视口 %spx' % (page['scrollWidth'], page['viewWidth'])),
        (page['copies'] == [1] * FORMULA_COUNT, '每个公式应当只显示一份：%r' % page['copies']),
        ('根号后面的文字' in page['body'], '正文丢了'),
        (page['fontsLoaded'] is True, 'KaTeX 字体没有加载出来'),
        (page['fracGap'] is not None and page['fracGap'] > 5, '分式没有上下排开：%r' % page['fracGap']),
        ('KaTeX_AMS' in page['bbFont'], '\\mathbb 的字体不对：%r' % page['bbFont']),
        (page['rootMarks'] >= 2, '根号的 SVG 没了，只剩根号下面的字'),
        (page['controls'] == 0, '编辑器的控件（下拉框、按钮）跟进了导出文件：%d 个' % page['controls']),
        ('code line' in page['body'], '代码块丢了'),
        ('☐ 待办未做' in lines and '☑ 待办已做' in lines, '待办看不出勾没勾'),
        (all(style == 'none' for _, style, _ in page['items']),
         '列表项还带着浏览器自己的编号或圆点：%r' % page['items']),
        (done and all('line-through' in deco for deco in done), '做完的待办没有划掉：%r' % done),
    ]
    return [message for ok, message in checks if not ok]


@pytest.mark.e2e
def test_exported_html_opens_with_each_formula_shown_once(tmp_path, monkeypatch):
    """Markdown 与富文本各导出一次再打开：页面不被撑宽、每个公式只显示一份、排版和字体都在。

    两条路的 HTML 来源不同（renderMarkdown / quill.root.innerHTML），都要过一遍。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    md_id, rich_id = _seed_notes(backend)
    md_path = str(tmp_path / 'markdown.html')
    rich_path = str(tmp_path / 'rich.html')
    target = {}
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: target['path'])

    def actions(window, result):
        target['path'] = md_path
        _click_export(window, md_id, 'md', '#md-preview', 'html')
        _wait_written(md_path, _html_complete)
        target['path'] = rich_path
        _click_export(window, rich_id, 'delta', '.ql-editor', 'html')
        _wait_written(rich_path, _html_complete)
        result['markdown'] = _open_and_measure(window, md_path)
        result['rich'] = _open_and_measure(window, rich_path)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    problems = {'Markdown': _page_problems(r['markdown']), '富文本': _page_problems(r['rich'])}
    assert problems == {'Markdown': [], '富文本': []}
    texts = [expected.split(' ', 1)[1] for expected in RICH_LIST_LINES]
    items = [line.strip() for line in r['rich']['body'].split('\n')
             if any(line.strip().endswith(text) for text in texts)]
    assert items == RICH_LIST_LINES, '富文本列表的符号和编号与编辑器里不一样：%r' % items


@pytest.mark.e2e
@pytest.mark.parametrize('fmt, editor', [('md', '#md-preview'), ('delta', '.ql-editor')],
                         ids=['markdown', 'rich-text'])
def test_docx_keeps_formulas_and_text_around_them(tmp_path, monkeypatch, fmt, editor):
    """真实导出按钮交出去的 HTML 写成 DOCX：根号前后的字、独占一段的长公式、代码块、待办都在。

    公式写 TeX 源码（docx_export.py 的约定），每个只写一次：排版用的那份和 MathML 都不能再写进去。
    列表和待办的符号是导出前页面写好的文字（list-style-type: none），DOCX 不能再画第二个。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    md_id, rich_id = _seed_notes(backend)
    note_id = md_id if fmt == 'md' else rich_id
    path = str(tmp_path / 'formula.docx')
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: path)

    def actions(window, result):
        _click_export(window, note_id, fmt, editor, 'docx')
        _wait_written(path, _docx_complete)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    from docx import Document
    lines = [p.text for p in Document(path).paragraphs]
    text = '\n'.join(lines)
    for piece in ('开头 $%s$ 根号后面的文字' % _SQRT, '段尾公式 $%s$' % _TAIL, '$$%s$$' % _FRAC,
                  '$%s$' % _BB, 'x_{199}', 'code line'):       # x_{199}：独占一段的长公式
        assert piece in text, 'DOCX 丢了 %r：%r' % (piece, text)
    for tex in (_SQRT, _TAIL, _FRAC, _BB):
        assert text.count(tex) == 1, '公式 %r 写了不止一次：%r' % (tex, text)
    assert '☐ 待办未做' in lines and '☑ 待办已做' in lines, '待办的符号丢了或画了两次：%r' % lines
    assert 'Plain' not in text, '代码块的语言下拉框进了 DOCX：%r' % text


# ---------------- e2e：XLSX / TXT 走真实导出按钮 ----------------
#
# 以前富文本表格的格子（<td data-row>）一格也认不到，XLSX 里的表是空的，表格前后的段落也没了；
# 带对齐的 Markdown 表格只剩没对齐的那一列；公式写成三份字连在一起；`=1+1` 被写成了公式。

TABLE_MD = ('表格前 $x^2$\n\n| 甲 | 乙 |\n|---|:-:|\n| $y_1$ | **粗** |\n\n表格后 a < b\n\n=1+1\n\n'
            '- 外层\n  - 内层\n- [x] 做完\n')
TABLE_RICH = json.dumps({'ops': [
    {'insert': '表格前 '}, {'insert': {'math-formula': {'latex': 'x^2', 'display': 'inline'}}}, {'insert': '\n'},
    {'insert': '甲'}, {'insert': '\n', 'attributes': {'table': 'row-1'}},
    {'insert': '乙'}, {'insert': '\n', 'attributes': {'table': 'row-1'}},
    {'insert': 'y1'}, {'insert': '\n', 'attributes': {'table': 'row-2'}},
    {'insert': '粗', 'attributes': {'bold': True}}, {'insert': '\n', 'attributes': {'table': 'row-2'}},
    {'insert': '表格后 a < b\n=1+1\n'},
    *_list_item('外层', 'bullet'), *_list_item('内层', 'bullet', 1), *_list_item('做完', 'checked'),
]}, ensure_ascii=False)


def _xlsx_complete(path):
    from openpyxl import load_workbook
    try:
        load_workbook(path)
    except Exception:                                # noqa: BLE001  还没写完
        return False
    return True


@pytest.mark.e2e
def test_xlsx_and_txt_from_the_export_buttons(tmp_path, monkeypatch):
    """Markdown 与富文本笔记各导出一次 XLSX 和 TXT：表格、表格前后的段落、公式、列表都在，`=1+1` 是字不是公式"""
    from openpyxl import load_workbook
    from test_export_xlsx import cells
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    md_id = backend.api.notes_create()['id']
    backend.api.notes_update(md_id, {'title': '表格 Markdown', 'content': TABLE_MD})
    rich_id = make_delta_note(backend, title='表格富文本', content=TABLE_RICH)
    target = {}
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: target['path'])

    def actions(window, result):
        for key, note_id, fmt, editor, formulas in (('md', md_id, 'md', '#md-preview', 2),
                                                    ('rich', rich_id, 'delta', '.ql-editor', 1)):
            for ext in ('xlsx', 'txt'):
                target['path'] = str(tmp_path / ('%s.%s' % (key, ext)))
                _click_export(window, note_id, fmt, editor, ext, formulas)
                _wait_written(target['path'], _xlsx_complete if ext == 'xlsx' else os.path.getsize)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    sheets = {key: load_workbook(str(tmp_path / ('%s.xlsx' % key)), rich_text=True).active for key in ('md', 'rich')}
    texts = {}
    for key in ('md', 'rich'):
        with open(str(tmp_path / ('%s.txt' % key)), encoding='utf-8') as f:
            texts[key] = f.read()

    md, rich = sheets['md'], sheets['rich']
    assert cells(md) == [('A1', '表格前 $x^2$'), ('A3', '甲'), ('B3', '乙'), ('A4', '$y_1$'), ('B4', '粗'),
                         ('A6', '表格后 a < b'), ('A7', '=1+1'), ('A8', '• 外层'), ('A9', '◦ 内层'),
                         ('A10', '☑ 做完')], cells(md)
    assert md['A3'].font.b and md['B3'].alignment.horizontal == 'center' and md['B4'].font.b
    assert cells(rich) == [('A1', '表格前 $x^2$'), ('A3', '甲'), ('B3', '乙'), ('A4', 'y1'), ('B4', '粗'),
                           ('A6', '表格后 a < b'), ('A7', '=1+1'), ('A8', '• 外层'), ('A9', '• 内层'),
                           ('A10', '☑ 做完')], cells(rich)
    assert rich['B4'].font.b and rich['A9'].alignment.indent == 1 and rich['A10'].font.strike
    for ws in (md, rich):
        assert ws['A7'].data_type == 's', '`=1+1` 被写成了公式'
    assert texts['md'] == ('表格前 $x^2$\n\n甲\t乙\n$y_1$\t粗\n\n表格后 a < b\n\n=1+1\n\n'
                           '• 外层\n  ◦ 内层\n☑ 做完'), texts['md']
    assert texts['rich'] == ('表格前 $x^2$\n\n甲\t乙\ny1\t粗\n\n表格后 a < b\n\n=1+1\n\n'
                             '• 外层\n  • 内层\n☑ 做完'), texts['rich']
