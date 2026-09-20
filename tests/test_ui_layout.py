# -*- coding: utf-8 -*-
"""UI 回归：窄窗不裁切 / 列表摘要与搜索高亮 / 骨架屏 / 深色主题。

分两层：
  · 不标 e2e 的静态检查（默认单测就跑）：防止主题变量、骨架屏标记被误删；
  · 标 e2e 的实机测量：用无头 pywebview 真跑一遍，断言**算出来的像素**没问题
    （上一轮就是靠这种方式量化出「900px 窗口下编辑器右侧少 209px、4 个按钮点不到」）。
"""
import json
import os
import re
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial

STYLE = os.path.join(PROJECT_ROOT, 'renderer', 'style.css')
INDEX = os.path.join(PROJECT_ROOT, 'renderer', 'index.html')


def _style():
    with open(STYLE, encoding='utf-8') as f:
        return f.read()


def _index():
    with open(INDEX, encoding='utf-8') as f:
        return f.read()


# ---------- 静态检查（无需浏览器） ----------

class TestStaticUI:
    def test_editor_grid_column_can_shrink(self):
        """列必须是 minmax(0, 1fr)：默认 auto 会被工具栏 826px 固有宽度撑住而裁切"""
        css = _style()
        block = re.search(r'\.editor-container\s*\{[^}]*\}', css, re.S).group(0)
        assert 'grid-template-columns: minmax(0, 1fr)' in block, \
            'editor-container 的列必须可收缩，否则窄窗口下编辑器右侧被 overflow:hidden 裁掉'

    def test_toolbar_formats_wrap(self):
        css = _style()
        assert 'flex-wrap: wrap' in css
        # 自定义按钮组（18 个按钮）所在规则必须允许换行
        m = re.findall(r'\.ql-toolbar\.ql-snow \.ql-formats\s*\{[^}]*\}', css, re.S)
        assert m and any('flex-wrap: wrap' in b for b in m), '工具栏格式组必须允许换行'

    def test_dark_theme_defined(self):
        css = _style()
        assert '[data-theme="dark"]' in css, '深色主题块不能丢'
        block = re.search(r'\[data-theme="dark"\]\s*\{[^}]*\}', css, re.S).group(0)
        for var in ('--bg-primary', '--text-primary', '--text-muted', '--accent',
                    '--accent-dark', '--accent-light', '--glass-sidebar', '--tag-text'):
            assert var in block, '深色主题缺少 %s' % var

    def test_dark_theme_inverts_accent_pair(self):
        """--accent-dark 当文字用、--accent-light 当底色用，深色下两者必须反转"""
        css = _style()
        block = re.search(r'\[data-theme="dark"\]\s*\{[^}]*\}', css, re.S).group(0)
        def lum(hexv):
            h = hexv.lstrip('#')
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        dark = re.search(r'--accent-dark:\s*(#[0-9A-Fa-f]{6})', block).group(1)
        light = re.search(r'--accent-light:\s*(#[0-9A-Fa-f]{6})', block).group(1)
        assert lum(dark) > lum(light), '深色下 --accent-dark 必须比 --accent-light 亮（否则深底深字）'

    def test_accent_text_variable_exists_per_theme(self):
        css = _style()
        for theme in ('white', 'cream', 'pink', 'blue', 'dark'):
            block = re.search(r'\[data-theme="%s"\]\s*\{[^}]*\}' % theme, css, re.S)
            assert block, '缺少主题 %s' % theme
            assert '--accent-text' in block.group(0), '%s 主题缺少 --accent-text' % theme

    def test_theme_panel_offers_dark_and_system(self):
        html = _index()
        assert 'data-theme="dark"' in html and 'data-theme="system"' in html
        assert '跟随系统' in html

    def test_list_skeleton_in_static_html(self):
        """骨架屏必须在静态 HTML 里：等 JS 生成的话第一帧仍然是空白"""
        html = _index()
        assert 'class="note-skeleton"' in html
        assert 'sk-line' in html

    def test_note_preview_and_mark_styles(self):
        css = _style()
        assert '.note-item-preview' in css
        assert 'mark' in css, '搜索命中高亮样式缺失'


# ---------- 实机测量（e2e） ----------

pytestmark_e2e = pytest.mark.e2e

MEASURE = r"""
JSON.stringify((function () {
  var area = document.getElementById('editor-area');
  var tb = document.getElementById('editor-toolbar');
  var ab = area.getBoundingClientRect();
  var tbVisible = getComputedStyle(tb).display !== 'none' && tb.getBoundingClientRect().width > 0;
  var btns = Array.prototype.slice.call(tb.querySelectorAll('button'));
  // 只统计「真的画出来了」的按钮：display:none 时 rect 全是 0，会被误判成越界
  var drawn = btns.filter(function (b) { return b.getBoundingClientRect().width > 0; });
  var off = drawn.filter(function (b) {
    var r = b.getBoundingClientRect();
    return r.right > ab.right + 1 || r.left < ab.left - 1;
  });
  var qlEl = document.querySelector('.ql-editor');
  var ql = qlEl.getBoundingClientRect();
  var cs = getComputedStyle(document.body);
  function lum(c) {
    var m = c.match(/\d+/g).map(Number);
    return (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255;
  }
  return {
    innerW: window.innerWidth,
    areaW: Math.round(ab.width),
    toolbarVisible: tbVisible,
    buttonsDrawn: drawn.length,
    offscreenButtons: off.length,
    qlEditorCutPx: Math.round(Math.max(0, ql.right - ab.right)),
    theme: document.body.getAttribute('data-theme'),
    bgLum: lum(cs.backgroundColor),
    fgLum: lum(cs.color),
    skeletonLeft: document.querySelectorAll('.note-skeleton').length,
    previewText: (document.querySelector('.note-item-preview') || {}).textContent || '',
    previewHtml: (document.querySelector('.note-item-preview') || {}).innerHTML || ''
  };
})())
"""


def _run(ns, actions, wait_before=8, wait_after=1):
    import webview
    result = {}
    window = webview.create_window('UI 回归', os.path.join(PROJECT_ROOT, 'renderer', 'index.html'),
                                   js_api=ns['api'], width=1200, height=800)

    def runner():
        try:
            time.sleep(wait_before)
            actions(window, result)
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            time.sleep(wait_after)
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


@pytest.mark.e2e
def test_no_toolbar_or_editor_clipping_at_small_widths(tmp_path, monkeypatch):
    """窄窗口（含应用允许的最小 900×600）不得裁切编辑器或把工具栏按钮挤出屏幕"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '裁切测试', 'content': '{"ops":[{"insert":"正文\\n"}]}'})
    out = {}

    def actions(window, result):
        for w, h in ((1200, 800), (1000, 700), (900, 600)):
            window.resize(w, h)
            time.sleep(1.4)
            out[w] = json.loads(window.evaluate_js(MEASURE))

    res = _run(ns, actions)
    assert 'error' not in res, res
    for w, m in out.items():
        assert m['toolbarVisible'], '%dpx 窗口下工具栏应可见（有笔记时）' % w
        assert m['buttonsDrawn'] == 26, '%dpx 窗口下应有 26 个按钮被画出，实际 %d' % (w, m['buttonsDrawn'])
        assert m['offscreenButtons'] == 0, '%dpx 窗口下 %d 个工具栏按钮不可见' % (w, m['offscreenButtons'])
        assert m['qlEditorCutPx'] == 0, '%dpx 窗口下编辑器右侧被裁 %dpx' % (w, m['qlEditorCutPx'])


@pytest.mark.e2e
def test_note_list_shows_preview_and_clears_skeleton(tmp_path, monkeypatch):
    """列表显示正文摘要，且骨架屏在渲染后被替换掉"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '摘要测试', 'content': '{"ops":[{"insert":"这是正文摘要内容\\n"}]}'})

    res = _run(ns, lambda window, result: result.update(json.loads(window.evaluate_js(MEASURE))))
    assert 'error' not in res, res
    assert res['skeletonLeft'] == 0, '渲染完成后骨架屏应被替换掉'
    assert '这是正文摘要内容' in res['previewText'], '列表应显示正文摘要，实际：%r' % res['previewText']


@pytest.mark.e2e
def test_search_snippet_highlighted(tmp_path, monkeypatch):
    """搜索命中时摘要显示命中片段，并用 <mark> 高亮关键词"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    a = backend.api.notes_create()['id']
    backend.api.notes_update(a, {'title': '甲', 'content': '{"ops":[{"insert":"前面一些无关的话，中间有火龙果三个字，后面还有别的\\n"}]}'})
    b = backend.api.notes_create()['id']
    backend.api.notes_update(b, {'title': '乙', 'content': '{"ops":[{"insert":"完全无关的内容\\n"}]}'})

    def actions(window, result):
        window.evaluate_js(
            "__app.dom.searchInput.value = '火龙果';"
            "__app.dom.searchInput.dispatchEvent(new Event('input', {bubbles:true}));")
        time.sleep(1.6)   # > 200ms 防抖 + 桥接往返
        result.update(json.loads(window.evaluate_js(MEASURE)))

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert '火龙果' in res['previewText'], '命中片段应显示关键词：%r' % res['previewText']
    assert '<mark>火龙果</mark>' in res['previewHtml'], \
        '关键词必须被 <mark> 高亮（且转义安全）：%r' % res['previewHtml']


@pytest.mark.e2e
def test_dark_theme_switches_and_persists(tmp_path, monkeypatch):
    """点「深色」后：DOM 主题切换、底色变暗文字变亮、设置落库"""
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        result['before'] = json.loads(window.evaluate_js(MEASURE))
        window.evaluate_js("document.querySelector('.theme-option[data-theme=\"dark\"]').click()")
        time.sleep(1.0)
        result['after'] = json.loads(window.evaluate_js(MEASURE))

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['before']['theme'] == 'white'
    assert res['after']['theme'] == 'dark', '点深色后面板应把 data-theme 切成 dark'
    assert res['after']['bgLum'] < 0.2, '深色主题底色亮度应很低，实际 %.2f' % res['after']['bgLum']
    assert res['after']['fgLum'] > 0.7, '深色主题文字应很亮，实际 %.2f' % res['after']['fgLum']

    import sqlite3
    conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
    val = conn.execute("SELECT value FROM settings WHERE key='theme'").fetchone()
    conn.close()
    assert val and val[0] == 'dark', '主题选择应落库（下次启动保持）'


@pytest.mark.e2e
def test_system_theme_resolves_to_concrete(tmp_path, monkeypatch):
    """「跟随系统」：设置项存 system，但 DOM 上落到具体主题（dark 或 white）"""
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        window.evaluate_js("document.querySelector('.theme-option[data-theme=\"system\"]').click()")
        time.sleep(1.0)
        result.update(json.loads(window.evaluate_js(MEASURE)))

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['theme'] in ('dark', 'white'), 'DOM 上不能留下 "system"，必须解析成具体主题'

    import sqlite3
    conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
    val = conn.execute("SELECT value FROM settings WHERE key='theme'").fetchone()
    conn.close()
    assert val and val[0] == 'system', '应保存用户的「跟随系统」选择而不是解析结果'
