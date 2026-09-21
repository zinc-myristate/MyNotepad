# -*- coding: utf-8 -*-
"""Markdown 工具栏的实机回归：点按钮 → 源码被正确改写 → 预览跟着变。

工具栏的正确性只有真点才知道（按钮的 data-md 路由、CodeMirror 选区操作、
前缀开关行为）。这里覆盖纯编辑动作；图片/附件按钮要走系统文件对话框，不在自动化里点。
"""
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('md 工具栏回归', RENDERER + '/index.html',
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


SRC = "document.querySelector('.CodeMirror').CodeMirror.getValue()"


def _click(window, action):
    window.evaluate_js("document.querySelector('[data-md=\"%s\"]').click();" % action)


def _set_cursor(window, line, ch=0):
    window.evaluate_js(
        "document.querySelector('.CodeMirror').CodeMirror.setCursor({line:%d, ch:%d});" % (line, ch))


@pytest.mark.e2e
def test_toolbar_visible_only_for_markdown(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()                    # 新建 = Markdown

    def actions(window, result):
        time.sleep(1.5)
        result['md_toolbar'] = window.evaluate_js(
            "!document.getElementById('md-toolbar').classList.contains('hidden')")
        result['quill_toolbar'] = window.evaluate_js(
            "!!document.querySelector('.ql-toolbar')?.classList.contains('hidden')")
        result['buttons'] = window.evaluate_js(
            "document.querySelectorAll('#md-toolbar [data-md]').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['md_toolbar'] is True, 'Markdown 笔记应显示 Markdown 工具栏'
    assert r['quill_toolbar'] is True, 'Markdown 笔记下 Quill 工具栏应隐藏'
    assert r['buttons'] >= 15, '工具栏按钮数量异常：%r' % r['buttons']


@pytest.mark.e2e
def test_toolbar_edits_source_and_preview(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.setValue('hello world\\n');")
        time.sleep(0.4)

        # 加粗：选中 world → Ctrl+B 等价路径（工具栏按钮）
        _set_cursor(window, 0, 6)
        window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.setSelection("
            "{line:0,ch:6},{line:0,ch:11});")
        _click(window, 'bold')
        result['after_bold'] = window.evaluate_js(SRC)

        # 标题：光标在第 0 行 → H2 前缀
        _set_cursor(window, 0, 0)
        _click(window, 'h2')
        result['after_h2'] = window.evaluate_js(SRC)

        # 再点一次应把前缀去掉（开关语义）
        _click(window, 'h2')
        result['after_h2_twice'] = window.evaluate_js(SRC)

        # 待办 + 表格 + 分割线
        _click(window, 'todo')
        result['after_todo'] = window.evaluate_js(SRC)
        _click(window, 'table')
        _click(window, 'divider')
        time.sleep(0.8)
        result['source'] = window.evaluate_js(SRC)
        result['preview'] = window.evaluate_js(
            "document.getElementById('md-preview').innerHTML")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert '**world**' in r['after_bold'], r['after_bold']
    assert r['after_h2'].startswith('## '), r['after_h2']
    assert not r['after_h2_twice'].startswith('## '), '再点一次应去掉标题前缀：%r' % r['after_h2_twice']
    assert '- [ ] ' in r['after_todo'], r['after_todo']
    assert '| 列一 | 列二 |' in r['source']
    assert '<hr class="divider-1">' in r['source']
    # 预览应把这些真的渲染出来
    assert '<table>' in r['preview'] and 'md-task' in r['preview'], r['preview'][:200]
