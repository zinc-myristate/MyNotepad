# -*- coding: utf-8 -*-
"""Markdown 工具栏的实机回归：点按钮 → 源码被正确改写 → 预览跟着变。

工具栏的正确性只有真点才知道（按钮的 data-md 路由、CodeMirror 选区操作、
前缀开关行为）。这里覆盖纯编辑动作；图片/附件按钮要走系统文件对话框，不在自动化里点。
"""
import json
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_app_ready, wait_for_js

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=None):
    import webview
    result = {}
    window = webview.create_window('md 工具栏回归', RENDERER + '/index.html',
                                   js_api=ns['api'], width=1200, height=800)

    def runner():
        try:
            if wait_before is None:
                wait_app_ready(window)        # 等启动完（以前固定等 8 秒）
            else:
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
        result['menu'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#md-insert-menu [data-md]')].map(b => b.dataset.md))")
        # 默认窗口量一次，再缩窄量一次（1000×700：这么窄放不下一行，也接近 CI 的样子）
        result['fit'] = [json.loads(window.evaluate_js(TOOLBAR_FIT))]
        window.resize(1000, 700)
        wait_for_js(window, 'innerWidth < 1010', True, timeout=3)
        time.sleep(0.4)
        result['fit'].append(json.loads(window.evaluate_js(TOOLBAR_FIT)))

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['md_toolbar'] is True, 'Markdown 笔记应显示 Markdown 工具栏'
    assert r['quill_toolbar'] is True, 'Markdown 笔记下 Quill 工具栏应隐藏'
    assert r['buttons'] == 14, '工具栏按钮数量异常：%r' % r['buttons']
    assert r['menu'] == '["codeblock","table","divider","attachment"]', r['menu']
    for m in r['fit']:
        assert m['squeezed'] == 0, '有按钮被压扁了（字从按钮里溢出来）：%r' % m
        if abs(m['need'] - m['room']) >= 3:              # 刚好卡在边上的那一两像素不判
            assert (m['spread'] < 10) == (m['need'] <= m['room']), '放得下就该是一行，放不下才折行：%r' % m
    # 默认窗口（1200×800，内宽约 1187）要放得下一行（以前折成两行，第二行只剩「公式」）。
    # CI 的屏幕只有 1024×768，1200×800 的窗口会被夹小，量不到这一档
    wide = [m for m in r['fit'] if m['inner'] >= 1180]
    assert all(m['need'] <= m['room'] and m['spread'] < 10 for m in wide), wide


# 工具栏要多宽（各按钮 / 分隔线的宽度 + 间距）、有多宽、按钮上沿相差多少
TOOLBAR_FIT = r"""JSON.stringify((() => {
  const bar = document.getElementById('md-toolbar'), cs = getComputedStyle(bar);
  const kids = [...bar.children].filter(el => el.getClientRects().length);
  const width = el => { const s = getComputedStyle(el);
    return el.getBoundingClientRect().width + parseFloat(s.marginLeft) + parseFloat(s.marginRight); };
  const need = kids.reduce((sum, el) => sum + width(el), 0) + (parseFloat(cs.columnGap) || 0) * (kids.length - 1);
  const room = bar.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const buttons = [...bar.querySelectorAll('button')];
  const tops = buttons.map(b => b.getBoundingClientRect().top);
  return {need: Math.round(need), room: Math.round(room), inner: innerWidth,
          spread: Math.round(Math.max(...tops) - Math.min(...tops)),
          // 不折行时按钮会被压窄：中文逐字换行，按钮高度是定的，字从下面溢出来。那样"放得下"是假的
          squeezed: buttons.filter(b => b.scrollWidth > b.clientWidth + 1 || b.scrollHeight > b.clientHeight + 1).length};
})())"""


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

        # 待办 + 表格 + 分割线（后两个在「插入」菜单里：先点开菜单再点那一项）
        _click(window, 'todo')
        result['after_todo'] = window.evaluate_js(SRC)
        for action in ('table', 'divider'):
            window.evaluate_js("document.getElementById('md-insert-btn').click()")
            assert window.evaluate_js("!document.getElementById('md-insert-menu').classList.contains('hidden')")
            window.evaluate_js("document.querySelector('#md-insert-menu [data-md=\"%s\"]').click()" % action)
            assert window.evaluate_js("document.getElementById('md-insert-menu').classList.contains('hidden')")
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
