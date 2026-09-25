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


# ---------------- 单测：后端字段 ----------------

def test_note_has_blur_and_scrim_columns(api, backend_mod):
    nid = api.notes_create()['id']
    note = api.notes_get(nid)
    assert 'bg_blur' in note and 'ui_scrim' in note
    api.notes_update(nid, {'bg_blur': 8, 'ui_scrim': 0.5})
    note = api.notes_get(nid)
    assert float(note['bg_blur']) == 8
    assert abs(float(note['ui_scrim']) - 0.5) < 1e-6


def test_settings_defaults(api, backend_mod):
    all_settings = api.settings_get_all()
    assert all_settings.get('bg_blur') == '0'
    assert all_settings.get('ui_scrim') == '0.3'


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
        result['adaptive'] = window.evaluate_js(
            "document.body.classList.contains('adaptive-bg')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['before'] >= 8, r
    assert r['after'] == 0, '清掉背景后不该留下明暗类：%r' % r['after']
    assert r['adaptive'] is False, r
