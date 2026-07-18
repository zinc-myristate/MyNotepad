# -*- coding: utf-8 -*-
"""无头 pywebview 端到端：驱动真实前端验证保存链路（需要显示环境，默认跳过）"""
import sqlite3
import threading
import time

import pytest

from conftest import PROJECT_ROOT, load_app_partial

pytestmark = pytest.mark.e2e


def _run_window(ns, js_actions, wait_before=7, wait_after=2):
    """启动无头窗口，执行 js_actions(window, result) 后销毁。挂载与生产一致的 closing 兜底。"""
    import webview
    result = {}
    window = webview.create_window(
        "E2E测试", PROJECT_ROOT + r'\renderer\index.html',
        js_api=ns['api'], width=900, height=600)
    import backend
    window.events.closing += ns['make_closing_handler'](window, backend.api)

    def runner():
        try:
            time.sleep(wait_before)  # 等 app.js 初始化并自动选中第一篇笔记
            js_actions(window, result)
            time.sleep(wait_after)
        except Exception as e:
            result['error'] = repr(e)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


def test_title_only_edit_persists(tmp_path, monkeypatch):
    """纯标题修改（不动正文）经 500ms 防抖后必须落库——回归保护（曾因去重基线 bug 丢失）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    test_title = '标题落库验证_' + str(int(time.time()))

    def actions(window, result):
        active = window.evaluate_js("state.activeNoteId")
        result['active'] = active
        assert active == nid, f'前端未自动选中预置笔记: {active}'
        window.evaluate_js(
            "dom.titleInput.value = %r;"
            "dom.titleInput.dispatchEvent(new Event('input', {bubbles:true}));" % test_title)
        result['dot_dirty'] = window.evaluate_js(
            "document.getElementById('save-dot').classList.contains('dirty')")
        time.sleep(2)  # > 500ms 防抖
        result['dot_saved'] = window.evaluate_js(
            "!document.getElementById('save-dot').classList.contains('dirty')"
            " && !document.getElementById('save-dot').classList.contains('error')")
        conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
        result['db_title'] = conn.execute(
            "SELECT title FROM notes WHERE id=?", (nid,)).fetchone()[0]
        conn.close()

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert result['db_title'] == test_title
    assert result['dot_dirty'] is True, '输入后圆点应为未保存态'
    assert result['dot_saved'] is True, '防抖保存后圆点应回到已保存态'


def test_closing_flush_saves_last_edits(tmp_path, monkeypatch):
    """关窗兜底：输入后不等 500ms 防抖立即关窗，closing 事件同步落库不丢输入"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    test_title = '关窗兜底验证_' + str(int(time.time()))

    def actions(window, result):
        active = window.evaluate_js("state.activeNoteId")
        assert active == nid, f'前端未自动选中预置笔记: {active}'
        window.evaluate_js(
            "dom.titleInput.value = %r;"
            "dom.titleInput.dispatchEvent(new Event('input', {bubbles:true}));" % test_title)
        # 不 sleep：立即销毁（防抖 500ms 定时器不会触发，全靠 closing 兜底）

    result = _run_window(ns, actions, wait_after=0)
    assert 'error' not in result, result
    conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
    saved_title = conn.execute("SELECT title FROM notes WHERE id=?", (nid,)).fetchone()[0]
    conn.close()
    assert saved_title == test_title, '关窗前最后的输入应由 closing 兜底落库'


def test_search_filters_note_list(tmp_path, monkeypatch):
    """搜索框驱动后端 notes_search：正文命中显示、未命中隐藏"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    a = backend.api.notes_create()['id']
    backend.api.notes_update(a, {'title': '苹果笔记', 'content': '{"ops":[{"insert":"这里讲水果种植技术\\n"}]}'})
    b = backend.api.notes_create()['id']
    backend.api.notes_update(b, {'title': '汽车笔记', 'content': '{"ops":[{"insert":"这里讲发动机保养\\n"}]}'})

    def actions(window, result):
        window.evaluate_js(
            "dom.searchInput.value = '水果种植';"
            "dom.searchInput.dispatchEvent(new Event('input', {bubbles:true}));")
        time.sleep(1.5)  # > 200ms 搜索防抖 + 桥接往返
        result['hidden'] = window.evaluate_js(
            "JSON.stringify([...dom.noteList.querySelectorAll('.note-item')]"
            ".map(el => [el.dataset.noteId, el.classList.contains('hidden-by-search')]))")

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    import json
    hidden = dict(json.loads(result['hidden']))
    assert hidden[a] is False, '正文命中的笔记不应被隐藏'
    assert hidden[b] is True, '未命中的笔记应被隐藏'
