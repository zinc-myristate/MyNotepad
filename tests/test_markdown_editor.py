# -*- coding: utf-8 -*-
"""Markdown 地基的实机回归（无头 pywebview）+ 资源/消毒静态守卫。

这一层的价值：前端「按 format 分流」的分叉点（编辑器显隐、保存取值、预览渲染）
只在真实浏览器里才会暴露问题——单测只能验后端。所以这里：

  · 用真实事件驱动界面（点按钮 / CodeMirror.setValue / 真按键），不用内部函数直调；
  · 断言「预览真的渲染出来了」（h1/strong/待办框/公式），而不只是"没报错"；
  · 断言**消毒真的生效**（注入的脚本不能执行、class/style 借不走应用样式）；
  · 断言**内容真的落库**（跨过 500ms 防抖后从后端读回来比对）。
"""
import base64
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_for_js
from test_frontend_modules import strip_code

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('markdown 回归',
                                   os.path.join(RENDERER, 'index.html'),
                                   js_api=ns['api'], width=1200, height=800)

    def runner():
        try:
            time.sleep(wait_before)
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


def _set_source(window, text):
    """通过 CodeMirror 实例写源码（等价于用户输入：会触发 change → 预览 + 防抖保存）"""
    window.evaluate_js(
        "(function(){var w=document.querySelector('.CodeMirror');"
        "if(!w||!w.CodeMirror){var t=document.getElementById('md-source');"
        "t.value=%s;t.dispatchEvent(new Event('input',{bubbles:true}));return 'textarea';}"
        "w.CodeMirror.setValue(%s);return 'codemirror';})()" % (json.dumps(text), json.dumps(text)))


def _preview_html(window):
    return window.evaluate_js("document.getElementById('md-preview').innerHTML") or ''


# ---------------- 静态守卫：vendor 资源必须在位 ----------------

def test_vendored_assets_exist_and_are_loaded():
    """Markdown 三个库是**自带离线文件**：少了任何一个，编辑器/预览/消毒就会静默降级。"""
    html = open(os.path.join(RENDERER, 'index.html'), encoding='utf-8').read()
    for rel in ('vendor/markdown-it.min.js', 'vendor/purify.min.js', 'vendor/codemirror.js',
                'vendor/codemirror-markdown.js', 'vendor/codemirror.css'):
        assert os.path.isfile(os.path.join(RENDERER, rel)), '%s 不在仓库里' % rel
        base = rel.split('/')[-1]
        assert base in html, '%s 没有被 index.html 引用（等于没生效）' % rel
    assert 'vendor/codemirror.css' in html, 'CodeMirror 样式必须静态 link（否则编辑器高度塌成 0）'


def test_third_party_libs_are_offline():
    """不能出现 CDN 引用：这个应用的核心承诺是离线可用。"""
    html = open(os.path.join(RENDERER, 'index.html'), encoding='utf-8').read()
    for bad in ('http://cdn', 'https://cdn', 'unpkg.com', 'jsdelivr.net'):
        assert bad not in html, 'index.html 里出现了外网引用：%s' % bad


def test_markdown_modules_are_pure_of_top_level_api_calls():
    """13/14 号模块不得在顶层调用桥接或别的模块函数。

    原因：03-notes ↔ 13-markdown-editor 是循环依赖，顶层调用会在模块初始化顺序上踩 TDZ。
    """
    for name in ('13-markdown-editor.js', '14-markdown-render.js'):
        src = strip_code(open(os.path.join(RENDERER, 'js', 'app', name), encoding='utf-8').read(),
                         blank_strings=True)
        head = src.split('export ')[0]
        assert 'pywebview' not in head, '%s 顶层不能碰桥接' % name


# ---------------- e2e：双轨分流 ----------------

@pytest.mark.e2e
def test_new_note_uses_markdown_editor(tmp_path, monkeypatch):
    """新建笔记 → 默认 Markdown：显示源码+预览双栏，隐藏 Quill 工具栏"""
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        window.evaluate_js("document.getElementById('btn-new-note').click();")
        time.sleep(2.5)
        result['format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['md_visible'] = window.evaluate_js(
            "!document.getElementById('md-editor').classList.contains('hidden')")
        result['quill_hidden'] = window.evaluate_js(
            "document.getElementById('quill-editor').classList.contains('hidden')")
        result['toolbar_hidden'] = window.evaluate_js(
            "!!document.querySelector('.ql-toolbar')?.classList.contains('hidden')")
        result['has_cm'] = window.evaluate_js(
            "!!(document.querySelector('.CodeMirror')||{}).CodeMirror")
        result['preview_present'] = window.evaluate_js(
            "!!document.getElementById('md-preview')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['format'] == 'md'
    assert r['md_visible'] and r['quill_hidden'] and r['toolbar_hidden']
    assert r['has_cm'], 'CodeMirror 实例没建起来（vendor 资源或初始化顺序有问题）'
    assert r['preview_present']


@pytest.mark.e2e
def test_typing_renders_preview_and_persists(tmp_path, monkeypatch):
    """输入 Markdown → 预览渲染 + 500ms 防抖后落库（重启后内容还在）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    body = ('# 标题一\n\n这是**粗体**和*斜体*。\n\n- [ ] 待办未做\n- [x] 待办已做\n\n'
            '> 引用一句\n\n```\ncode block\n```\n\n公式 $a^2+b^2=c^2$\n')

    def actions(window, result):
        _set_source(window, body)
        time.sleep(1.6)                              # 预览节流 180ms + 保存防抖 500ms
        html = _preview_html(window)
        # 第 9 轮起标题带锚点 id（`<h1 id="md-h-标题一">`），所以不能只匹配 `<h1>`
        result['h1'] = ('<h1' in html) and ('</h1>' in html)
        result['h1_anchor'] = 'id="md-h-' in html
        result['strong'] = '<strong>' in html
        result['task_done'] = 'md-task-done' in html
        result['blockquote'] = '<blockquote>' in html
        result['pre'] = '<pre>' in html
        result['katex'] = 'katex' in html
        result['preview_len'] = len(html)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    for key in ('h1', 'h1_anchor', 'strong', 'task_done', 'blockquote', 'pre', 'katex'):
        assert r[key], '预览缺少 %s 的渲染结果' % key
    stored = backend.api.notes_get(nid)['content']
    assert stored == body, '落库内容应与输入逐字符一致（防抖保存链路）'
    assert backend.api.notes_get(nid)['format'] == 'md'


@pytest.mark.e2e
def test_legacy_delta_note_still_uses_quill(tmp_path, monkeypatch):
    """双轨的另一半：历史 Delta 笔记必须继续走 Quill，不能被 Markdown 编辑器接管"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    backend.api.notes_update(nid, {
        'title': '老笔记',
        'content': json.dumps({'ops': [{'insert': '德尔塔正文，加粗：'}, {'insert': '粗',
                                 'attributes': {'bold': True}}, {'insert': '\n'}]},
                              ensure_ascii=False)})

    def actions(window, result):
        time.sleep(1.5)                              # 等启动自动选中
        result['format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['md_hidden'] = window.evaluate_js(
            "document.getElementById('md-editor').classList.contains('hidden')")
        result['quill_visible'] = window.evaluate_js(
            "!document.getElementById('quill-editor').classList.contains('hidden')")
        result['toolbar_visible'] = window.evaluate_js(
            "!document.querySelector('.ql-toolbar')?.classList.contains('hidden')")
        result['quill_text'] = window.evaluate_js(
            "document.querySelector('.ql-editor').innerText.trim()")
        result['bold'] = window.evaluate_js(
            "document.querySelectorAll('.ql-editor strong, .ql-editor b').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['format'] == 'delta'
    assert r['md_hidden'] and r['quill_visible'] and r['toolbar_visible']
    assert '德尔塔正文' in r['quill_text']
    assert r['bold'] >= 1, 'Delta 的行内格式必须还原出来'


@pytest.mark.e2e
def test_preview_sanitizes_user_html(tmp_path, monkeypatch):
    """内嵌 HTML 是用户内容：脚本/事件属性/借样式类都必须被挡掉"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()                       # 让启动时自动选中一篇
    payload = ('<script>window.__pwned = 1;</script>\n\n'
               '<img src=x onerror="window.__pwned = 2">\n\n'
               '<span style="position:fixed;top:0;left:0;color:#B8844A">彩色</span>\n\n'
               '<div class="panel-overlay">借样式</div>\n\n'
               '<a href="javascript:window.__pwned=3">点我</a>\n')

    def actions(window, result):
        _set_source(window, payload)
        time.sleep(1.2)
        html = _preview_html(window)
        result['pwned'] = window.evaluate_js("window.__pwned === undefined ? 'clean' : 'PWNED'")
        result['no_script'] = '<script' not in html.lower()
        result['no_onerror'] = 'onerror' not in html.lower()
        result['no_position'] = 'position' not in html.lower()
        result['no_borrowed_class'] = 'panel-overlay' not in html
        result['no_js_href'] = 'javascript:' not in html.lower()
        result['keeps_color'] = '#B8844A' in html or 'rgb(184, 132, 74)' in html

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['pwned'] == 'clean', '注入的脚本被执行了'
    for key in ('no_script', 'no_onerror', 'no_position', 'no_borrowed_class', 'no_js_href'):
        assert r[key], '%s 检查未通过' % key
    assert r['keeps_color'], '白名单内的排版样式（颜色）应当保留'


@pytest.mark.e2e
def test_preview_resolves_attachment_image(tmp_path, monkeypatch):
    """相对路径图片：预览不得去请求 http://127.0.0.1:<port>/attachments/...（那是渲染器目录）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    # 造一个真附件文件（1×1 透明 PNG，70 字节）
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==')
    att_dir = os.path.join(backend.ATTACH_DIR, nid)
    os.makedirs(att_dir, exist_ok=True)
    with open(os.path.join(att_dir, 'pic.png'), 'wb') as f:
        f.write(png)

    def actions(window, result):
        _set_source(window, '看图：![](attachments/%s/pic.png)\n' % nid)
        time.sleep(2.0)                              # 等异步 hydration
        result['src_kind'] = window.evaluate_js(
            "(function(){var i=document.querySelector('#md-preview img');if(!i)return 'noimg';"
            "var s=i.getAttribute('src')||'';return s.slice(0,5);})()")
        result['relative_src_left'] = window.evaluate_js(
            "!!document.querySelector('#md-preview img[src^=\"attachments\"]')")
        result['broken'] = window.evaluate_js(
            "!!document.querySelector('#md-preview img[data-md-broken]')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert not r['relative_src_left'], '预览里还留着相对路径 src（会 404）'
    assert r['src_kind'] == 'data:', '应换成 data URI，实际 src 前缀=%r' % r['src_kind']


# ---------------- e2e：公式排版与消毒边界 ----------------
#
# KaTeX 的输出绕开了 class/style 过滤（14-markdown-render.js 的 sanitizeKatex）。这一组钉住两件事：
# 公式的几何必须和 KaTeX 原始输出一致；放宽的只有 KaTeX 自己的输出，用户内容一条都没松。

# 必须与 renderMath 传给 KaTeX 的选项一致，否则比的不是同一个东西
_RAW_KATEX = ("katex.renderToString(%s, {displayMode: %s, "
              "throwOnError: false, trust: false, strict: 'ignore'})")

_GEOMETRY_CASES = [
    r'\frac{a}{b}',
    r'\sqrt{x}',
    r'x_i^2',
    r'\frac{a^2+b}{\sqrt{c}} = \sum_{i=1}^{n} x_i',
]

# 把 KaTeX 原始输出放进同一个预览容器再量：字号、行高、全局样式都相同，差别只可能来自消毒。
# 不用 iframe 做对照（CSP 的 frame-src 'none'），也不用 .base 这类类名（KaTeX 0.18 起改了前缀）；
# 尺寸取 .katex-html 子元素的并集，因为块级公式的 .katex-html 本身撑满容器，量不出公式多大。
_GEOMETRY_JS = r"""
(function (cases) {
  var preview = document.getElementById('md-preview');
  var rendered = preview.querySelectorAll('.katex');
  function rect(el) { return el.getBoundingClientRect(); }
  function size(html) {
    var rects = Array.prototype.map.call(html.children, rect);
    return [Math.max.apply(null, rects.map(function (b) { return b.right; }))
              - Math.min.apply(null, rects.map(function (b) { return b.left; })),
            Math.max.apply(null, rects.map(function (b) { return b.bottom; }))
              - Math.min.apply(null, rects.map(function (b) { return b.top; }))];
  }
  function boxes(html) {
    var o = rect(html);
    return Array.prototype.map.call(html.querySelectorAll('*'), function (el) {
      var r = rect(el);
      return [r.left - o.left, r.top - o.top, r.width, r.height];
    });
  }
  var out = cases.map(function (c, i) {
    var probe = document.createElement('p');
    probe.innerHTML = c.raw;
    preview.appendChild(probe);
    var html = rendered[i].querySelector('.katex-html'), raw = probe.querySelector('.katex-html');
    var a = boxes(html), b = boxes(raw), worst = 0;
    if (a.length === b.length) {
      a.forEach(function (box, n) {
        box.forEach(function (v, k) { worst = Math.max(worst, Math.abs(v - b[n][k])); });
      });
    }
    var item = {tex: c.tex, display: c.display, size: size(html), rawSize: size(raw),
                nodes: a.length, rawNodes: b.length, worst: worst};
    probe.remove();
    return item;
  });
  return JSON.stringify({count: rendered.length, formulas: out});
})(%s)
"""

# 不靠对照的绝对判断：分子在分母上方、上标比底字高且小、根号真的画出来了。
# 对照只能证明"和原始输出一样"，全局样式把两边一起弄坏时它是发现不了的
_ANATOMY_JS = r"""
(function (texts) {
  function find(html, text) {
    return Array.prototype.filter.call(html.querySelectorAll('span'), function (el) {
      return !el.children.length && el.textContent === text;
    })[0];
  }
  function cy(el) { var r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; }
  function hh(el) { return el.getBoundingClientRect().height; }
  var all = document.querySelectorAll('#md-preview .katex-html');
  return JSON.stringify(Array.prototype.map.call(all, function (html, i) {
    var res = {tex: texts[i]};
    if (texts[i] === '\\frac{a}{b}') {
      var a = find(html, 'a'), b = find(html, 'b');
      res.ok = !!(a && b) && cy(b) - cy(a) > hh(a) * 0.8;
    } else if (texts[i] === '\\sqrt{x}') {
      var x = find(html, 'x'), svg = html.querySelector('svg');
      var clip = svg && svg.parentElement.getBoundingClientRect();
      res.ok = !!(x && svg && svg.querySelector('path') && clip.height > 0)
        && clip.width >= x.getBoundingClientRect().width
        && clip.right <= html.getBoundingClientRect().right + 1;   // 400em 的 SVG 要被裁住
    } else if (texts[i] === 'x_i^2') {
      var base = find(html, 'x'), sub = find(html, 'i'), sup = find(html, '2');
      res.ok = !!(base && sub && sup) && cy(sup) < cy(base) && cy(sub) > cy(base)
        && hh(sup) < hh(base) && hh(sub) < hh(base);
    } else {
      res.skip = true;
    }
    return res;
  }));
})(%s)
"""


def _wait_editor_ready(window):
    """等应用选中笔记、Markdown 编辑器就绪（选中之前写的源码会被切换笔记的写入盖掉）"""
    wait_for_js(window, "!!(window.__app && window.__app.state.activeNoteId"
                        " && !window.__app.state.isLoading"
                        " && window.__app.state.noteFormat === 'md'"
                        " && window.katex && window.DOMPurify"
                        " && (document.querySelector('.CodeMirror') || {}).CodeMirror)", timeout=40)


@pytest.mark.e2e
def test_math_geometry_matches_raw_katex(tmp_path, monkeypatch):
    """分式、根号、上下标的几何必须与 KaTeX 原始输出一致。

    `'katex' in html` 抓不到这个问题：曾经的消毒把 mord、vlist-t 这些 class 和 height、top
    这些内联样式全滤掉、把根号的 SVG 删掉，公式散成一行，而 HTML 里 katex 字样一个不少。
    所以这里逐元素比位置和大小，行内与块级都比。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    cases = [(tex, display) for tex in _GEOMETRY_CASES for display in (False, True)]
    source = ''.join(('$$%s$$\n\n' if display else '$%s$\n\n') % tex for tex, display in cases)

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "document.querySelectorAll('#md-preview .katex').length", len(cases))
        payload = [{'tex': tex, 'display': display,
                    'raw': window.evaluate_js(_RAW_KATEX % (json.dumps(tex), json.dumps(display)))}
                   for tex, display in cases]
        result['geometry'] = json.loads(window.evaluate_js(_GEOMETRY_JS % json.dumps(payload)))
        texts = [tex for tex, _ in cases]
        result['anatomy'] = json.loads(window.evaluate_js(_ANATOMY_JS % json.dumps(texts)))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['geometry']['count'] == len(cases), '预览里的公式数量不对'
    for f in r['geometry']['formulas']:
        name = '%s（%s）' % (f['tex'], '块级' if f['display'] else '行内')
        assert f['size'] == pytest.approx(f['rawSize'], abs=0.5), \
            '%s 消毒后尺寸 %s，KaTeX 原始输出 %s' % (name, f['size'], f['rawSize'])
        assert f['nodes'] == f['rawNodes'], '%s 消毒吃掉了元素' % name
        assert f['worst'] <= 0.5, '%s 内部元素错位 %.2fpx' % (name, f['worst'])
    for item in r['anatomy']:
        assert item.get('skip') or item['ok'], '%s 的结构不对：%r' % (item['tex'], item)


@pytest.mark.e2e
def test_math_markup_survives_sanitizer_intact(tmp_path, monkeypatch):
    """KaTeX 的白名单要够用：各类命令消毒后的 DOM 必须与原始输出逐节点相同。

    白名单是手写的，少一个标签或属性不会报错，只会让某一类公式悄悄坏掉。
    SVG（可伸缩符号、高括号）、<line>（\\cancel）、内联颜色与尺寸、MathML、语法错误都要过一遍。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    samples = [
        r'\sqrt[3]{x}', r'\sum_{i=1}^{n} x_i', r'\int_0^1 f', r'\lim_{x\to 0} y',
        r'\left(\frac{\frac{a}{b}}{c}\right)', r'\begin{pmatrix}a&b\\c&d\end{pmatrix}',
        r'\begin{array}{c|c}a&b\\\hline c&d\end{array}', r'\begin{cases}a&b\\c&d\end{cases}',
        r'\overbrace{a+b}^{n}', r'\widehat{abc}', r'\overrightarrow{AB}', r'\xrightarrow[b]{a}',
        r'\cancel{a}', r'\xcancel{a}', r'\sout{abc}', r'\boxed{a}', r'\fcolorbox{red}{yellow}{a}',
        r'\colorbox{yellow}{a}', r'\textcolor{#ff0000}{a}', r'\rule{2em}{1em}',
        r'\raisebox{0.5em}{a}', r'\kern1em a', r'\mathllap{a}b', r'\phantom{a}b', r'\pmb{x}',
        r'\mathbb{R}\mathcal{L}\mathfrak{g}', r'\text{中文 text}', r'\Huge a', r'a<b',
        r'\frac{',                                   # 语法错误走 katex-error 分支
    ]
    source = ''.join('$%s$\n\n' % tex for tex in samples)

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        selector = "document.querySelectorAll('#md-preview .katex, #md-preview .katex-error')"
        wait_for_js(window, selector + ".length", len(samples))
        raw = [window.evaluate_js(_RAW_KATEX % (json.dumps(tex), 'false')) for tex in samples]
        result['diff'] = json.loads(window.evaluate_js(
            "(function (raw) {"
            "  var nodes = %s, holder = document.createElement('div'), bad = [];"
            "  raw.forEach(function (html, i) {"
            "    holder.innerHTML = html;"
            "    if (!nodes[i] || !nodes[i].isEqualNode(holder.firstElementChild)) bad.push(i);"
            "  });"
            "  return JSON.stringify({count: nodes.length, bad: bad});"
            "})(%s)" % (selector, json.dumps(raw))))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['diff']['count'] == len(samples)
    assert not r['diff']['bad'], '消毒改动了这些公式的 DOM：%r' % [
        samples[i] for i in r['diff']['bad']]


@pytest.mark.e2e
def test_math_commands_and_user_html_stay_blocked(tmp_path, monkeypatch):
    """放宽的只有 KaTeX 自己的输出：公式里写不进 class/style/链接，公式外的内嵌 HTML 照旧被挡。

    断言看 DOM 不看字符串：被拒绝的 `\\href{javascript:...}` 会作为文字留在预览和
    <annotation> 里，这是对的，有害的是它变成真的链接。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    # 公式里不能写 window.__pwned：`__` 在数学模式是语法错误，走不到 KaTeX 的正常输出
    payload = '\n\n'.join([
        r'$\href{javascript:window.pwned=1}{点我}$',
        r'$\url{javascript:window.pwned=2}$',
        r'$\htmlClass{panel-overlay}{借样式}$',
        r'$\htmlStyle{position:fixed;top:0}{盖住界面}$',
        r'$\htmlId{editor-status}{顶掉状态栏}$',
        r'$\htmlData{foo=bar}{带数据}$',
        r'$\includegraphics{attachments/x.png}$',
        r'$\textcolor{red;position:fixed}{借颜色写样式}$',
        r'$<img src=x onerror=window.pwned=3>$',
        r'$\text{<img src=x onerror=window.pwned=4>}$',
        # 公式外的内嵌 HTML。冒充公式的两条：放宽要是按"在 .katex 里就跳过"来判断就会中招
        '<div class="panel-overlay">借样式</div>',
        '<span class="katex panel-overlay" style="position:fixed;top:0">冒充公式</span>',
        '<span class="katex"><span class="katex-html"><span class="panel-overlay vlist-t"'
        ' style="position:fixed;height:9em">套在公式壳子里</span></span></span>',
        '<svg onload="window.pwned=5"><path d="M0 0"></path></svg>',
        '<script>window.pwned=6</script>',
        '<img src="data:," onerror="window.pwned=7">',
        # `$` 落在属性值里：不能借公式逃出属性（占位符的落点见下一组）
        '<span title="$x$ onmouseover=window.pwned=8 $y$">属性里的公式</span>',
        '<span title="$\\frac{ onmouseover=window.pwned=9 $">属性里的语法错误</span>',
    ]) + '\n'

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, payload)
        wait_for_js(window, "!!document.querySelector('#md-preview .katex')")
        result.update(json.loads(window.evaluate_js(
            "(function () {"
            "  var p = document.getElementById('md-preview');"
            "  var all = Array.prototype.slice.call(p.querySelectorAll('*'));"
            "  function attrs(pattern) {"
            "    return all.filter(function (el) {"
            "      return el.getAttributeNames().some(function (n) { return pattern.test(n); });"
            "    }).length;"
            "  }"
            "  function kept(text) {"                  # 用户写的那个元素还留着什么 class 和 style
            "    var el = all.filter(function (e) {"
            "      return !e.children.length && e.textContent === text; })[0];"
            "    return el ? [el.className, el.getAttribute('style')] : null;"
            "  }"
            "  return JSON.stringify({"
            "    links: p.querySelectorAll('a').length,"
            "    borrowed: p.querySelectorAll('.panel-overlay').length,"
            "    lookalike: [kept('冒充公式'), kept('套在公式壳子里')],"
            "    fixed: all.filter(function (el) {"
            "      return getComputedStyle(el).position === 'fixed'; }).length,"
            "    ids: attrs(/^id$/), events: attrs(/^on/i), data: attrs(/^data-foo/),"
            "    svg: p.querySelectorAll('svg').length, script: p.querySelectorAll('script').length,"
            "    formulaImg: p.querySelectorAll('.katex img').length,"
            "    refused: p.textContent.indexOf('\\\\href{javascript:') >= 0,"
            "    pwned: window.pwned === undefined ? 'clean' : 'PWNED'"
            "  });"
            "})()")))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['pwned'] == 'clean', '注入的脚本被执行了'
    assert r['refused'], '\\href 应当被 KaTeX 拒绝并原样显示成文字'
    assert r['links'] == 0, '公式里的 \\href / \\url 变成了真链接'
    assert r['borrowed'] == 0, '用户内容借到了应用自己的样式类'
    assert r['lookalike'] == [['katex', None], ['', None]], \
        '冒充公式的用户内容逃过了 class/style 过滤：%r' % r['lookalike']
    assert r['fixed'] == 0, '用户写的内联样式没被过滤'
    assert r['ids'] == 0, '\\htmlId 写进了 id（能顶掉 getElementById 的查找）'
    assert r['data'] == 0, '\\htmlData 写进了 data 属性'
    assert r['events'] == 0 and r['script'] == 0
    assert r['formulaImg'] == 0, '公式里的 \\includegraphics 或 HTML 变成了真图片'
    assert r['svg'] == 0, '用户写的 <svg> 不能进预览：放行 SVG 的只有 KaTeX 那条路'


# ---------------- e2e：`$` 落在正文以外的地方 ----------------
#
# 公式在 Markdown 解析之前就换成占位符，解析完才知道它落在哪：落在正文里才是公式，
# 属性和代码里的 `$` 是文字。这一组钉住"落点"，消毒规则本身见上一组。

@pytest.mark.e2e
def test_math_in_attributes_stays_source_text(tmp_path, monkeypatch):
    """属性和链接里的 `$…$` 是文字。

    占位符曾在消毒之后按字符串替换：KaTeX 的 HTML 拼进属性值，DOM 乱掉，
    多出名为 `katex"` 的属性，页面上露出 `">`。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    cases = [   # (源码, 选择器, 属性, 期望值)
        ('<span class="md-a" title="$x$">内嵌 HTML</span>', '.md-a', 'title', '$x$'),
        ('<span class="md-b" title="$$y$$">块级定界符</span>', '.md-b', 'title', '$$y$$'),
        ('<span class="md-c" title="\\(x\\)">别的定界符</span>', '.md-c', 'title', '\\(x\\)'),
        ('<span class="md-d" title="$a &amp; b$">实体</span>', '.md-d', 'title', '$a & b$'),
        ('<span class="md-e" title="单价 $5">配不成对</span>，原价 $6', '.md-e', 'title', '单价 $5'),
        ('<div class="md-f" title="$x$" bad$>markdown-it 不认的标签</div>', '.md-f', 'title', '$x$'),
        ('<a href="https://a.b/$x$">内嵌链接</a>', 'a[href*="a.b"]', 'href', 'https://a.b/$x$'),
        ('[链接](https://c.d/$x$)', 'a[href*="c.d"]', 'href', 'https://c.d/$x$'),
        ('[块级](https://e.f/$$x$$)', 'a[href*="e.f"]', 'href', 'https://e.f/$$x$$'),
        ('[单价](https://g.h/?p=$1)，原价 $6', 'a[href*="g.h"]', 'href', 'https://g.h/?p=$1'),
        ('[标题](https://i.j "提示 $y$")', 'a[href*="i.j"]', 'title', '提示 $y$'),
        ('<https://k.l/$x$>', 'a[href*="k.l"]', 'href', 'https://k.l/$x$'),
        ('[引用][r]\n\n[r]: https://m.n/$x$', 'a[href*="m.n"]', 'href', 'https://m.n/$x$'),
        ('![图 $x$](https://o.p/$y$.png)', 'img', 'alt', '图 $x$'),
        ('![图](https://q.r/$y$.png)', 'img[src*="q.r"]', 'src', 'https://q.r/$y$.png'),
        ('![图 $a<b>c$](https://u.v/p.png)', 'img[src*="u.v"]', 'alt', '图 $a<b>c$'),
        ('[[$x$]]', 'a[data-wikilink]', 'data-wikilink', '$x$'),
        ('## 小节 $x$', 'h2', 'id', 'md-h-小节-x'),       # 锚点要和 [[标题#小节 $x$]] 算出来的一样
    ]
    source = '\n\n'.join([c[0] for c in cases] + [
        '<span class="md-ok">$c^2$</span>',               # 对照：正文里的公式照常渲染
        '[$k$](https://s.t/)',                            # 链接文字是正文，地址才是属性
    ]) + '\n'

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "!!document.querySelector('#md-preview .md-ok .katex')")
        result.update(json.loads(window.evaluate_js(
            "(function (cases) {"
            "  var p = document.getElementById('md-preview'), names = {};"
            "  p.querySelectorAll('*').forEach(function (el) {"
            "    el.getAttributeNames().forEach(function (n) { names[n] = 1; });"
            "  });"
            "  return JSON.stringify({"
            "    values: cases.map(function (c) {"
            "      var el = p.querySelector(c[0]);"
            "      return el ? el.getAttribute(c[1]) : 'MISSING';"
            "    }),"
            "    names: Object.keys(names), html: p.innerHTML, text: p.textContent,"
            "    linked: p.querySelectorAll('a[href*=\"s.t\"] .katex').length,"
            "    autolink: p.querySelector('a[href*=\"k.l\"]').textContent"
            "  });"
            "})(%s)" % json.dumps([[c[1], c[2]] for c in cases]))))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    for (src, _selector, attr, want), got in zip(cases, r['values'], strict=True):
        assert got == want, '%s 的 %s 应是 %r，实际 %r' % (src, attr, want, got)
    assert not [n for n in r['names'] if '"' in n], 'KaTeX 的 HTML 拼进了属性：%r' % r['names']
    assert '">' not in r['text'], '属性的结尾露到了页面上：%r' % r['text']
    assert 'mdmath' not in r['html'].lower(), '占位符漏进了预览：%r' % r['html']
    assert r['linked'] == 1, '链接文字里的公式应当照常渲染'
    assert r['autolink'] == 'https://k.l/$x$', '自动链接的文字就是地址本身，不是公式'


@pytest.mark.e2e
def test_math_in_code_stays_source_text(tmp_path, monkeypatch):
    """代码里的 `$` 是字面量。shell 脚本和 `$HOME`、`$PATH` 这样的说明文字都要逐字保留。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    source = '\n\n'.join([
        '行内 `$x$`，块级 `$$y$$`，双反引号 ``$$z$$``，两段配对的 `$HOME` 和 `$PATH`',
        '多行的行内代码 `$a$\n和 $b$` 也是代码',
        '```\n围栏 $a$ 和 $$b$$\n\n空行之后 $c$\n```',
        '````\n四个反引号 $a$\n```\n$b$ 不是结束\n```\n````',
        '```\n```后面有字 $$a$$ 也不是结束\n```',
        '```java\n@Test $a$ 和 $$b$$\n```',            # hljs 按 `@` 切 token，占位符进去就被拆开了
        '```sh\necho $HOME $PATH\n```',
        '~~~\n波浪线围栏 $a$\n~~~',
        '> ```\n> 引用里的围栏 $a$\n>\n> $$b$$\n> ```',
        '    缩进代码 $a$ 和 $b$',                       # 放在列表前面：列表后面的缩进是列表内容
        '- ```\n  列表里的围栏 $a$\n  ```',
        '<code>内嵌 code $x$</code>',
        '<pre>内嵌 pre $y$</pre>',
        '<math><mi>$x$</mi></math>',                    # MathML 里放不了 KaTeX 的 HTML
        '```sh\necho $$\n```',
        '两段代码中间的公式 $c^2$',                     # 上下两个 `$$` 不能跨过它配成一对
        '```sh\nkill $$\n```',
        '```java\n@Test 没闭合的围栏 $a$ 和 $b$',        # 正在输入时的样子
    ])
    want = [
        '$x$', '$$y$$', '$$z$$', '$HOME', '$PATH',
        '$a$ 和 $b$',
        '围栏 $a$ 和 $$b$$\n\n空行之后 $c$',
        '四个反引号 $a$\n```\n$b$ 不是结束\n```',
        '```后面有字 $$a$$ 也不是结束',
        '@Test $a$ 和 $$b$$',
        'echo $HOME $PATH',
        '波浪线围栏 $a$',
        '引用里的围栏 $a$\n\n$$b$$',
        '缩进代码 $a$ 和 $b$',
        '列表里的围栏 $a$',
        '内嵌 code $x$',
        '内嵌 pre $y$',
        'echo $$',
        'kill $$',
        '@Test 没闭合的围栏 $a$ 和 $b$',
    ]

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "!!document.querySelector('#md-preview .katex')")
        result.update(json.loads(window.evaluate_js(
            "(function () {"
            "  var p = document.getElementById('md-preview');"
            "  var code = p.querySelectorAll('pre, :not(pre) > code');"
            "  return JSON.stringify({"
            "    code: Array.prototype.map.call(code, function (el) { return el.textContent; }),"
            "    inCode: p.querySelectorAll('pre .katex, code .katex').length,"
            "    mathml: p.querySelector('math mi').textContent,"
            "    formulas: p.querySelectorAll('.katex').length"
            "  });"
            "})()")))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert [t.rstrip('\n') for t in r['code']] == want
    assert r['inCode'] == 0, '代码里出现了公式'
    assert r['mathml'] == '$x$', '用户写的 MathML 里出现了占位符或公式'
    assert r['formulas'] == 1, '代码之外的公式应当照常渲染，且只有这一个'


@pytest.mark.e2e
def test_math_source_cannot_smuggle_values_past_sanitizer(tmp_path, monkeypatch):
    """属性里的占位符必须在消毒**之前**换回原文：过滤器要检查最终的值。

    公式原文能带分号和空格。消毒之后才换的话，过滤器看到的只是一个占位符，
    style 白名单、class 前缀和 DOMPurify 自己的属性检查都被绕开。

    这些标签都带一个 markdown-it 不认的属性名（`bad$`）：认得的标签在抽公式时就跳过了，
    不认的才会让占位符真的进到属性里（HTML 块原样放行，浏览器解析时又是容错的）。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    source = '\n\n'.join([
        '<div class="md-a" style="color: $red; position: fixed; top: 0; x: $" bad$>绕 style</div>',
        '<div title="绕 class" class="md-$ panel-overlay $" bad$>绕 class</div>',
        '<div class="md-b" title="$--><img src=x onerror=window.pwned=1>$" bad$>绕属性检查</div>',
        '<div><a class="md-c" href="java$x$script:window.pwned=2" bad$>绕协议</a></div>',
        '<div><a class="md-d" href="$x$" bad$>占位符当协议</a></div>',
        '<span class="md-ok">$c^2$</span>',
    ]) + '\n'

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "!!document.querySelector('#md-preview .md-ok .katex')")
        result.update(json.loads(window.evaluate_js(
            "(function () {"
            "  var p = document.getElementById('md-preview');"
            "  function attr(selector, name) {"
            "    var el = p.querySelector(selector);"
            "    return el ? el.getAttribute(name) : 'MISSING';"
            "  }"
            "  return JSON.stringify({"
            "    style: attr('.md-a', 'style'),"
            "    position: (function (el) { return el ? getComputedStyle(el).position : 'MISSING'; })"
            "      (p.querySelector('.md-a')),"
            "    className: attr('[title=\"绕 class\"]', 'class'),"
            "    borrowed: p.querySelectorAll('.panel-overlay').length,"
            "    title: attr('.md-b', 'title'),"
            "    href: [attr('.md-c', 'href'), attr('.md-d', 'href')],"
            "    img: p.querySelectorAll('img').length,"
            "    pwned: window.pwned === undefined ? 'clean' : 'PWNED'"
            "  });"
            "})()")))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['pwned'] == 'clean', '注入的脚本被执行了'
    assert r['position'] == 'static', '公式原文带着 position 绕过了 style 白名单：%r' % r['style']
    assert r['borrowed'] == 0, '公式原文带着空格绕过了 class 前缀：%r' % r['className']
    assert r['title'] is None, '属性值含 `-->`，DOMPurify 看到真实的值就会删掉它'
    assert r['href'] == [None, None], '消毒要拿真实的地址判断协议'
    assert r['img'] == 0


@pytest.mark.e2e
def test_literal_regions_do_not_swallow_formulas(tmp_path, monkeypatch):
    """反过来：认"字面量"认过了头，公式就不渲染了。这里都是长得像代码、标签、链接的公式，
    以及公式靠抽占位符才保住的写法（表格里的 `|`、HTML 块里的公式、加粗和链接文字）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    cases = [   # (源码, KaTeX 收到的公式)
        ('$a<b$ 和 $c>d$', ['a<b', 'c>d']),                   # 中间那段像标签
        ('$a<b>c$', ['a<b>c']),
        ('$f[1](x)$', ['f[1](x)']),                           # 像链接地址
        ('\\[E=mc^2\\](1)', ['E=mc^2']),                      # `\]` 不是链接的右括号
        ('$\\text{``引号}$', ['\\text{``引号}']),              # 公式里的反引号
        ('`代码` $e$ `代码`', ['e']),
        ('\\`$v$\\`', ['v']),                                  # 转义的反引号不是行内代码
        ('$a~~~b$', ['a~~~b']),                                # `~~~` 在公式里是空格，不是围栏
        ('$w$', ['w']),
        ('$c~~~d$', ['c~~~d']),
        ('行中的 ``` 不是围栏', []),
        ('$x$', ['x']),
        ('行中的 ``` 也不是', []),
        ('$g$ 和 `$h$` 和 $i$', ['g', 'i']),
        ('\\(j\\) 和 `\\(k\\)`', ['j']),
        ('| 表头 |\n|---|\n| $|l|$ |', ['|l|']),
        ('<div>$m$</div>', ['m']),
        ('<details><summary>$n$</summary>$o$</details>', ['n', 'o']),
        ('<span style="color: red">$p$</span>', ['p']),
        ('**$q$** 和 [$r$](https://a.b/)', ['q', 'r']),
        ('$$\\int s$$', ['\\int s']),
        ('<!-- 注释 -->$t$', ['t']),                           # 文字直接落在顶层，没有父元素
    ]
    # 序号不存在的占位符是用户自己写的字
    source = '\n\n'.join(c[0] for c in cases) + '\n\n正文 @@MDMATH99@@，代码 ``@@MDMATH98@@``\n'
    want = [tex for _src, group in cases for tex in group]

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "document.querySelectorAll('#md-preview .katex').length", len(want))
        result.update(json.loads(window.evaluate_js(
            "(function () {"
            "  var p = document.getElementById('md-preview');"
            "  return JSON.stringify({"
            "    tex: Array.prototype.map.call(p.querySelectorAll('.katex-mathml annotation'),"
            "      function (el) { return el.textContent; }),"
            "    text: p.textContent"
            "  });"
            "})()")))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['tex'] == want
    assert '@@MDMATH99@@' in r['text'] and '@@MDMATH98@@' in r['text'], '用户自己写的字被删掉了'


@pytest.mark.e2e
def test_katex_output_still_goes_through_dompurify(tmp_path, monkeypatch):
    """绕开的只是 class/style 过滤，不是消毒：KaTeX 吐出什么都要再过一遍白名单。

    trust: false 时 KaTeX 正常不会输出这些，所以这里把它换成一个恶意的输出，
    模拟库本身的漏洞或者 trust 被改掉。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    hostile = ('<span class="katex mord" style="height:1em" onmouseover="window.__pwned=1">'
               '<script>window.__pwned=2</script>'
               '<img src="data:," onerror="window.__pwned=3">'
               '<a href="javascript:window.__pwned=4">链接文字</a>'
               '<span id="editor-status" data-foo="1">留下的文字</span>'
               '<svg onload="window.__pwned=5"><use href="#x"></use>'
               '<path d="M0 0" onclick="window.__pwned=6"></path>'
               '<foreignObject><iframe src="javascript:window.__pwned=7"></iframe></foreignObject>'
               '</svg></span>')

    def actions(window, result):
        _wait_editor_ready(window)
        window.evaluate_js("window.katex.renderToString = function () { return %s; };"
                           % json.dumps(hostile))
        _set_source(window, '$x$\n')
        wait_for_js(window, "!!document.querySelector('#md-preview .katex')")
        result.update(json.loads(window.evaluate_js(
            "(function () {"
            "  var k = document.querySelector('#md-preview .katex');"
            "  var all = [k].concat(Array.prototype.slice.call(k.querySelectorAll('*')));"
            "  return JSON.stringify({"
            "    className: k.className, style: k.getAttribute('style'),"
            "    text: k.textContent, path: k.querySelectorAll('path').length,"
            "    bad: k.querySelectorAll('script, img, a, use, foreignObject, iframe').length,"
            "    attrs: all.reduce(function (acc, el) {"
            "      return acc.concat(el.getAttributeNames()); }, [])"
            "  });"
            "})()")))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert r['className'] == 'katex mord' and r['style'] == 'height:1em', \
        'KaTeX 这条路应当保留 class 和 style'
    assert '留下的文字' in r['text'] and r['path'] == 1
    assert r['bad'] == 0, 'KaTeX 这条路放过了白名单外的标签'
    leaked = [a for a in r['attrs'] if a.startswith(('on', 'data-')) or a in ('id', 'href', 'src')]
    assert not leaked, 'KaTeX 这条路放过了白名单外的属性：%r' % leaked


@pytest.mark.e2e
def test_exported_formulas_do_not_carry_katex_svg(tmp_path, monkeypatch):
    """导出和预览共用渲染入口，但导出文件没带 KaTeX 的样式表，所以 SVG 不能跟过去。

    根号的 `<svg width="400em">` 没了裁切，HTML 导出会被撑到 6400px 宽；
    DOCX 的分块正则还会把 `<path` 当成 `<p` 切开，吞掉根号后面的文字。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    source = '段尾公式 $\\sqrt{c}+d$\n\n$$\\frac{a}{\\sqrt{b}}$$\n'
    html_path = str(tmp_path / 'formula.html')
    docx_path = str(tmp_path / 'formula.docx')

    def actions(window, result):
        _wait_editor_ready(window)
        # 动态 import 拿到的是应用正在用的同一个模块实例
        window.evaluate_js(
            "(function () {"
            "  import('./js/app/14-markdown-render.js')"
            "    .then(function (m) { return m.markdownExportHtml(%s, null); })"
            "    .then(function (html) { window.__exported = html; },"
            "          function (e) { window.__exported = 'ERROR ' + e; });"
            "  return 1;"
            "})()" % json.dumps(source))
        wait_for_js(window, "typeof window.__exported === 'string'")
        html = window.evaluate_js("window.__exported")
        result['html'] = html
        backend.api.export_note('公式', html, 'html', html_path)
        backend.api.export_note('公式', html, 'docx', docx_path)
        window.load_url('file:///' + html_path.replace('\\', '/'))
        wait_for_js(window, "location.protocol === 'file:' && !!document.querySelector('math')")
        result['page_width'] = window.evaluate_js("document.documentElement.scrollWidth")
        result['view_width'] = window.evaluate_js("window.innerWidth")

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    assert '<math' in r['html'], '导出里的公式靠 MathML 显示，不能一起丢掉'
    assert '<svg' not in r['html'], 'KaTeX 的 SVG 跟进了导出'
    assert r['page_width'] <= r['view_width'], '导出的 HTML 被撑宽了：%r' % r['page_width']
    from docx import Document
    text = '\n'.join(p.text for p in Document(docx_path).paragraphs)
    assert '段尾公式' in text and '+d' in text, 'DOCX 丢了公式后半段的文字：%r' % text


# ---------------- e2e：块级公式落在容器里 ----------------
#
# `$$` 换成占位符时曾经前后都垫空行，好让它自成一段。可空行在 Markdown 里是"这一块到此为止"，
# 落在列表、引用、表格、缩进代码里就把它们拦腰截断。现在只有从行首开始的 `$$` 才垫。

# 预览的骨架，一个顶层块一项：元素写成 `标签(内容)`，块级公式写成 `{{tex}}`，行内公式写成 `{tex}`。
# 代码里的 `$$x$$` 是文字，原样出现，所以和渲染出来的公式分得开
_SKELETON_JS = r"""
(function () {
  function tex(el) { return (el.querySelector('annotation') || el).textContent.trim(); }
  function sk(node) {
    if (node.nodeType === 3) return node.nodeValue.trim();
    if (node.nodeType !== 1) return '';
    if (node.classList.contains('katex-display')) return '{{' + tex(node) + '}}';
    if (node.classList.contains('katex')) return '{' + tex(node) + '}';
    var inner = Array.prototype.map.call(node.childNodes, sk).filter(Boolean).join(' ');
    return node.tagName.toLowerCase() + '(' + inner + ')';
  }
  var p = document.getElementById('md-preview');
  return JSON.stringify(Array.prototype.map.call(p.childNodes, sk).filter(Boolean));
})()
"""


def _preview_skeleton(tmp_path, monkeypatch, cases):
    """cases 是 (源码, [顶层块的骨架…])。用 HTML 注释隔开：同种列表之间只隔空行会并成一个列表"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    source = '\n\n<!-- -->\n\n'.join(c[0] for c in cases) + '\n'
    count = sum(block.count('{{') for c in cases for block in c[1])    # 这里的公式都是块级的

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "document.querySelectorAll('#md-preview .katex').length", count)
        result['skeleton'] = json.loads(window.evaluate_js(_SKELETON_JS))

    r = _run(ns, actions, wait_before=0)
    assert 'error' not in r, r.get('error')
    return r['skeleton']


@pytest.mark.e2e
def test_block_math_stays_inside_containers(tmp_path, monkeypatch):
    """不从行首开始的 `$$` 留在原地：列表、引用、表格、缩进代码都不能被拆开。

    缩进代码里的 `$$` 是文字。原来垫的空行把那一行变成了空行，代码块没了，公式露在外面。
    """
    cases = [
        ('- 列表 $$a$$\n- $$b$$\n- 第三项',
         ['ul(li(列表 {{a}}) li({{b}}) li(第三项))']),
        ('- 跨行\n  $$\n  c\n  $$\n- 下一项',                    # 缩进是列表项的，不是公式的
         ['ul(li(跨行 {{c}}) li(下一项))']),
        ('- 第一段\n\n  $$d$$',                                  # 列表项的第二段从缩进开始
         ['ul(li(p(第一段) p({{d}})))']),
        ('> 引用 $$e$$\n> 下一行',
         ['blockquote(p(引用 {{e}} 下一行))']),
        ('| 表头 |\n|---|\n| $$f$$ |\n| 下一行 |',
         ['table(thead(tr(th(表头))) tbody(tr(td({{f}})) tr(td(下一行))))']),
        ('    $$g$$\n    缩进代码 $$h$$',
         ['pre(code($$g$$\n缩进代码 $$h$$))']),
    ]
    want = [block for c in cases for block in c[1]]
    assert _preview_skeleton(tmp_path, monkeypatch, cases) == want


@pytest.mark.e2e
def test_block_math_at_line_start_still_stands_alone(tmp_path, monkeypatch):
    """从行首开始的 `$$` 照旧自成一段，没有空行也一样。

    不垫空行的话，表格下一行的公式会变成表格的一行，段落下一行的会并进那一段。
    """
    cases = [
        ('| 表头 |\n|---|\n| 格子 |\n$$x$$',
         ['table(thead(tr(th(表头))) tbody(tr(td(格子))))', 'p({{x}})']),
        ('段落\n$$y$$\n段落',
         ['p(段落)', 'p({{y}})', 'p(段落)']),
    ]
    want = [block for c in cases for block in c[1]]
    assert _preview_skeleton(tmp_path, monkeypatch, cases) == want
