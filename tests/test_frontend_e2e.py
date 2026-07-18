# -*- coding: utf-8 -*-
"""无头 pywebview 端到端：驱动真实前端验证保存链路（需要显示环境，默认跳过）"""
import sqlite3
import threading
import time

import pytest

from conftest import PROJECT_ROOT, load_app_partial

pytestmark = pytest.mark.e2e


def _run_window(ns, js_actions, wait_before=7, wait_after=2):
    """启动无头窗口，执行 js_actions(window, result) 后销毁。"""
    import webview
    result = {}
    window = webview.create_window(
        "E2E测试", PROJECT_ROOT + r'\renderer\index.html',
        js_api=ns['api'], width=900, height=600)

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
        time.sleep(2)  # > 500ms 防抖
        conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
        result['db_title'] = conn.execute(
            "SELECT title FROM notes WHERE id=?", (nid,)).fetchone()[0]
        conn.close()

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert result['db_title'] == test_title
