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
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note

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

    def test_sidebar_search_and_notebook_bar_share_one_style(self):
        """侧栏「搜索框」与「笔记本按钮」要像同一套控件（用户红圈反馈）。

        实测过：边框与圆角本来就一致（1px #E0DBD4 / 6px），不一致的是
        ①底色（搜索框近白 --input-bg，笔记本按钮浅灰 --bg-secondary —— 同一条边框压在
        浅灰底上显得更重，看着像两种控件）②高度 35 vs 34 ③左边距 14 vs 10。
        这条测试把三者钉在 CSS 里（像素级一致性由实机那条 test_sidebar_pair_* 保证）。
        """
        css = _style()

        def block(sel, must_have):
            """取该选择器的**第一条**含指定声明的规则块（注释先剥掉）。

            两个坑：①同一个选择器在自适应背景下还有 `!important` 覆盖块（只改 border-color），
            直接 re.search 会抓到那条；②注释里会写"原来是 --bg-secondary"这种话，
            按子串判断会被注释里的旧值骗到。
            """
            for b in re.findall(re.escape(sel) + r'\s*\{[^}]*\}', css, re.S):
                if must_have in b:
                    return re.sub(r'/\*.*?\*/', '', b, flags=re.S)
            raise AssertionError('找不到含 %r 的 %s 规则' % (must_have, sel))

        header = block('.sidebar-header', 'padding')
        bar = block('.notebook-bar', 'padding')
        btn = block('.notebook-select-btn', 'min-height')
        plus = block('.btn-new-notebook', 'min-height')
        assert 'padding: 18px 14px 8px' in header, '侧栏头部左右内边距是 14px（搜索框据此对齐）'
        assert 'padding: 2px 14px 4px' in bar, '笔记本栏左右内边距必须与侧栏头部一致（都用 14px）'
        assert 'var(--input-bg)' in btn, '笔记本按钮底色要跟搜索框同一档'
        assert '--bg-secondary' not in btn, '浅灰底会让同一条边框看着更重'
        assert 'min-height: 35px' in btn, '高度要跟搜索框的 35px 对齐'
        assert 'min-height: 35px' in plus, '「+」也要 35px（同一行三个控件等高）'
        assert 'margin: 0' in plus, '「+」的 8px 右边距会把它的右边界推进来，与搜索框对不齐'

    def test_note_time_never_wraps_and_yields_on_hover(self):
        """笔记行的时间戳：正常态不折行；悬停态让位给那 5 个操作按钮。

        实测（侧栏 270px）：悬停时 5 个按钮吃掉 ~142px，信息列从 181px 被压到 39px，
        自然宽 105px 的 "2026-09-27 16:58" 于是折成三行（用户截图）。只加 nowrap 会变成
        压到按钮上，所以悬停时让它先退场。
        """
        css = _style()
        t = next((b for b in re.findall(r'\.note-item-time\s*\{[^}]*\}', css, re.S)
                  if 'white-space' in b), None)
        assert t, '找不到 .note-item-time 的 white-space 规则'
        assert 'white-space: nowrap' in t, '时间戳不许折行'
        assert re.search(r'\.note-item:hover \.note-item-time\s*\{\s*display:\s*none', css), \
            '悬停时要隐藏时间戳（按钮占位后放不下它）'

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

    def test_every_theme_defines_on_accent_pair(self):
        """承载文字的实心主色必须是 --accent-solid + --on-accent（白字压主色全主题不达标）"""
        css = _style()
        for theme in ('white', 'cream', 'pink', 'blue', 'dark'):
            block = re.search(r'\[data-theme="%s"\]\s*\{[^}]*\}' % theme, css, re.S).group(0)
            assert '--accent-solid' in block, '%s 缺少 --accent-solid' % theme
            assert '--on-accent' in block, '%s 缺少 --on-accent' % theme

    def test_no_white_text_on_plain_accent(self):
        """回归护栏：不要再出现 `background: var(--accent); color: #fff/#FFFFFF`。

        实测白字压主色在 5 个主题下是 3.66/3.26/3.47/4.22/2.38，全部低于 AA 的 4.5。
        """
        css = _style()
        bad = re.findall(r'background:\s*var\(--accent\)[^;]*;\s*color:\s*#(?:fff|FFFFFF)\b',
                         css, re.I)
        assert not bad, '这些规则仍在用「主色底 + 白字」，应改用 --accent-solid/--on-accent'
        bad2 = re.findall(r'color:\s*#(?:fff|FFFFFF)\s*;\s*background:\s*var\(--accent\)',
                          css, re.I)
        assert not bad2, '同上（属性顺序相反的那种写法）'

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

    def test_group234_ui_present(self):
        """本轮新增的入口不能被误删：快速跳转、多选条、按范围导出、Markdown、自动锁定、热键"""
        html = _index()
        for needle, what in (
            ('id="quick-switch-panel"', '快速跳转面板'),
            ('id="quick-switch-input"', '快速跳转输入框'),
            ('id="bulk-bar"', '批量操作条'),
            ('id="bulk-delete"', '批量删除按钮'),
            ('data-format="zip-notebook"', '按笔记本导出'),
            ('data-format="zip-tag"', '按标签导出'),
            ('data-format="md"', 'Markdown 导出'),
            ('data-format="md-import"', 'Markdown 导入'),
            ('id="sel-autolock"', '自动锁定下拉'),
            ('id="chk-hotkey"', '全局热键开关'),
            ('tag:标签名', '搜索范围语法提示'),
        ):
            assert needle in html, '缺少 %s（%s）' % (what, needle)
        css = _style()
        for needle in ('.bulk-bar', '.quick-switch-item', '.note-item.selected', '.autolock-row'):
            assert needle in css, '缺少样式 %s' % needle


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
    # 工具栏 26 个按钮是 Quill 的（Markdown 笔记不显示工具栏），所以这里要 delta 笔记
    make_delta_note(backend, '裁切测试', '{"ops":[{"insert":"正文\\n"}]}')
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
        # 读**命中那篇**的摘要（未命中的那篇在窗口化渲染下根本不在 DOM 里，
        # 所以这里也顺带验"列表里只剩命中的行"）
        result['rows'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.note-item')].map(e => e.dataset.noteId))")
        result['previewText'] = window.evaluate_js(
            "var el = document.querySelector('.note-item[data-note-id=\"%s\"] .note-item-preview');"
            "el ? el.textContent : 'MISSING'" % a)
        result['previewHtml'] = window.evaluate_js(
            "var el = document.querySelector('.note-item[data-note-id=\"%s\"] .note-item-preview');"
            "el ? el.innerHTML : 'MISSING'" % a)
        # `b` 的行现在**不渲染**（而不是渲染后隐藏）
        result['b_present'] = window.evaluate_js(
            "!!document.querySelector('.note-item[data-note-id=\"%s\"]')" % b)

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert '火龙果' in res['previewText'], '命中片段应显示关键词：%r' % res['previewText']
    assert '<mark>火龙果</mark>' in res['previewHtml'], \
        '关键词必须被 <mark> 高亮（且转义安全）：%r' % res['previewHtml']
    assert a in json.loads(res['rows']), '命中的笔记要留在列表里'
    assert res['b_present'] is False, \
        '未命中的笔记不该出现在列表里（窗口化：根本不渲染，而不是渲染后加 hidden 类）'


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


SIDEBAR_PAIR = r"""JSON.stringify((() => {
  const rect = (el) => el.getBoundingClientRect();
  const pick = (el) => { const c = getComputedStyle(el); return {
    left: Math.round(rect(el).left * 10) / 10, right: Math.round(rect(el).right * 10) / 10,
    height: Math.round(rect(el).height * 10) / 10,
    border: c.borderTopWidth + ' ' + c.borderTopColor,
    radius: c.borderTopLeftRadius, bg: c.backgroundColor,
    radiusPx: c.borderTopLeftRadius }; };
  const time = document.querySelector('#note-list .note-item-time');
  const tc = getComputedStyle(time);
  const lh = parseFloat(tc.lineHeight) || 15;
  const pinSvg = document.querySelector('#note-list .note-item-pin svg');
  return {
    search: pick(document.getElementById('search-input')),
    notebook: pick(document.getElementById('btn-notebook-select')),
    plus: pick(document.getElementById('btn-new-notebook-sidebar')),
    pinBtnRadius: getComputedStyle(document.querySelector('.note-item-pin')).borderTopLeftRadius,
    time: { nowrap: tc.whiteSpace,
            lines: Math.round(rect(time).height / lh * 10) / 10 },
    pin: pinSvg ? { circles: pinSvg.querySelectorAll('circle').length,
                    paths: pinSvg.querySelectorAll('path').length,
                    stroke: pinSvg.getAttribute('stroke-width') } : null,
  };
})())"""


@pytest.mark.e2e
def test_sidebar_pair_and_note_row_pixels(tmp_path, monkeypatch):
    """侧栏那两块 + 列表行的时间戳：按**算出来的像素**验收。

    改之前实测：搜索框 35px 高 / 左边 14 / 底色 #FCFAF7，笔记本按钮 34px 高 / 左边 10 /
    底色 #F6F3EF —— 边框与圆角其实一样（1px #E0DBD4 / 6px），差的是底色、高度与 4px 错位。
    时间戳那三行也是量出来的：悬停时 5 个按钮把信息列从 181px 压到 39px。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '未命名笔记', 'content': '一段正文摘要文字\n'})

    def actions(window, result):
        time.sleep(1.0)
        result.update(json.loads(window.evaluate_js(SIDEBAR_PAIR)))

    res = _run(ns, actions)
    assert 'error' not in res, res
    s, n, plus = res['search'], res['notebook'], res['plus']

    # ① 同一套控件：边框 / 圆角 / 底色 / 高度 / 左边界
    assert s['border'] == n['border'], '边框必须一致：%s vs %s' % (s['border'], n['border'])
    assert s['radius'] == n['radius'], '圆角必须一致：%s vs %s' % (s['radius'], n['radius'])
    assert s['bg'] == n['bg'], '底色必须一致（浅灰底会让同一条边框看着更重）：%s vs %s' % (s['bg'], n['bg'])
    assert abs(s['height'] - n['height']) <= 0.5, '高度差 %.1fpx' % abs(s['height'] - n['height'])
    assert abs(s['left'] - n['left']) <= 0.5, '左边界差 %.1fpx' % abs(s['left'] - n['left'])
    assert abs(s['right'] - plus['right']) <= 0.5, \
        '右边界（笔记本栏的「+」按钮）应与搜索框对齐：%.1f vs %.1f' % (s['right'], plus['right'])

    # ② 时间戳一行；悬停让位（:hover 无法在无头环境触发，规则由静态测试钉住）
    assert res['time']['nowrap'] == 'nowrap'
    assert res['time']['lines'] == 1, '时间戳折成了 %.1f 行' % res['time']['lines']

    # ③ 置顶图标：细描边图钉（无圆点），悬停底色是圆
    assert res['pin'] == {'circles': 0, 'paths': 2, 'stroke': '2'}, res['pin']
    assert res['pinBtnRadius'] == '50%', '悬停底框应是圆：%s' % res['pinBtnRadius']
