# -*- coding: utf-8 -*-
"""格式徽标的实机回归：点徽标 → 确认框 → 转换 → 徽标/编辑器切换 → 还原原始富文本。

为什么要在浏览器里测：这条链路的正确性几乎全在**交互**上——徽标文案是否按状态变化、
确认框是否真的弹出并回传 promise、转换后编辑器是否切换、列表预览是否刷新。
后端单测只能证明数据层对。
"""
import json
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note, wait_for_js

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('格式徽标回归', RENDERER + '/index.html',
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


DELTA = json.dumps({
    'ops': [
        {'insert': '原始标题'},
        {'insert': '\n', 'attributes': {'header': 1}},
        {'insert': '红色字', 'attributes': {'color': '#B8844A'}},
        {'insert': '\n'},
    ]
}, ensure_ascii=False)


@pytest.mark.e2e
def test_badge_converts_to_markdown_then_restores(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = make_delta_note(backend, '格式测试', DELTA)

    def click_badge(window):
        window.evaluate_js("document.getElementById('btn-note-format').click();")

    def confirm(window):
        window.evaluate_js("document.getElementById('btn-confirm-ok').click();")

    def actions(window, result):
        # 等启动自动选中 + 徽标刷新完：CI 上固定 sleep 读到的可能还是启动前的 'MD'
        result['badge_before'] = wait_for_js(
            window, "document.getElementById('btn-note-format').textContent", '富文本')
        click_badge(window)
        time.sleep(0.9)
        result['dialog_visible'] = window.evaluate_js(
            "document.getElementById('confirm-dialog').style.display")
        result['dialog_text'] = window.evaluate_js(
            "document.getElementById('confirm-message').textContent")
        confirm(window)
        # 轮询到状态真的切过去为止（固定 sleep 在 CI 上会读到转换还没落地的中间态）
        result['format_after'] = wait_for_js(window, "window.__app.state.noteFormat", 'md')
        result['badge_after'] = wait_for_js(
            window, "document.getElementById('btn-note-format').textContent", 'MD')
        result['md_visible'] = window.evaluate_js(
            "!document.getElementById('md-editor').classList.contains('hidden')")
        result['source'] = window.evaluate_js(
            "(document.querySelector('.CodeMirror')||{CodeMirror:null}).CodeMirror"
            " ? document.querySelector('.CodeMirror').CodeMirror.getValue() : ''")
        result['preview_has_color'] = window.evaluate_js(
            "(document.getElementById('md-preview').innerHTML||'').indexOf('color') >= 0")
        # 再点一次：此时应提示"还原原始富文本"
        click_badge(window)
        time.sleep(0.9)
        result['dialog_text2'] = window.evaluate_js(
            "document.getElementById('confirm-message').textContent")
        confirm(window)
        result['format_restored'] = wait_for_js(window, "window.__app.state.noteFormat", 'delta')
        result['quill_visible'] = window.evaluate_js(
            "!document.getElementById('quill-editor').classList.contains('hidden')")
        result['quill_text'] = window.evaluate_js(
            "document.querySelector('.ql-editor').innerText.trim()")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')

    assert r['badge_before'] == '富文本', 'delta 笔记的徽标应显示"富文本"，实际 %r' % r['badge_before']
    assert r['dialog_visible'] == 'flex', '确认框没有弹出'
    assert 'Markdown' in r['dialog_text'] and '备份' in r['dialog_text'], r['dialog_text']

    assert r['format_after'] == 'md', '转换后前端状态没切到 md'
    assert r['badge_after'] == 'MD'
    assert r['md_visible'], '转换后应显示 Markdown 双栏'
    assert '# 原始标题' in r['source'], '源码里应是转换出来的 Markdown，实际 %r' % r['source']
    assert 'color' in r['source'] and r['preview_has_color'], '颜色应保留为内联 HTML 并渲染出来'

    assert '还原' in r['dialog_text2'], '有备份时应提示无损还原，实际 %r' % r['dialog_text2']
    assert r['format_restored'] == 'delta', '还原后应回到富文本'
    assert r['quill_visible'], '还原后应显示 Quill'
    assert '原始标题' in r['quill_text'] and '红色字' in r['quill_text']

    # 数据层：内容必须与转换前逐字节一致
    assert json.loads(backend.api.notes_get(nid)['content']) == json.loads(DELTA), \
        '无损还原必须逐字节回到原始 Delta'


@pytest.mark.e2e
def test_badge_hidden_without_note_and_reports_lossy_path(tmp_path, monkeypatch):
    """没有笔记时徽标隐藏；Markdown 笔记（无备份）转富文本要提示"有损" """
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        wait_for_js(window, "!!(window.__app && window.__app.state)")   # 等应用启动完
        result['hidden_no_note'] = window.evaluate_js(
            "document.getElementById('btn-note-format').classList.contains('hidden')")
        window.evaluate_js("document.getElementById('btn-new-note').click();")
        time.sleep(2.0)
        window.evaluate_js(
            "(function(){var w=document.querySelector('.CodeMirror');"
            "w.CodeMirror.setValue('# 新笔记\\n\\n内容\\n');})()")
        time.sleep(1.0)
        result['badge'] = window.evaluate_js(
            "document.getElementById('btn-note-format').textContent")
        window.evaluate_js("document.getElementById('btn-note-format').click();")
        time.sleep(0.9)
        result['dialog_text'] = window.evaluate_js(
            "document.getElementById('confirm-message').textContent")
        result['cancel'] = window.evaluate_js(
            "document.getElementById('btn-confirm-cancel').click(); 'cancelled'")
        time.sleep(0.6)
        result['format_unchanged'] = window.evaluate_js("window.__app.state.noteFormat")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['hidden_no_note'] is True, '没有笔记时格式徽标应隐藏'
    assert r['badge'] == 'MD'
    assert '有损' in r['dialog_text'], '无备份的 Markdown 转富文本必须明确说明有损'
    assert r['format_unchanged'] == 'md', '取消后不得改动格式'
