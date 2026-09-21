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
from conftest import PROJECT_ROOT, load_app_partial
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
        result['h1'] = '<h1>' in html
        result['strong'] = '<strong>' in html
        result['task_done'] = 'md-task-done' in html
        result['blockquote'] = '<blockquote>' in html
        result['pre'] = '<pre>' in html
        result['katex'] = 'katex' in html
        result['preview_len'] = len(html)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    for key in ('h1', 'strong', 'task_done', 'blockquote', 'pre', 'katex'):
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
