# -*- coding: utf-8 -*-
"""自定义背景图下的界面可读性：单测（后端字段）+ 实机（分区明暗、薄纱、模糊）。

背景（用户反馈 + 取证）：自适应系统算了图片亮度却**没用**，而且第 6 轮之后新增的
Markdown 工具栏 / 标签行 / 属性行 / 状态栏**一条自适应规则都没有** —— 它们 100% 透明地压在
花图上，用的还是"浅色纸面"的深色文字，于是功能键和标题看不清。

选定的方案是「路线 B 沉浸式」：正文区继续全透明，界面行按**它自己那块图片区域**的明暗
切换文字色（分区采样），再给用户两个滑杆（背景模糊 / 界面不透明度）兜底。
"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('背景可读性', RENDERER + '/index.html',
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


def _solid_png(path, size, color):
    from PIL import Image
    Image.new('RGB', size, color).save(path)
    return path


def _rgb(css):
    """'rgb(a, b, c)' / 'rgba(a, b, c, 0.9)' → (a, b, c)"""
    import re
    nums = [int(v) for v in re.findall(r'\d+', css or '')[:3]]
    assert len(nums) == 3, '颜色解析失败：%r' % css
    return tuple(nums)


def _set_input(window, selector, value):
    window.evaluate_js(
        "(function(){var el=document.querySelector(%s); if(!el) return;"
        "el.value=%s; el.dispatchEvent(new Event('input',{bubbles:true}));})()"
        % (json.dumps(selector), json.dumps(str(value))))


def _click_theme(window, theme):
    """点主题面板里的某个主题（面板可以没打开，click() 照样触发监听）"""
    return window.evaluate_js(
        "(function(){var o=document.querySelector(%s); if(!o) return false;"
        "o.click(); return true;})()" % json.dumps('.theme-option[data-theme="%s"]' % theme))


def _veil_geometry(window):
    """三片薄纱与两条界面边界的实际像素位置（都相对 #editor-container 顶边）"""
    return window.evaluate_js(
        "(function(){"
        "var cont=document.getElementById('editor-container').getBoundingClientRect();"
        "var ink=document.querySelector('#md-editor:not(.hidden)')||document.querySelector('.ql-container');"
        "var st=document.getElementById('editor-status').getBoundingClientRect();"
        "var ct=document.getElementById('chrome-veil').getBoundingClientRect();"
        "var cb=document.getElementById('bottom-veil').getBoundingClientRect();"
        "var cc=document.getElementById('content-veil').getBoundingClientRect();"
        "return JSON.stringify({inkTop:ink.getBoundingClientRect().top-cont.top,"
        "statusTop:st.top-cont.top,chromeTop:ct.top-cont.top,chromeBottom:ct.bottom-cont.top,"
        "bottomTop:cb.top-cont.top,contentTop:cc.top-cont.top,contentBottom:cc.bottom-cont.top});})()")


# ---------------- 静态：薄纱的 CSS 约定 ----------------

def test_veil_css_conventions():
    """界面层薄纱必须是"整片 + 磨砂 + mask 渐隐"，且自适应下 Quill 的白玻璃要让位。

    这几点都是踩过坑才定下来的：
      · 逐行 background-color → 行间空隙露原图（斑马纹）+ 交界处一刀切；
      · 只用渐变背景、不用 mask → backdrop-filter 的模糊边界又成了新硬边；
      · 留着 .ql-toolbar 自带的 55% 白玻璃 → 富文本模式多出一条亮带。
    """
    css = open(os.path.join(PROJECT_ROOT, 'renderer', 'style.css'), encoding='utf-8').read()
    assert '.chrome-veil' in css and '.bottom-veil' in css
    assert 'mask-image: linear-gradient' in css, '薄纱必须用 mask 渐隐'
    assert 'backdrop-filter: blur' in css, '薄纱必须是磨砂玻璃'
    assert 'body.adaptive-bg .ql-toolbar.ql-snow' in css
    idx = css.index('body.adaptive-bg .ql-toolbar.ql-snow')
    assert 'background: transparent !important' in css[idx:idx + 220], \
        '自适应模式下 Quill 工具栏的白玻璃应当交还给薄纱'
    # 界面行自己不该再有底色（那是第一版的斑马纹来源）
    tone_block = css[css.index('.bg-tone-light, .bg-tone-dark {'):]
    tone_block = tone_block[:tone_block.index('}')]
    assert 'background' not in tone_block, '明暗类不该再设底色：%r' % tone_block


def test_content_veil_css_and_js_constants_match():
    """正文薄纱的渐隐段必须与上下两片**逐像素重合**（48 / 40），而且 CSS 与 JS 两处数字一致。

    为什么要这条：三片薄纱的 alpha 是相加的，首尾相接会多出两条硬边；数字写歪一处，
    用户看到的就是另一块"突兀的一块"。CSS 里是 mask 的长度，JS 里是 VEIL_FADE_*，
    两边必须同时改——所以直接比对两处。
    """
    css = open(os.path.join(PROJECT_ROOT, 'renderer', 'style.css'), encoding='utf-8').read()
    js = open(os.path.join(RENDERER, 'js', 'app', '08-appearance2.js'), encoding='utf-8').read()
    assert '.content-veil' in css and 'content-veil' in js
    assert 'body.adaptive-bg .content-veil' in css, '正文薄纱只在自适应模式显示'
    assert '--ad-content-scrim' in css and '--ad-content-scrim' in js
    assert 'VEIL_FADE_TOP = 48' in js and 'VEIL_FADE_BOTTOM = 40' in js
    cveil = css[css.index('.content-veil {\n  top: 0;'):]
    cveil = cveil[:cveil.index('}')]
    assert '#000 48px' in cveil and 'calc(100% - 40px)' in cveil, \
        '正文薄纱的渐隐段必须与上下两片的 48/40 对齐：%r' % cveil
    chrome = css[css.index('.chrome-veil {'):]
    chrome = chrome[:chrome.index('}')]
    assert 'calc(100% - 48px)' in chrome, '顶部薄纱的渐隐段是 48px'
    bottom = css[css.index('.bottom-veil {'):]
    bottom = bottom[:bottom.index('}')]
    assert 'calc(100% - 40px)' in bottom, '底部薄纱的渐隐段是 40px'


def test_tone_classes_cover_very_muted_and_mixed_ink():
    """两个实测踩到的漏项：--text-very-muted 没被覆盖（占位符在图上隐形）、混合底没有描边兜底。"""
    css = open(os.path.join(PROJECT_ROOT, 'renderer', 'style.css'), encoding='utf-8').read()
    for cls in ('.bg-tone-light {', '.bg-tone-dark {'):
        block = css[css.index(cls):]
        block = block[:block.index('}')]
        assert '--text-very-muted' in block, '%s 必须覆盖 --text-very-muted（占位符用它）' % cls
        assert '--ad-stroke' in block, '%s 需要给描边一个颜色' % cls
    mixed = css[css.index('.bg-ink-mixed {'):]
    mixed = mixed[:mixed.index('}')]
    assert 'text-stroke' in mixed and 'paint-order: stroke' in mixed, \
        '混合底必须描边，且描边要画在字形下面（否则笔画被吃掉）：%r' % mixed


# ---------------- 单测：后端字段 ----------------

def test_note_has_blur_and_scrim_columns(api, backend_mod):
    nid = api.notes_create()['id']
    note = api.notes_get(nid)
    assert 'bg_blur' in note and 'ui_scrim' in note
    # content_scrim 刻意没有默认值：NULL = 没单独设过 → 跟随全局（不给存量笔记强加一层薄纱）
    assert 'content_scrim' in note and note['content_scrim'] is None
    api.notes_update(nid, {'bg_blur': 8, 'ui_scrim': 0.5, 'content_scrim': 0.7})
    note = api.notes_get(nid)
    assert float(note['bg_blur']) == 8
    assert abs(float(note['ui_scrim']) - 0.5) < 1e-6
    assert abs(float(note['content_scrim']) - 0.7) < 1e-6


def test_settings_defaults(api, backend_mod):
    all_settings = api.settings_get_all()
    assert all_settings.get('bg_blur') == '0'
    assert all_settings.get('ui_scrim') == '0.3'
    assert all_settings.get('content_scrim') == '0.3'


# ---------------- 实机：分区明暗 / 薄纱 / 模糊 ----------------

@pytest.mark.e2e
def test_dark_image_switches_chrome_to_light_ink(tmp_path, monkeypatch):
    """深色背景图 → 整条界面链变成"暗底亮字"（这就是"看不清"的直接解药）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'dark.png'), (600, 400), (14, 16, 24))
    nid = backend.api.notes_create()['id']
    # 缩放 200% 让图盖满编辑区：否则宽图上下留空、露出主题底色，上下两条界面行本就该判成浅色
    backend.api.notes_update(nid, {'title': '暗背景', 'content': '# 标题\n\n正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_opacity': 1.0,
                                   'bg_zoom': 200})

    def actions(window, result):
        time.sleep(2.5)
        result['tone'] = window.evaluate_js(
            "['#title-row','#md-toolbar','#editor-status'].map(function(s){"
            "var el=document.querySelector(s);"
            "return el ? (el.classList.contains('bg-tone-dark') ? 'dark' :"
            "(el.classList.contains('bg-tone-light') ? 'light' : 'none')) : 'missing';}).join(',')")
        result['ink'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('editor-status')).color")
        result['scrim'] = window.evaluate_js(
            "getComputedStyle(document.documentElement).getPropertyValue('--ad-scrim').trim()")
        result['veil'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('chrome-veil')).backgroundColor")
        result['shadow'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('editor-status')).textShadow")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['tone'] == 'dark,dark,dark', r['tone']
    # 亮字：RGB 三个通道都高
    assert min(_rgb(r['ink'])) > 200, '深色背景下界面文字应当是亮色，实际 %r' % r['ink']
    assert r['scrim'] == '0.3', r['scrim']
    assert 'rgba' in r['veil'] and '0.3' in r['veil'].replace(' ', ''), r['veil']
    assert r['shadow'] != 'none', '亮字需要有暗阴影兜底：%r' % r['shadow']


@pytest.mark.e2e
def test_light_image_switches_chrome_to_dark_ink(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'light.png'), (600, 400), (245, 243, 238))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '亮背景', 'content': '# 标题\n\n正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_opacity': 1.0})

    def actions(window, result):
        time.sleep(2.5)
        result['tone'] = window.evaluate_js(
            "document.getElementById('md-toolbar').classList.contains('bg-tone-light')")
        result['ink'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('md-toolbar')).color")
        result['status_toned'] = window.evaluate_js(
            "document.querySelectorAll('.bg-tone-dark, .bg-tone-light').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['tone'] is True
    assert max(_rgb(r['ink'])) < 90, '浅色背景下界面文字应当是深色，实际 %r' % r['ink']
    assert r['status_toned'] >= 8, '界面行都应当被上色：%r' % r['status_toned']


@pytest.mark.e2e
def test_two_sliders_blur_and_scrim(tmp_path, monkeypatch):
    """两个滑杆：背景模糊落到图层 filter 上，界面不透明度落到 --ad-scrim 上，并落库。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'bg.png'), (600, 400), (30, 40, 60))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '滑杆', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_opacity': 1.0})

    def actions(window, result):
        time.sleep(2.5)
        _set_input(window, '#note-bg-blur', 12)
        time.sleep(0.8)
        result['filter'] = window.evaluate_js(
            "document.getElementById('note-bg-layer').style.filter")
        _set_input(window, '#note-ui-scrim', 80)
        time.sleep(0.8)
        result['scrim'] = window.evaluate_js(
            "getComputedStyle(document.documentElement).getPropertyValue('--ad-scrim').trim()")
        result['veil'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('chrome-veil')).backgroundColor")
        # 全局滑杆在笔记自带背景图时**不该**抢（笔记级设置优先），此时它只写设置值
        _set_input(window, '#global-bg-blur', 5)
        time.sleep(0.5)
        result['note_filter_kept'] = window.evaluate_js(
            "document.getElementById('note-bg-layer').style.filter")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['filter'] == 'blur(12px)', r['filter']
    assert r['scrim'] == '0.8', r['scrim']
    assert '0.8' in r['veil'].replace(' ', ''), r['veil']
    assert r['note_filter_kept'] == 'blur(12px)', '笔记级背景图生效时，全局滑杆不该改动它'
    note = backend.api.notes_get(nid)
    assert float(note['bg_blur']) == 12
    assert abs(float(note['ui_scrim']) - 0.8) < 1e-6


@pytest.mark.e2e
def test_global_sliders_apply_when_using_global_background(tmp_path, monkeypatch):
    """笔记"跟随全局背景"时，全局那套滑杆才生效（笔记级背景图在时由笔记设置优先）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'global.png'), (600, 400), (28, 34, 48))
    backend.api.settings_set('bg_type', 'image')
    backend.api.settings_set('bg_value', img)
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '跟随全局', 'content': '正文\n', 'bg_type': 'global'})

    def actions(window, result):
        time.sleep(2.5)
        _set_input(window, '#global-bg-blur', 6)
        _set_input(window, '#global-ui-scrim', 60)
        time.sleep(0.8)
        result['global_filter'] = window.evaluate_js(
            "document.getElementById('global-bg-layer').style.filter")
        result['scrim'] = window.evaluate_js(
            "getComputedStyle(document.documentElement).getPropertyValue('--ad-scrim').trim()")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['global_filter'] == 'blur(6px)', r
    assert r['scrim'] == '0.6', r
    assert backend.api.settings_get('bg_blur') == '6'
    assert abs(float(backend.api.settings_get('ui_scrim')) - 0.6) < 1e-6


@pytest.mark.e2e
def test_chrome_veil_is_one_frosted_layer_that_fades_out(tmp_path, monkeypatch):
    """界面层是**一整片**磨砂薄纱，且用 mask 把"色调 + 模糊"一起淡出。

    为什么要这条：第一版是逐行加 background-color，工具栏那一行到底就"一刀切"，
    行与行之间的空隙还会露出原图（斑马纹）——用户看到的正是那条硬边。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'bg.png'), (600, 400), (40, 60, 90))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '薄纱', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_zoom': 200})

    def actions(window, result):
        time.sleep(2.5)
        result['display'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('chrome-veil')).display")
        result['top_h'] = window.evaluate_js(
            "parseInt(document.getElementById('chrome-veil').style.height) || 0")
        result['bottom_h'] = window.evaluate_js(
            "parseInt(document.getElementById('bottom-veil').style.height) || 0")
        result['mask'] = window.evaluate_js(
            "(getComputedStyle(document.getElementById('chrome-veil')).maskImage || "
            "getComputedStyle(document.getElementById('chrome-veil')).webkitMaskImage || '')")
        result['blur'] = window.evaluate_js(
            "(getComputedStyle(document.getElementById('chrome-veil')).backdropFilter || "
            "getComputedStyle(document.getElementById('chrome-veil')).webkitBackdropFilter || '')")
        result['row_bg'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('md-toolbar')).backgroundColor")
        result['ql_bg'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('editor-toolbar')).backgroundColor")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['display'] == 'block', r
    assert r['top_h'] > 120 and r['bottom_h'] > 20, r
    assert 'linear-gradient' in r['mask'], '薄纱必须用 mask 渐隐：%r' % r['mask']
    assert 'blur' in r['blur'], '薄纱应当是磨砂玻璃：%r' % r['blur']
    assert r['row_bg'] in ('rgba(0, 0, 0, 0)', 'transparent'), \
        '界面行自己不该再有底色（会形成斑马纹）：%r' % r['row_bg']
    assert r['ql_bg'] in ('rgba(0, 0, 0, 0)', 'transparent'), \
        '自适应模式下 Quill 工具栏的白玻璃应当交还给薄纱：%r' % r['ql_bg']


@pytest.mark.e2e
def test_clearing_background_removes_tone_classes(tmp_path, monkeypatch):
    """清掉背景图后不能留着上一次图片的配色（否则浅色主题上会出现"亮字压白底"）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'dark.png'), (400, 300), (10, 12, 18))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '临时背景', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img})

    def actions(window, result):
        time.sleep(2.5)
        result['before'] = window.evaluate_js(
            "document.querySelectorAll('.bg-tone-dark, .bg-tone-light').length")
        window.evaluate_js("document.getElementById('btn-clear-note-bg').click();")
        time.sleep(1.6)
        result['after'] = window.evaluate_js(
            "document.querySelectorAll('.bg-tone-dark, .bg-tone-light').length")
        result['mixed'] = window.evaluate_js(
            "document.querySelectorAll('.bg-ink-mixed').length")
        result['adaptive'] = window.evaluate_js(
            "document.body.classList.contains('adaptive-bg')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['before'] >= 8, r
    assert r['after'] == 0, '清掉背景后不该留下明暗类：%r' % r['after']
    assert r['mixed'] == 0, '正文描边也是上一次图片的产物，要一并摘掉：%r' % r['mixed']
    assert r['adaptive'] is False, r


# ---------------- 实机：切换主题不能把自适应层抹掉（用户报的"突兀的一块"） ----------------

@pytest.mark.e2e
def test_rich_text_body_ink_follows_tone(tmp_path, monkeypatch):
    """富文本**正文**的字色也必须跟随薄纱。

    真机实测踩到的坑：`.ql-editor` 自己没有 `color` 声明（颜色是从祖先继承的），
    `.bg-tone-*` 只重定义变量 —— 于是深色图上 `.ql-editor` 带着 `bg-tone-dark`，
    算出来的字色却还是主题的深墨（正文 vs 背景对比度实测 1.28:1）。
    md 源码 / md 预览都自己声明了 `color: var(--text-primary)`，只有富文本会踩。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'dark.png'), (600, 400), (14, 16, 24))
    nid = backend.api.notes_create()['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    backend.api.notes_update(nid, {
        'title': '富文本正文', 'bg_type': 'image', 'bg_value': img, 'bg_opacity': 1.0,
        'bg_zoom': 200,
        'content': json.dumps({'ops': [{'insert': '正文一行\n'}]}, ensure_ascii=False)})

    def actions(window, result):
        time.sleep(2.5)
        result['ql_classes'] = window.evaluate_js(
            "document.querySelector('.ql-editor').className")
        result['ink'] = window.evaluate_js(
            "getComputedStyle(document.querySelector('.ql-editor')).color")
        # 清掉背景图 → 字色要回到主题墨色，不能留着上一张图的亮字
        window.evaluate_js("document.getElementById('btn-clear-note-bg').click();")
        time.sleep(1.6)
        result['ink_after'] = window.evaluate_js(
            "getComputedStyle(document.querySelector('.ql-editor')).color")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert 'bg-tone-dark' in r['ql_classes'], r['ql_classes']
    assert min(_rgb(r['ink'])) > 200, \
        '深色图上富文本正文必须是亮字（否则就是深底深字）：%r' % r['ink']
    assert max(_rgb(r['ink_after'])) < 90, \
        '清掉背景后字色要回到主题墨色：%r' % r['ink_after']


@pytest.mark.e2e
def test_theme_switch_keeps_adaptive_layer(tmp_path, monkeypatch):
    """点主题 = 重算自适应层，**不是**清掉它。

    复现的 bug：图挂在**笔记层**（全局背景仍是默认的「主题色」）时，点主题会走到
    applyGlobalBackground() 的 color 分支 → clearAdaptiveUI()：
      · .ql-toolbar 拿回自带的 55% 白玻璃 → 一条硬边亮块（截图实测带内 RGB(190,200,212)
        压在 RGB(100,117,138) 的图上，差 90 个色阶）；
      · 薄纱 display:none；
      · 墨色回落成主题色 → 深图上暗底暗字。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'dark.png'), (600, 400), (14, 16, 24))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '切主题', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_opacity': 1.0,
                                   'bg_zoom': 200})

    def actions(window, result):
        time.sleep(2.5)
        result['clicked'] = _click_theme(window, 'cream')
        time.sleep(2.0)
        result['theme'] = window.evaluate_js("document.body.getAttribute('data-theme')")
        result['adaptive'] = window.evaluate_js(
            "document.body.classList.contains('adaptive-bg')")
        result['ql_bg'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('editor-toolbar')).backgroundColor")
        result['tone'] = window.evaluate_js(
            "document.getElementById('title-row').classList.contains('bg-tone-dark')")
        result['ink'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('editor-status')).color")
        result['chrome_display'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('chrome-veil')).display")
        result['content_display'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('content-veil')).display")
        result['layers'] = window.evaluate_js(
            "document.getElementById('note-bg-layer').style.backgroundImage.slice(0, 12)")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['clicked'] is True, '主题选项没点到'
    assert r['theme'] == 'cream', r
    assert r['adaptive'] is True, '切换主题不该摘掉 adaptive-bg：%r' % r
    assert r['tone'] is True, '深色图上的界面行必须还是亮字：%r' % r
    assert min(_rgb(r['ink'])) > 200, '墨色不该回落到主题色（深底暗字）：%r' % r['ink']
    assert r['ql_bg'] in ('rgba(0, 0, 0, 0)', 'transparent'), \
        'Quill 工具栏的白玻璃必须继续让位给薄纱（否则就是那块硬边亮块）：%r' % r['ql_bg']
    assert r['chrome_display'] == 'block' and r['content_display'] == 'block', r
    assert r['layers'].startswith('url('), '背景图应当还在（只是重算配色）：%r' % r['layers']
    assert backend.api.settings_get('theme') == 'cream'


@pytest.mark.e2e
def test_theme_switch_recomputes_band_sampling(tmp_path, monkeypatch):
    """主题换了 → --bg-editor 换了 → "图没盖满处"的明暗判定要跟着翻（重算，而不是用旧结果）。

    构造：全局背景是**中性灰**图 + 缩放 50%，图只占中间一块，标题那条带整条都是露出来的
    主题底色。白色主题下它是浅色（配深字），深色主题下它是暗色（配亮字）——
    这条带一翻，就说明切换主题后确实重新采样了（旧结果会一直停在 light）。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'gray.png'), (600, 400), (120, 120, 120))
    backend.api.settings_set('bg_type', 'image')
    backend.api.settings_set('bg_value', img)
    backend.api.settings_set('bg_zoom', '50')
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '盖不满', 'content': '正文\n', 'bg_type': 'global'})

    def actions(window, result):
        time.sleep(2.5)
        result['light'] = window.evaluate_js(
            "document.getElementById('title-row').classList.contains('bg-tone-light')")
        _click_theme(window, 'dark')
        time.sleep(2.0)
        result['dark'] = window.evaluate_js(
            "document.getElementById('title-row').classList.contains('bg-tone-dark')")
        result['adaptive'] = window.evaluate_js(
            "document.body.classList.contains('adaptive-bg')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['light'] is True, '白色主题下露出来的是浅色纸面 → 该配深字：%r' % r
    assert r['dark'] is True, '深色主题下露出来的是暗色纸面 → 该配亮字（说明重新采样了）：%r' % r
    assert r['adaptive'] is True, r


# ---------------- 实机：正文薄纱（第三片） ----------------

@pytest.mark.e2e
def test_content_scrim_slider_and_storage(tmp_path, monkeypatch):
    """正文薄纱滑杆：落到 --ad-content-scrim、写进笔记，0% 就是原来的纯沉浸式。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'bg.png'), (600, 400), (30, 40, 60))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '正文薄纱', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_zoom': 200})

    def actions(window, result):
        time.sleep(2.5)
        result['default'] = window.evaluate_js(
            "getComputedStyle(document.documentElement).getPropertyValue('--ad-content-scrim').trim()")
        result['display'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('content-veil')).display")
        _set_input(window, '#note-content-scrim', 70)
        time.sleep(0.8)
        result['scrim'] = window.evaluate_js(
            "getComputedStyle(document.documentElement).getPropertyValue('--ad-content-scrim').trim()")
        result['veil_bg'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('content-veil')).backgroundColor")
        _set_input(window, '#note-content-scrim', 0)
        time.sleep(0.8)
        result['off'] = window.evaluate_js(
            "getComputedStyle(document.getElementById('content-veil')).backgroundColor")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    # 笔记没单独设过 → 跟随全局默认 30%
    assert r['default'] == '0.3', r
    assert r['display'] == 'block', r
    assert r['scrim'] == '0.7', r
    assert '0.7' in r['veil_bg'].replace(' ', ''), r['veil_bg']
    assert '0' in r['off'].replace(' ', '') and '0.7' not in r['off'], \
        '拉到 0%% 应当完全透明（回到沉浸式）：%r' % r['off']
    note = backend.api.notes_get(nid)
    assert abs(float(note['content_scrim']) - 0.0) < 1e-6, '最后一次拖动（0%%）要落库：%r' % note


@pytest.mark.e2e
def test_three_veils_fades_align(tmp_path, monkeypatch):
    """三片薄纱的渐隐段必须**首尾重合**，否则 alpha 相加处会出现新的硬边。

    可断言的几何不变量（相对 #editor-container 顶边）：
      顶部薄纱的不透明区结束 = 内容区顶边 = 正文薄纱渐入结束
      底部薄纱的渐隐开始 = 状态栏顶边 = 正文薄纱渐出开始
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _solid_png(os.path.join(str(tmp_path), 'bg.png'), (600, 400), (40, 60, 90))
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '接缝', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': img, 'bg_zoom': 200})

    def actions(window, result):
        time.sleep(2.5)
        result['geom'] = json.loads(_veil_geometry(window))

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    g = r['geom']
    assert abs(g['chromeBottom'] - 48 - g['inkTop']) <= 1, \
        '顶部薄纱的不透明区结束应当正好在内容区顶边：%r' % g
    assert abs(g['contentTop'] + 48 - g['inkTop']) <= 1, \
        '正文薄纱要在同一点渐入结束：%r' % g
    assert abs(g['contentBottom'] - 40 - g['statusTop']) <= 1, \
        '正文薄纱要在状态栏顶边前 40px 淡完：%r' % g
    assert abs(g['bottomTop'] + 40 - g['statusTop']) <= 1, \
        '底部薄纱要在同一点开始渐隐：%r' % g


@pytest.mark.e2e
def test_mixed_image_adds_ink_stroke(tmp_path, monkeypatch):
    """正文带一明一暗（上白下黑）→ 给正文挂描边兜底；纯色图不该挂（保持干净字形）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    from PIL import Image

    import backend
    mixed = os.path.join(str(tmp_path), 'mixed.png')
    im = Image.new('RGB', (600, 400), (250, 250, 250))
    im.paste((12, 14, 20), (0, 200, 600, 400))       # 下半张是暗的：正文带跨了两段
    im.save(mixed)
    solid = _solid_png(os.path.join(str(tmp_path), 'solid.png'), (600, 400), (14, 16, 24))
    # 先建纯色那篇，再建混合底那篇：列表按 updated_at DESC，最后建的会被自动选中
    plain_id = backend.api.notes_create()['id']
    backend.api.notes_update(plain_id, {'title': '纯色底', 'content': '正文\n',
                                        'bg_type': 'image', 'bg_value': solid, 'bg_zoom': 200})
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '混合底', 'content': '正文\n',
                                   'bg_type': 'image', 'bg_value': mixed, 'bg_zoom': 200})

    def click_note(window, title):
        return window.evaluate_js(
            "(function(){var it=document.querySelectorAll('.note-item');"
            "for(var i=0;i<it.length;i++){if(it[i].textContent.indexOf(%s)>=0){it[i].click();return true;}}"
            "return false;})()" % json.dumps(title))

    def actions(window, result):
        time.sleep(2.5)
        result['mixed'] = window.evaluate_js(
            "document.querySelectorAll('.bg-ink-mixed').length")
        result['stroke'] = window.evaluate_js(
            "getComputedStyle(document.querySelector('#md-editor')).webkitTextStrokeWidth")
        result['paint'] = window.evaluate_js(
            "getComputedStyle(document.querySelector('#md-editor')).paintOrder")
        # 切到纯色底那篇：两段判定一致 → 描边要摘掉
        result['switched'] = click_note(window, '纯色底')
        time.sleep(1.8)
        result['solid_mixed'] = window.evaluate_js(
            "document.querySelectorAll('.bg-ink-mixed').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['mixed'] >= 1, '上亮下暗的图必须给正文挂 .bg-ink-mixed：%r' % r
    assert r['stroke'] not in ('0px', ''), '描边宽度要有值：%r' % r['stroke']
    assert r['paint'] == 'stroke', 'paint-order 必须是 stroke（描边画在字形下面）：%r' % r['paint']
    assert r['switched'] is True, '没切到纯色底那篇笔记'
    assert r['solid_mixed'] == 0, '纯色图两段判定一致，不该留描边：%r' % r['solid_mixed']

