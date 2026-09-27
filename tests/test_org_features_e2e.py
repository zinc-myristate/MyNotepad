# -*- coding: utf-8 -*-
"""第 7 轮「组织力」的实机回归：待办面板 / 命令面板 / 保存的搜索 / Ctrl+F 规则。

这一层必须真跑浏览器：面板的事件委托、Ctrl+P 面板的模式切换、写回后的刷新，
单测覆盖不到；而写回本身（后端）已有单测，这里验的是"点了真的会变"。
"""
import datetime
import json
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_for_js

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('组织力回归', RENDERER + '/index.html',
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


def _md_note(api, title, text):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': text})
    return nid


@pytest.mark.e2e
def test_todo_panel_lists_and_toggles(tmp_path, monkeypatch):
    """打开待办面板 → 勾选一条 → 原文真的变成 [x] 且面板数字跟着变"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md_note(backend.api, '清单', '- [ ] 先做\n- [ ] 后做\n')

    def actions(window, result):
        time.sleep(1.2)
        window.evaluate_js("document.getElementById('btn-todos').click();")
        time.sleep(1.2)
        result['panel'] = window.evaluate_js("document.getElementById('todo-panel').style.display")
        result['tabs'] = window.evaluate_js(
            "[...document.querySelectorAll('#todo-tabs .todo-tab')].map(b => b.textContent.trim())")
        result['items'] = window.evaluate_js(
            "document.querySelectorAll('#todo-list .todo-item').length")
        result['texts'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#todo-list .todo-text')].map(e => e.textContent))")
        # 勾第一条（"先做"）
        window.evaluate_js("document.querySelector('#todo-list .todo-check').click();")
        time.sleep(1.8)
        result['items_after'] = window.evaluate_js(
            "document.querySelectorAll('#todo-list .todo-item').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['panel'] == 'flex', '待办面板没打开'
    assert r['items'] == 2, r
    assert '先做' in r['texts'] and '后做' in r['texts']
    assert any('全部' in t for t in r['tabs']) and any('逾期' in t for t in r['tabs'])
    # 勾完"先做"后，今天页签里只剩"后做"
    assert r['items_after'] == 1, r
    stored = backend.api.notes_get(nid)['content']
    assert '- [x] 先做' in stored and '- [ ] 后做' in stored, stored


@pytest.mark.e2e
def test_todo_tabs_fit_in_one_row(tmp_path, monkeypatch):
    """待办面板顶部六个筛选必须**一行**放得下（用户反馈：第六个「已完成」掉到第二行）。

    为什么用测量而不是静态检查：换不换行取决于字体与徽章宽度，只有真浏览器量得准。
    实测（12px 字体 + 一位/两位计数）：六个胶囊要 ~373px，`.panel-dialog-wide` 的内容宽 412px。
    只把胶囊改窄是放不下的（360px 面板只有 312px 内容宽）—— 所以面板宽度也算进这条断言里。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    today = datetime.date.today().isoformat()
    lines = ['- [ ] 今天的事 @%s' % today, '- [ ] 逾期的事 @2020-01-01', '- [x] 已完成 @2020-01-01']
    lines += ['- [ ] 无期限 %d' % i for i in range(12)]      # 让「全部」「无日期」变成两位数计数
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '清单', 'content': '\n'.join(lines) + '\n'})

    def actions(window, result):
        window.evaluate_js("document.getElementById('btn-todos').click();")
        wait_for_js(window, "document.querySelectorAll('#todo-tabs .todo-tab').length", 6)
        time.sleep(0.6)
        result['m'] = json.loads(window.evaluate_js("""JSON.stringify((() => {
            const box = document.getElementById('todo-tabs');
            const chips = [...box.querySelectorAll('.todo-tab')];
            const widths = chips.map(c => c.getBoundingClientRect().width);
            const gap = parseFloat(getComputedStyle(box).gap) || 0;
            return {
                rows: new Set(chips.map(c => Math.round(c.getBoundingClientRect().top))).size,
                need: widths.reduce((a, b) => a + b, 0) + gap * (chips.length - 1),
                avail: box.getBoundingClientRect().width,
                labels: chips.map(c => c.textContent.trim()),
                dialogW: document.querySelector('#todo-panel .panel-dialog')
                                 .getBoundingClientRect().width,
            };
        })())"""))

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    m = r['m']
    assert len(m['labels']) == 6, m
    assert all(any(ch.isdigit() for ch in s) for s in m['labels']), \
        '这一条要验的是"带计数时也一行"，六个页签都该有计数：%s' % m['labels']
    assert m['dialogW'] == 460, '待办面板应当用 .panel-dialog-wide（460px）：%s' % m['dialogW']
    assert m['need'] <= m['avail'], \
        '六个胶囊要 %.1fpx，可用只有 %.1fpx → 会换行' % (m['need'], m['avail'])
    assert m['rows'] == 1, '页签换成了 %d 行：%s' % (m['rows'], m['labels'])


@pytest.mark.e2e
def test_command_panel_runs_command(tmp_path, monkeypatch):
    """Ctrl+P → 输入 `>` → 列出命令 → 回车执行（用"待办清单"命令验证效果）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '清单', '- [ ] 甲\n')

    def actions(window, result):
        time.sleep(1.2)
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'p', ctrlKey:true, bubbles:true, cancelable:true}));")
        time.sleep(0.6)
        result['placeholder'] = window.evaluate_js(
            "document.getElementById('quick-switch-input').placeholder")
        # 输入 `>` → 命令模式
        window.evaluate_js(
            "var i=document.getElementById('quick-switch-input'); i.value='>';"
            "i.dispatchEvent(new Event('input',{bubbles:true}));")
        time.sleep(0.4)
        result['commands'] = window.evaluate_js(
            "document.querySelectorAll('#quick-switch-list [data-cmd]').length")
        result['badges'] = window.evaluate_js(
            "document.querySelectorAll('#quick-switch-list .qs-kind').length")
        result['titles'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#quick-switch-list .qs-title')]"
            ".map(e => e.textContent))")
        # 过滤到"待办"并回车
        window.evaluate_js(
            "var i=document.getElementById('quick-switch-input'); i.value='>待办';"
            "i.dispatchEvent(new Event('input',{bubbles:true}));")
        time.sleep(0.4)
        result['filtered'] = window.evaluate_js(
            "document.querySelectorAll('#quick-switch-list [data-cmd]').length")
        window.evaluate_js(
            "document.getElementById('quick-switch-input')"
            ".dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));")
        time.sleep(1.2)
        result['qs_closed'] = window.evaluate_js(
            "document.getElementById('quick-switch-panel').style.display")
        result['todo_opened'] = window.evaluate_js(
            "document.getElementById('todo-panel').style.display")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert '>' in r['placeholder'], '占位提示应告诉用户 `>` 可以执行命令'
    assert r['commands'] >= 10, '命令数量偏少：%r' % r['commands']
    assert r['badges'] == r['commands'], '每条命令都该带「命令」标签'
    assert '待办清单（跨笔记）' in r['titles']
    assert r['filtered'] == 1, '过滤后应只剩一条：%r' % r['filtered']
    assert r['qs_closed'] == 'none', '执行后面板应关闭'
    assert r['todo_opened'] == 'flex', '命令应真的打开了待办面板'


@pytest.mark.e2e
def test_saved_search_save_and_apply(tmp_path, monkeypatch):
    """搜索 → 保存为视图 → 侧栏出现 → 点击套用（清空后能一键回到这次筛选）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '苹果笔记', '甲\n')
    _md_note(backend.api, '汽车笔记', '乙\n')

    def actions(window, result):
        time.sleep(1.2)
        # 先用标签语法筛选（不用关键词，避免 FTS 时序影响）
        window.evaluate_js(
            "var i=document.getElementById('search-input'); i.value='title:苹果';"
            "i.dispatchEvent(new Event('input',{bubbles:true}));")
        time.sleep(1.0)
        result['filtered'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        window.evaluate_js("document.getElementById('btn-save-search').click();")
        time.sleep(0.5)
        result['dialog'] = window.evaluate_js("document.getElementById('input-dialog').style.display")
        window.evaluate_js(
            "document.getElementById('input-dialog-input').value='只看苹果';"
            "document.getElementById('btn-input-ok').click();")
        time.sleep(1.0)
        result['ss_visible'] = window.evaluate_js(
            "!document.getElementById('saved-searches').classList.contains('hidden')")
        result['ss_names'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#ss-list .ss-name')].map(e => e.textContent))")
        # 清空搜索 → 全部笔记；再点视图 → 回到筛选结果
        window.evaluate_js(
            "var i=document.getElementById('search-input'); i.value='';"
            "i.dispatchEvent(new Event('input',{bubbles:true}));")
        time.sleep(1.0)
        result['all_notes'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        window.evaluate_js("document.querySelector('#ss-list .ss-item').click();")
        time.sleep(1.2)
        result['after_apply'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        result['search_value'] = window.evaluate_js(
            "document.getElementById('search-input').value")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['filtered'] == 1, 'title: 语法应筛出 1 篇，实际 %r' % r['filtered']
    assert r['dialog'] == 'flex', '保存视图应弹出输入框'
    assert r['ss_visible'] is True, '侧栏视图区应出现'
    assert '只看苹果' in r['ss_names']
    assert r['all_notes'] == 2
    assert r['after_apply'] == 1, '点击视图应重新套用查询'
    assert r['search_value'] == 'title:苹果'


@pytest.mark.e2e
def test_ctrl_f_respects_editor_focus(tmp_path, monkeypatch):
    """焦点在编辑器里时 Ctrl+F 不抢侧栏搜索（第 8 轮的笔记内查找要靠这条规则）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()          # 新建 = Markdown，启动会选中

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.focus();")
        time.sleep(0.3)
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'f', ctrlKey:true, bubbles:true, cancelable:true}));")
        time.sleep(0.3)
        result['in_editor'] = window.evaluate_js(
            "document.activeElement.id || document.activeElement.className")
        result['search_focused_1'] = window.evaluate_js(
            "document.activeElement === document.getElementById('search-input')")
        # 焦点移到正文外 → Ctrl+F 应聚焦侧栏搜索
        window.evaluate_js("document.activeElement.blur(); document.body.focus();")
        time.sleep(0.2)
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'f', ctrlKey:true, bubbles:true, cancelable:true}));")
        time.sleep(0.3)
        result['search_focused_2'] = window.evaluate_js(
            "document.activeElement === document.getElementById('search-input')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['search_focused_1'] is False, \
        '编辑器内按 Ctrl+F 不该抢侧栏搜索（当前焦点：%r）' % r['in_editor']
    assert r['search_focused_2'] is True, '正文外按 Ctrl+F 应聚焦侧栏搜索'


@pytest.mark.e2e
def test_tag_manager_shows_counts_and_actions(tmp_path, monkeypatch):
    """标签面板：显示层级缩进/计数，并带重命名与合并入口"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    parent = backend.api.tags_create('项目')['id']
    child = backend.api.tags_create('项目/子项目')['id']
    nid = _md_note(backend.api, '甲', '内容\n')
    backend.api.note_tags_set(nid, [parent, child])

    def actions(window, result):
        time.sleep(1.2)
        window.evaluate_js("document.getElementById('btn-tag-manager').click();")
        time.sleep(1.2)
        result['panel'] = window.evaluate_js(
            "document.getElementById('tag-manager-panel').style.display")
        result['items'] = window.evaluate_js(
            "document.querySelectorAll('#tag-manager-list .tag-manager-item').length")
        result['depths'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#tag-manager-list .tag-chip')]"
            ".map(e => e.className))")
        result['counts'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#tag-manager-list .tag-manager-count')]"
            ".map(e => e.textContent))")
        result['actions'] = window.evaluate_js(
            "document.querySelectorAll('#tag-manager-list [data-rename-tag]').length + '/' +"
            "document.querySelectorAll('#tag-manager-list [data-merge-tag]').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['panel'] == 'flex'
    assert r['items'] == 2, r
    depths = json.loads(r['depths'])
    counts = json.loads(r['counts'])
    assert any('tag-depth-1' in d for d in depths), '子标签应缩进展示：%r' % depths
    assert counts == ['1', '1'], counts
    assert r['actions'] == '2/2', '每个标签都该有重命名与合并入口：%r' % r['actions']
