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
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note, wait_for_js

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
        # ⚠️ 这里只钉**左右** 14px —— 那才是这条断言想守的东西（与侧栏头部对齐）。
        # 原来写的是完整字面量 `padding: 2px 14px 4px`，把上下的值也一起锁死了，
        # 于是第二次红圈反馈（分界线上下不对称）改不动它，得先来改测试。
        # 上下留白由下面那条 test_notebook_bar_padding_is_vertically_symmetric 单独守。
        assert re.search(r'padding:\s*[\d.]+px\s+14px\s+[\d.]+px', bar), \
            '笔记本栏左右内边距必须与侧栏头部一致（都用 14px），实际：%r' % bar
        assert 'var(--input-bg)' in btn, '笔记本按钮底色要跟搜索框同一档'
        assert '--bg-secondary' not in btn, '浅灰底会让同一条边框看着更重'
        assert 'min-height: 35px' in btn, '高度要跟搜索框的 35px 对齐'
        assert 'min-height: 35px' in plus, '「+」也要 35px（同一行三个控件等高）'
        assert 'margin: 0' in plus, '「+」的 8px 右边距会把它的右边界推进来，与搜索框对不齐'

    def test_notebook_bar_padding_is_vertically_symmetric(self):
        """笔记本栏的上下内边距必须相等（用户第二次红圈反馈：那一行看着挤）。

        它是 `.sidebar-header` 的 `border-bottom` 分界线**下方**的第一块，所以它的
        `padding-top` 就是"分界线到笔记本行"的距离。原来的 `2px 14px 4px` 让分界线
        上方 9px、下方只有 2px —— 笔记本行像贴着分界线挂上去的。

        为什么必须靠测试钉：这种 6px 的差肉眼能感觉出"挤"但说不清是哪儿，
        而"省一点侧栏高度"是很自然的改动，很容易又被改回不对称。
        实机像素另有 e2e 断言（test_sidebar_divider_gaps_are_symmetric）。
        """
        css = _style()
        bar = None
        for b in re.findall(r'\.notebook-bar\s*\{[^}]*\}', css, re.S):
            if 'padding' in b:
                bar = re.sub(r'/\*.*?\*/', '', b, flags=re.S)
                break
        assert bar, '找不到 .notebook-bar 的 padding 规则'
        m = re.search(r'padding:\s*([\d.]+)px\s+[\d.]+px\s+([\d.]+)px', bar)
        assert m, 'padding 必须是"上 左右 下"三值写法才好看对称性，实际：%r' % bar
        top, bottom = float(m.group(1)), float(m.group(2))
        assert top == bottom, (
            '笔记本栏上下内边距必须相等（分界线两侧才对称）。\n'
            '  现在 上 %gpx / 下 %gpx —— 上方是搜索区、下方是笔记本行，'
            '不对称会让这一行看着贴住分界线。' % (top, bottom))
        assert top >= 6, '上下留白至少 6px，否则仍然显得挤（实测 8px 合适）'

    def test_new_note_button_still_separated_from_notebook_bar(self):
        """加完笔记本栏的留白后，「新建笔记」按钮不能反而贴上去。

        这条守的是"改一处、坏一处"：笔记本栏 padding 从 4px 变 8px 会让按钮下移，
        如果谁为了把按钮挪回去而把它的 margin-top 改成 0，红框下方就又挤了。
        """
        css = _style()
        m = re.search(r'\.btn-new-note\s*\{[^}]*?margin:\s*([^;]+);', css, re.S)
        assert m, '找不到 .btn-new-note 的 margin'
        parts = m.group(1).split()
        top = float(re.sub(r'[^\d.]', '', parts[0]))
        assert top >= 2, '「新建笔记」按钮上方至少要留 2px（当前 margin: %s）' % m.group(1)

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


SIDEBAR_DIVIDER = r"""JSON.stringify((() => {
  const r = (sel) => { const el = document.querySelector(sel);
    if (!el) return null; const b = el.getBoundingClientRect();
    return {top: Math.round(b.top), bottom: Math.round(b.bottom),
            h: Math.round(b.height)}; };
  const hdr = r('.sidebar-header');
  const search = r('#search-input');
  const bar = r('.notebook-bar');
  const row = r('#btn-notebook-select');
  const newNote = r('.btn-new-note');
  return {
    gapAbove: row.top - hdr.bottom,       // 分界线 → 笔记本行
    gapBelow: hdr.bottom - search.bottom, // 搜索框 → 分界线
    gapBarToNewNote: newNote.top - bar.bottom,
    barHeight: bar.h,
  };
})())"""


@pytest.mark.e2e
def test_sidebar_divider_gaps_are_symmetric(tmp_path, monkeypatch):
    """分界线两侧的留白必须对称（用户第二次红圈反馈：笔记本行看着挤）。

    **实测过的旧状态**（1200×800）：搜索框底 88 → 分界线 97（9px）→ 笔记本行顶 99（**2px**）。
    分界线上方 9px、下方只有 2px，笔记本行像贴着分界线挂上去；它到「新建笔记」按钮又只有 7px。

    根因：`.notebook-bar` 的 `padding` 是 `2px 14px 4px` —— 上 2px。
    现在两侧都是 8px（与 `.sidebar-header` 自己的 8px 同值）。

    这条用**算出来的像素**验收，而不是看 CSS 里写了什么 —— CSS 里还有 border、
    min-height、flex 居中，叠加起来才是肉眼看到的缝。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()

    def actions(window, result):
        time.sleep(1.0)
        result.update(json.loads(window.evaluate_js(SIDEBAR_DIVIDER)))

    res = _run(ns, actions)
    assert 'error' not in res, res
    above, below = res['gapAbove'], res['gapBelow']

    # ① 分界线两侧对称（这是本次反馈的核心）
    assert abs(above - below) <= 1, (
        '笔记本栏的上下留白不对称：分界线上方 %dpx / 下方 %dpx。\n'
        '下方明显偏小 ⇒ 笔记本行会显得贴住分界线（用户红圈反馈）。\n'
        '修法是 .notebook-bar 的 padding 上下取同一个值（当前 8px）。' % (below, above))

    # ② 留白要给够（1px 对称也算对称，但依然挤）
    assert above >= 6, '分界线下方只留了 %dpx，仍然偏挤（实测 8px 合适）' % above

    # ③ 加完笔记本栏的留白后，按钮不能被挤到贴着它
    assert res['gapBarToNewNote'] >= 2, \
        '笔记本行到「新建笔记」按钮只剩 %dpx' % res['gapBarToNewNote']

    # ④ 别把这一行本身压扁（padding 改成不对称的一个常见动机是"省高度"）
    assert res['barHeight'] >= 45, '笔记本栏总高只有 %dpx（8+35+8 = 51 才对）' % res['barHeight']


SIDEBAR_BUDGET = r"""JSON.stringify((() => {
  const sidebar = document.getElementById('sidebar');
  const list = document.getElementById('note-list');
  const tools = [...document.querySelectorAll('.btn-sidebar-footer')];
  const shown = sel => document.querySelector(sel).getClientRects().length > 0;
  const onTop = el => { const r = el.getBoundingClientRect();
    return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); };
  return {
    inner: innerHeight,
    listH: list.clientHeight,
    rowH: list.querySelector('.note-item').getBoundingClientRect().height,
    overflow: sidebar.scrollHeight - sidebar.clientHeight,
    toolRows: new Set(tools.map(b => Math.round(b.getBoundingClientRect().top))).size,
    toolsOnTop: tools.filter(onTop).length,
    labelsInline: tools.filter(b => getComputedStyle(b.querySelector('.tool-label')).position !== 'absolute').length,
    heading: shown('.sidebar-tools-heading'), local: shown('.workspace-local'),
    subtitle: shown('.workspace-subtitle'), listHeading: shown('.note-list-heading'),
  };
})())"""


def _fill_sidebar(backend, tags=0, views=0):
    """一屏放不下的笔记 + 指定数量的标签 / 视图（后两者决定侧栏还剩多少高度给列表）。"""
    for i in range(20):
        note = backend.api.notes_create()
        backend.api.notes_update(note['id'], {'title': '列表高度 %02d' % i, 'content': '正文'})
    for i in range(tags):
        backend.api.tags_create('标签%02d' % i, '#888888')
    for i in range(views):
        backend.api.saved_search_create('视图%02d' % i, 'tag:标签%02d' % i)


def _sidebar_ready(window, tags, views):
    """等列表、标签、视图都到位，并去掉开发态提示条（它盖住窗口底部，打包版没有）。"""
    assert wait_for_js(window, 'Boolean(window.__app && !document.querySelector(".note-skeleton"))')
    assert wait_for_js(window, "Boolean(document.querySelector('.note-item'))")
    if tags:
        assert wait_for_js(window, "document.querySelectorAll('.tag-filter-chip').length", tags)
    if views:
        assert wait_for_js(window, "document.querySelectorAll('.ss-item').length", views)
    wait_for_js(window, "Boolean(document.getElementById('startup-notice'))", timeout=3)
    window.evaluate_js("document.getElementById('startup-notice')?.remove()")


def _resize(window, width, height):
    """改窗口尺寸并等视口跟上。CI 屏幕只有 1024×768，窗口可能被夹住不变，所以等不到变化也往下走。"""
    before = window.evaluate_js('innerWidth + "x" + innerHeight')
    window.resize(width, height)
    wait_for_js(window, 'innerWidth + "x" + innerHeight !== %r' % before, timeout=2)


@pytest.mark.e2e
@pytest.mark.parametrize('tags, views', [(0, 0), (12, 3)], ids=['bare', 'tags-and-views'])
def test_note_list_gets_three_rows_at_every_supported_height(tmp_path, monkeypatch, tags, views):
    """列表优先：矮窗口下先让位的是工具、视图和标签，不是笔记。

    修之前的实测（行高 76px，标签 12 个 + 视图 3 个）：

        窗口        | 没有标签和视图     | 有标签和视图
        1200×800    | 313px，4.1 行      | 72px，0.9 行
        1000×700    | 213px，2.8 行      | 0px，底部被挤出窗口 28px
        900×600     | 133px，1.8 行      | 0px，出界 108px，6 个工具点不到

    1100×792 是最紧的一档：工具还是三行、标签和视图已经收成窄条。
    1100×760 守工具收起的阈值：再调低，这个高度的列表就放不下 3 行。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _fill_sidebar(backend, tags, views)
    out = {}

    def actions(window, result):
        _sidebar_ready(window, tags, views)
        for w, h in ((1200, 800), (1100, 792), (1100, 760), (1000, 700), (900, 600)):
            _resize(window, w, h)
            out['%d×%d' % (w, h)] = json.loads(window.evaluate_js(SIDEBAR_BUDGET))

    res = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in res, res
    for size, m in out.items():
        assert m['rowH'] == 76, '%s 行高变成 %s（虚拟列表只量一次行高，不能随窗口变）' % (size, m['rowH'])
        assert m['listH'] >= 3 * m['rowH'], '%s 列表只有 %dpx，放不下 3 行：%s' % (size, m['listH'], m)
        assert m['overflow'] == 0, '%s 侧栏被挤出窗口 %dpx' % (size, m['overflow'])
        assert m['toolsOnTop'] == 9, '%s 只有 %d 个工具点得到' % (size, m['toolsOnTop'])
        # 分档看真实视口，不看名义窗口：CI 屏幕小，1200×800 的窗口会被夹小。
        # 默认窗口内高约 765，要留在最宽裕的一档：三行带字的工具、标题、状态行、副标题都在
        icons = m['inner'] <= 755
        assert m['toolRows'] == (1 if icons else 3), '%s 工具行数不对：%s' % (size, m)
        assert m['labelsInline'] == (0 if icons else 9), '%s 文字应%s：%s' % (size, '收进气泡' if icons else '留在按钮里', m)
        assert m['heading'] != icons and m['local'] != icons, m
        assert m['subtitle'] == m['listHeading'] == (m['inner'] > 660), m


RAIL_STATE = r"""JSON.stringify((() => {
  const rail = document.getElementById('tag-filter-list');
  const box = rail.getBoundingClientRect();
  const active = document.querySelector('.tag-filter-chip.active');
  const inside = el => { const r = el.getBoundingClientRect();
    return r.left >= box.left - 0.5 && r.right <= box.right + 0.5; };
  return {
    lines: new Set([...rail.children].map(c => Math.round(c.getBoundingClientRect().top))).size,
    hiddenWidth: rail.scrollWidth - rail.clientWidth,
    scrollLeft: rail.scrollLeft,
    activeVisible: active ? inside(active) : null,
    clearShown: document.getElementById('btn-clear-tag-filter').getClientRects().length > 0,
    viewLines: new Set([...document.querySelectorAll('.ss-item')]
      .map(c => Math.round(c.getBoundingClientRect().top))).size,
  };
})())"""


@pytest.mark.e2e
def test_tag_and_view_rails_stay_usable_on_short_windows(tmp_path, monkeypatch):
    """收成一行之后，滚轮要滚得动，选中的标签要看得见，「清除筛选」要在该出现时出现。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _fill_sidebar(backend, 12, 3)

    def state(window):
        return json.loads(window.evaluate_js(RAIL_STATE))

    def actions(window, result):
        _sidebar_ready(window, 12, 3)
        _resize(window, 900, 600)
        result['idle'] = state(window)
        # 脚本派发的 wheel 不会触发原生滚动，动了就是横向映射在工作
        window.evaluate_js("document.getElementById('tag-filter-list').dispatchEvent("
                           "new WheelEvent('wheel', {deltaY: 120, bubbles: true, cancelable: true}))")
        result['wheeled'] = state(window)
        # 选中最后一个：列表重建 + 「清除筛选」出现把窄条挤窄，它都得留在视野里
        window.evaluate_js("[...document.querySelectorAll('.tag-filter-chip')].at(-1).click()")
        assert wait_for_js(window, "Boolean(document.querySelector('.tag-filter-chip.active'))")
        result['picked'] = state(window)
        window.evaluate_js("document.getElementById('btn-clear-tag-filter').click()")
        assert wait_for_js(window, "!document.querySelector('.tag-filter-chip.active')")
        result['cleared'] = state(window)

    res = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in res, res
    assert res['idle']['lines'] == 1 and res['idle']['viewLines'] == 1, res['idle']
    assert res['idle']['hiddenWidth'] > 0, '12 个标签一行放不下，才谈得上滚动：%s' % res['idle']
    assert not res['idle']['clearShown'], '没有筛选时「清除筛选」不占窄条的位置'
    assert res['wheeled']['scrollLeft'] == 120, res['wheeled']
    assert res['picked']['activeVisible'], '选中的标签被挤出了窄条：%s' % res['picked']
    assert res['picked']['clearShown'], res['picked']
    assert not res['cleared']['clearShown'], res['cleared']


def test_short_window_rules_leave_rows_alone_and_label_the_icons():
    """两条 e2e 抓不到的约束，钉在样式表上。

    ① 虚拟列表的行高只量一次：媒体查询改 `.note-item`，滚动条位置就和第几行错开。
    ② 脚本 `.focus()` 不算键盘操作，触发不了 `:focus-visible`，所以聚焦时冒文字这条只能静态守。
    """
    css = open(os.path.join(PROJECT_ROOT, 'renderer', 'workspace.css'), encoding='utf-8').read()
    short = css[css.index('@media (max-height: 960px)'):css.index('@media (prefers-reduced-motion')]
    assert '.note-item' not in short
    assert '.btn-sidebar-footer:focus-visible .tool-label' in short


# ---------- app.pyw 的模块别名（静态，无需浏览器） ----------

APP_PYW = os.path.join(PROJECT_ROOT, 'app.pyw')


class TestAppModuleAliases:
    """`app.pyw` 里引用 backend 的方式必须是它真的绑定了的那些名字。

    ## 为什么要有这一组

    `app.pyw` 顶部只绑了两个名字：

        from backend import api as backend_api      # Api 实例
        import backend as _backend_mod              # 模块本身

    **没有 `backend`**。而"关窗兜底"那条路径上曾经写着 `backend.checkpoint_and_close()` ——
    那个 `backend` 是 `make_closing_handler(target_window, backend, ...)` 的**参数**
    （传进来的是 `backend_api`，一个 Api 实例）。于是：

    * `Api` 实例上没有 `checkpoint_and_close`（它是**模块级**函数）→ AttributeError；
    * 它被 `except Exception` 吞进日志（`退出前 WAL checkpoint 失败`）；
    * 表现是 **notes.db-wal 永远不归零** —— 而"退出后 data 目录是单文件状态"正是
      引入 WAL 时期望的性质（用户手动拷 data 目录才安全）。

    这个 bug 从 WAL 那一版就存在，一直没被发现：它只在"真正退出"这条路径上，
    而默认开着托盘驻留时关窗只是隐藏，日常根本走不到。

    实测复现过（隔离数据目录 + tray_enabled=0 + WM_CLOSE）：
    修复前 `notes.db-wal` 是 506792 字节，修复后 0 字节。
    """

    def test_no_bare_backend_reference(self):
        r"""`app.pyw` 里的裸 `backend.xxx` 必须真的是**词法作用域里的参数**。

        为什么这么判：文件里有三种 `backend` 用法，只有一种是合法的 ——

        1. `_load_saved_geometry(backend)` / `_save_window_geometry(backend, ...)` /
           `make_closing_handler(..., backend, ...)` 里的属性访问 → **合法**，
           那三个函数都把 Api 实例当参数收；
        2. 同名的**嵌套闭包**里访问（如 `_flush_and_close` 里的 `backend.notes_update`）
           → 也合法（Python 词法作用域，看得到外层参数）；
        3. 在**没有这个参数**的函数里裸用 → 真 NameError。

        历史上出事的正是 (2) 的**误用**：`_force_close()` 在 `make_closing_handler` 里，
        它写 `backend.checkpoint_and_close()` —— 语法合法、`backend` 也确实解析到了，
        但那是 **Api 实例**，而 `checkpoint_and_close` 是**模块级函数** → AttributeError
        → 被外层 except 吞掉 → 退出时 WAL 永不归零（那条已由下面的
        `test_no_bare_backend_inside_the_closing_handler` 精确挡住）。

        所以这条只判"`backend` 是不是某个外层函数的参数"：不是就是真 bug。
        用 AST 而不是正则扫文本 —— 正则会连注释里的反例一起算（第一版就误报了）。
        """
        import ast

        tree = ast.parse(open(APP_PYW, encoding='utf-8').read())

        # 收集每个属性的 lineno → 它所在函数的"可见参数集"
        def walk(node, visible_params):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = {a.arg for a in child.args.posonlyargs + child.args.args
                              + child.args.kwonlyargs}
                    if child.args.vararg:
                        params.add(child.args.vararg.arg)
                    if child.args.kwarg:
                        params.add(child.args.kwarg.arg)
                    walk(child, visible_params | params)
                elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) \
                        and child.value.id == 'backend':
                    if 'backend' not in visible_params:
                        offenders.append((child.lineno, child.attr, node.name
                                          if hasattr(node, 'name') else '?'))
                else:
                    walk(child, visible_params)

        offenders = []
        walk(tree, set())
        assert offenders == [], (
            'app.pyw 里裸用了 `backend.xxx`，但它不在作用域里（既不是参数，也没有模块级绑定）'
            '—— 这会是 NameError。\n'
            '文件里只有 `backend_api`（Api 实例）与 `_backend_mod`（模块）两个别名。\n'
            '命中的位置：\n  %s'
            % '\n  '.join('行 %d: backend.%s（在 %s 里）' % o for o in offenders))

    def test_no_bare_backend_inside_the_closing_handler(self):
        """关窗那条路径上尤其不能出现裸 `backend.`。

        为什么单独一条：`make_closing_handler(target_window, backend, ...)` 的**参数**
        就叫 `backend`（传进来的是 Api 实例）。所以在它内部写 `backend.xxx` 语法完全合法、
        不报 NameError，但**语义是 Api 实例** —— 调模块级函数就会 AttributeError，
        而外层 `except Exception` 会把它咽掉（实测就是这么藏了整整一个版本）。
        """
        import ast

        tree = ast.parse(open(APP_PYW, encoding='utf-8').read())
        handler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'make_closing_handler':
                handler = node
                break
        assert handler is not None, '找不到 make_closing_handler'

        params = {a.arg for a in handler.args.args}
        assert 'backend' in params, \
            'make_closing_handler 的第二个参数名变了，这条测试的前提要重看'

        # 这个闭包里有没有调用 checkpoint_and_close（如果有，必须是 _backend_mod 的形式）
        for sub in ast.walk(handler):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == 'checkpoint_and_close':
                base = sub.func.value
                assert not (isinstance(base, ast.Name) and base.id == 'backend'), \
                    ('第 %d 行在关窗闭包里调了 backend.checkpoint_and_close() —— '
                     '那个 backend 是 Api 实例，没有这个方法（会被 except 吞掉，'
                     '表现为退出时 WAL 不归零）。要用 _backend_mod.checkpoint_and_close()。'
                     % sub.lineno)

    def test_backend_module_alias_is_bound(self):
        """确认那两个别名还在（改名字会让上面的判据失效，这条一起钉住）。"""
        src = open(APP_PYW, encoding='utf-8').read()
        assert 'from backend import api as backend_api' in src, \
            'app.pyw 必须把 Api 实例绑成 backend_api'
        assert 'import backend as _backend_mod' in src, \
            'app.pyw 必须把模块绑成 _backend_mod（模块级函数走它）'

    def test_exit_path_calls_checkpoint_on_the_module(self):
        """退出路径必须调**模块级**的 checkpoint_and_close。

          * `Api` 实例上没有它（实测 hasattr 为 False）；
          * 所以只能走 `_backend_mod.checkpoint_and_close()`。

        这条同时挡住"不小心改成 backend_api.checkpoint_and_close()"
        —— 那会静默退回 AttributeError 被吞掉的老样子。
        """
        src = open(APP_PYW, encoding='utf-8').read()
        assert '_backend_mod.checkpoint_and_close()' in src, \
            '退出时的 WAL 归零必须调 _backend_mod.checkpoint_and_close()'
        assert 'backend_api.checkpoint_and_close' not in src, \
            'Api 实例上没有 checkpoint_and_close（它是模块级函数）'


def test_note_row_actions_follow_keyboard_focus_only():
    """行可聚焦之后，鼠标点一下也算焦点：`:focus-within` 会让点过的行一直露着操作按钮、日期也没了。

    无头环境里的 `.click()` 不移动焦点，这条退步在 e2e 里看不出来，所以钉在样式表上。
    """
    css = open(os.path.join(PROJECT_ROOT, 'renderer', 'workspace.css'), encoding='utf-8').read()
    assert '.note-item:focus-within' not in css
    assert '.note-item:has(:focus-visible) .note-item-row-cover > button' in css
    assert '.note-item:focus-visible .note-item-time' in css
