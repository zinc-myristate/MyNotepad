# -*- coding: utf-8 -*-
"""第二组功能的实机回归（无头 pywebview）：Ctrl+P 快速跳转 / 复制笔记按钮 / 导出范围标签。

这些功能的正确性有一半在**交互**上（快捷键是否抢了浏览器默认、面板是否真的能筛、按钮是否
接了正确的 data-* 路由），只靠后端单测覆盖不到。
"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('二组回归', os.path.join(PROJECT_ROOT, 'renderer', 'index.html'),
                                   js_api=ns['api'], width=1100, height=750)

    def runner():
        try:
            time.sleep(wait_before)
            actions(window, result)
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


@pytest.mark.e2e
def test_quick_switch_filters_and_opens(tmp_path, monkeypatch):
    """Ctrl+P：面板打开 → 输入过滤 → 回车打开对应笔记"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    a = backend.api.notes_create()['id']
    backend.api.notes_update(a, {'title': '高数笔记', 'content': '{"ops":[{"insert":"拉格朗日\\n"}]}'})
    b = backend.api.notes_create()['id']
    backend.api.notes_update(b, {'title': '菜谱', 'content': '{"ops":[{"insert":"红烧肉\\n"}]}'})

    def actions(window, result):
        # 用真实按键事件触发（而不是直接调函数），才能证明快捷键真的接上了
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'p', ctrlKey:true, bubbles:true, cancelable:true}));")
        time.sleep(0.6)
        result['opened'] = window.evaluate_js(
            "document.getElementById('quick-switch-panel').style.display")
        result['count_all'] = window.evaluate_js(
            "document.querySelectorAll('#quick-switch-list .quick-switch-item').length")
        window.evaluate_js(
            "var i = document.getElementById('quick-switch-input'); i.value = '菜';"
            "i.dispatchEvent(new Event('input', {bubbles:true}));")
        time.sleep(0.4)
        result['titles'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#quick-switch-list .qs-title')]"
            ".map(e => e.textContent))")
        result['marked'] = window.evaluate_js(
            "document.querySelectorAll('#quick-switch-list mark').length")
        window.evaluate_js(
            "document.getElementById('quick-switch-input')"
            ".dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));")
        time.sleep(1.2)
        result['panel_after'] = window.evaluate_js(
            "document.getElementById('quick-switch-panel').style.display")
        result['active'] = window.evaluate_js("__app.state.activeNoteId")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['opened'] == 'flex', 'Ctrl+P 应打开快速跳转面板'
    assert res['count_all'] == 2, '空查询应列出全部笔记'
    assert json.loads(res['titles']) == ['菜谱'], '输入「菜」后只应剩菜谱'
    assert res['marked'] >= 1, '命中字符应被 <mark> 高亮'
    assert res['panel_after'] == 'none', '回车后应关闭面板'
    assert res['active'] == b, '应打开选中的那篇笔记'


@pytest.mark.e2e
def test_quick_switch_esc_and_fuzzy(tmp_path, monkeypatch):
    """Esc 关闭；模糊匹配是**子序列**（跳过中间字符也能命中，不是简单 substring）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': 'Markdown 19 条技巧',
                                   'content': '{"ops":[{"insert":"x\\n"}]}'})
    other = backend.api.notes_create()['id']
    backend.api.notes_update(other, {'title': '菜谱', 'content': '{"ops":[{"insert":"y\\n"}]}'})

    def actions(window, result):
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'p', ctrlKey:true, bubbles:true, cancelable:true}));")
        time.sleep(0.5)
        # "m19" 不是 "Markdown 19 条技巧" 的连续子串，但字符按顺序出现 → 子序列应命中
        window.evaluate_js(
            "var i = document.getElementById('quick-switch-input'); i.value = 'm19';"
            "i.dispatchEvent(new Event('input', {bubbles:true}));")
        time.sleep(0.4)
        result['fuzzy'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#quick-switch-list .qs-title')]"
            ".map(e => e.textContent))")
        window.evaluate_js(
            "document.getElementById('quick-switch-input')"
            ".dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));")
        time.sleep(0.4)
        result['closed'] = window.evaluate_js(
            "document.getElementById('quick-switch-panel').style.display")

    res = _run(ns, actions)
    assert 'error' not in res, res
    titles = json.loads(res['fuzzy'])
    assert titles == ['Markdown 19 条技巧'], '子序列 m19 应命中；实际 %r' % titles
    assert res['closed'] == 'none', 'Esc 应关闭面板'


@pytest.mark.e2e
def test_duplicate_button_copies_note(tmp_path, monkeypatch):
    """列表里的复制按钮：点一下多出一篇「xx 副本」"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '原始笔记', 'content': '{"ops":[{"insert":"正文\\n"}]}'})

    def actions(window, result):
        result['before'] = window.evaluate_js(
            "document.querySelectorAll('.note-item').length")
        window.evaluate_js(
            "document.querySelector('[data-copy-id=\"%s\"]').click()" % nid)
        time.sleep(2.0)
        result['after'] = window.evaluate_js(
            "document.querySelectorAll('.note-item').length")
        result['titles'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.note-item-title')]"
            ".map(e => e.textContent))")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['after'] == res['before'] + 1, '复制后列表应多一篇'
    assert any('副本' in t for t in json.loads(res['titles'])), '应出现「… 副本」'


@pytest.mark.e2e
def test_scope_export_buttons_disabled_without_filter(tmp_path, monkeypatch):
    """没在筛选时，按笔记本/标签导出的按钮应置灰并说明原因"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '甲', 'content': '{"ops":[{"insert":"x\\n"}]}'})

    def actions(window, result):
        window.evaluate_js("document.getElementById('btn-export').click()")
        time.sleep(0.8)
        result['nb_disabled'] = window.evaluate_js(
            "document.getElementById('export-scope-notebook').classList.contains('is-disabled')")
        result['tag_disabled'] = window.evaluate_js(
            "document.getElementById('export-scope-tag').classList.contains('is-disabled')")
        result['nb_desc'] = window.evaluate_js(
            "document.getElementById('export-scope-notebook-desc').textContent")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['nb_disabled'] is True and res['tag_disabled'] is True
    assert '先在侧边栏选择' in res['nb_desc'], res['nb_desc']


@pytest.mark.e2e
def test_autolock_setting_visible_and_persists(tmp_path, monkeypatch):
    """密码面板里的自动锁定下拉：改一下要落库"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '甲', 'content': '{"ops":[{"insert":"x\\n"}]}'})

    def actions(window, result):
        window.evaluate_js("__app.dom.state = null;") if False else None
        window.evaluate_js(
            "var s = document.getElementById('sel-autolock'); s.value = '5';"
            "s.dispatchEvent(new Event('change', {bubbles:true}));")
        time.sleep(0.6)
        result['value'] = window.evaluate_js(
            "document.getElementById('sel-autolock').value")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['value'] == '5', '下拉应保持所选值'

    import sqlite3
    conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
    row = conn.execute("SELECT value FROM settings WHERE key='auto_lock_minutes'").fetchone()
    conn.close()
    assert row and row[0] == '5', '自动锁定设置应落库'
