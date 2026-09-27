# -*- coding: utf-8 -*-
"""笔记本范围的实机回归（第 12 轮）：真浏览器里跑"在某个笔记本里新建/搜索/跳转"。

用户报的现象只能在这一层验：单测能证明 `notes_list(notebook_id)` 过滤对了，
但"点了新建之后列表变成什么"是 DOM、事件与刷新路径共同决定的 —— 而这一轮修的核心
恰恰是"**任何一条刷新路径都不许把范围冲掉**"。所以这里逐条走真实交互：

  · 切到「原神」→ 列表只剩原神 → 点「＋ 新建笔记」→ 新笔记落在原神、列表仍是原神
    （顺带点一下置顶按钮，那是**另一条** loadNotes 路径）；
  · 在原神里搜一个只存在于别的笔记本的词 → 给"当前笔记本里没有匹配"的提示，
    点一下能切到全部笔记找到它；
  · Ctrl+P 跳一篇别的笔记本的笔记 → 视角跟着切过去（否则列表里看不见正在编辑的它）。

配套单测见 tests/test_notebook_scope.py（后端范围 + 前端接线静态检查）。
"""
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_for_js

pytestmark = pytest.mark.e2e

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('笔记本范围回归', RENDERER + '/index.html',
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


def _mk(api, title, notebook_id=None):
    nid = api.notes_create(notebook_id)['id']
    api.notes_update(nid, {'title': title})
    return nid


def test_app_boots_cleanly(tmp_path, monkeypatch):
    """启动不许"半死"：列表渲染出来、笔记本栏有计数、`window.__bootError` 为空。

    第 12 轮实测踩到的坑：给几个叶子模块加 `import … from './09-boot.js'` 之后，
    09-boot 的函数体在**别的模块函数体之前**被求值，`initApp()` 走到
    `initMarkdownEditor()` 抛 `ReferenceError: Cannot access 'cm' before initialization`
    （TDZ），界面只剩骨架：没有列表、笔记本栏空白、标签栏没渲染 —— 而模块图本身"加载成功"
    （`__appBoot.done === true`，没有红条），单测与静态检查全绿。
    修法是启动排到下一个宏任务（模块求值同步完成后再 init），这条测试就是它的哨兵。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    nb = api.notebooks_create('原神')['id']
    _mk(api, 'A', nb)
    _mk(api, 'B')
    _mk(api, 'C')

    def actions(window, result):
        result['boot_error'] = window.evaluate_js("window.__bootError || null")
        result['items'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item').length")
        result['skeleton'] = window.evaluate_js("!!document.querySelector('.note-skeleton')")
        result['badge'] = window.evaluate_js(
            "document.getElementById('notebook-count').textContent")
        result['dropdown_items'] = window.evaluate_js(
            "document.querySelectorAll('#notebook-dropdown .notebook-drop-item').length")
        result['boot_done'] = window.evaluate_js("window.__appBoot && window.__appBoot.done")

    r = _run(ns, actions)
    assert 'error' not in r, r
    assert r['boot_error'] is None, '启动过程抛错了：%s' % r['boot_error']
    assert r['boot_done'] is True
    assert r['skeleton'] is False, '列表还停在骨架占位 → initApp 没走完'
    assert r['items'] == 3, r
    assert r['badge'] == '(3)', r['badge']
    assert r['dropdown_items'] == 2, '下拉里应有「原神」+「全部笔记」两项'


def _click_notebook(window, name):
    """点笔记本下拉里的某一项（用户真实路径，不是直接调 setNotebookScope）"""
    window.evaluate_js(
        "(() => { const it = [...document.querySelectorAll('#notebook-dropdown .notebook-drop-item')]"
        ".find(e => e.textContent.indexOf('%s') === 0 || e.textContent.indexOf('%s') >= 0);"
        " if (it) it.click(); })();" % (name, name))


def _titles(window):
    return window.evaluate_js(
        "JSON.stringify([...document.querySelectorAll('#note-list .note-item-title')]"
        ".map(e => e.textContent))")


def test_create_inside_notebook_does_not_mix(tmp_path, monkeypatch):
    """★ 用户报的那一幕（三张截图）：在**空的**「原神」(0) 里点新建 → 新笔记进原神，
    别的笔记本一篇都不许混进来。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    nb = api.notebooks_create('原神')['id']        # 刻意留空：截图里就是「原神 (0)」
    _mk(api, 'MATS1192')
    _mk(api, 'Genshin')

    def actions(window, result):
        _click_notebook(window, '原神')
        time.sleep(2)
        result['scoped_titles'] = _titles(window)
        result['scoped_badge'] = window.evaluate_js(
            "document.getElementById('notebook-count').textContent")
        result['scope'] = window.evaluate_js("__app.getCurrentNotebookId()")
        result['editor_hidden'] = window.evaluate_js(
            "document.body.classList.contains('no-active-note')")

        # 新建（就是截图里那颗「＋ 新建笔记」）
        window.evaluate_js("document.getElementById('btn-new-note').click()")
        wait_for_js(window, "document.querySelectorAll('#note-list .note-item').length", 1)
        result['after_create_titles'] = _titles(window)
        result['after_create_badge'] = window.evaluate_js(
            "document.getElementById('notebook-count').textContent")
        result['active_title'] = window.evaluate_js(
            "document.getElementById('note-title-input').value")
        result['editor_visible'] = window.evaluate_js(
            "!document.body.classList.contains('no-active-note')")

        # 另一条刷新路径（置顶按钮 → togglePinNote → loadNotes）：范围同样不许被冲掉
        window.evaluate_js(
            "document.querySelector('#note-list .note-item [data-pin-id]').click()")
        time.sleep(2)
        result['after_pin_titles'] = _titles(window)
        result['after_pin_badge'] = window.evaluate_js(
            "document.getElementById('notebook-count').textContent")

    r = _run(ns, actions)
    assert 'error' not in r, r

    # 切到（空的）原神：一篇都没有、徽章 (0)、编辑区收起来
    assert r['scope'] == nb
    assert r['scoped_badge'] == '(0)'
    assert r['scoped_titles'] == '[]', r['scoped_titles']
    assert r['editor_hidden'] is True, '空笔记本应把编辑区收起来（点上方按钮新建）'

    # 新建之后：原神里**只有那一篇新的**，MATS1192 / Genshin 不许出现（这就是"混在一起"）
    titles = r['after_create_titles']
    assert 'MATS1192' not in titles and 'Genshin' not in titles, \
        '新建之后列表变回全量了（用户报的"混在一起"）：%s' % titles
    assert titles == '["未命名笔记"]', titles
    assert r['after_create_badge'] == '(1)', r['after_create_badge']
    assert r['active_title'] == '未命名笔记', r['active_title']
    assert r['editor_visible'] is True, '新建后编辑区必须露出来（否则用户看不到刚建的笔记）'

    # 置顶（另一条 loadNotes 路径）之后仍然是原神视角
    assert r['after_pin_titles'] == titles, r['after_pin_titles']
    assert r['after_pin_badge'] == '(1)'

    # 库里：新笔记确实属于原神，未分类那两篇没被动过
    notes = api.notes_list(nb)
    assert len(notes) == 1, [n['title'] for n in notes]
    assert notes[0]['title'] == '未命名笔记', '新建的笔记必须落在原神里，而不是未分类'
    assert len(api.notes_list('')) == 2, '未分类里的两篇应原样保留'
    assert api.notebook_counts()['total'] == 3


def test_search_inside_notebook_reports_other_notebooks(tmp_path, monkeypatch):
    """在笔记本里搜索 = 只在这一本里搜；别的笔记本有命中时把话说清楚并能一键切过去"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    nb = api.notebooks_create('原神')['id']
    _mk(api, '原神角色笔记', nb)          # 标题刻意不含 MATS：本笔记本里应当一篇都不命中
    _mk(api, 'MATS1192 复习')
    _mk(api, 'Genshin')

    def actions(window, result):
        _click_notebook(window, '原神')
        wait_for_js(window, "document.querySelectorAll('#note-list .note-item').length", 1)
        window.evaluate_js(
            "const i = document.getElementById('search-input'); i.value = 'MATS';"
            "i.dispatchEvent(new Event('input', { bubbles: true }));")
        wait_for_js(window, "!!document.getElementById('search-scope-hint')")
        result['hint'] = window.evaluate_js(
            "(document.getElementById('search-scope-hint') || {}).textContent")
        result['visible'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        # 一键切到全部笔记再找
        window.evaluate_js("document.querySelector('.search-scope-hint-btn').click()")
        wait_for_js(window, "__app.getCurrentNotebookId()", None)
        time.sleep(1.5)
        result['after_scope'] = window.evaluate_js("String(__app.getCurrentNotebookId())")
        result['after_visible'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        result['hint_gone'] = window.evaluate_js(
            "!document.getElementById('search-scope-hint')")

    r = _run(ns, actions)
    assert 'error' not in r, r
    assert r['hint'] and '其它笔记本里有 1 篇' in r['hint'], r['hint']
    assert r['visible'] == 0, '当前笔记本里不该出现别的笔记本的命中'
    assert r['after_scope'] == 'null', '点了提示应切到全部笔记'
    assert r['after_visible'] == 1, '切到全部笔记后 MATS1192 应出现'
    assert r['hint_gone'] is True, '全部笔记视角下不该再提示"其它笔记本"'


def test_quick_switch_follows_the_note_across_notebooks(tmp_path, monkeypatch):
    """Ctrl+P 跳一篇别的笔记本的笔记 → 视角跟着切过去（列表里必须看得见正在编辑的这篇）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    nb = api.notebooks_create('原神')['id']
    _mk(api, '原神角色笔记', nb)
    _mk(api, 'MATS1192 复习')
    _mk(api, 'Genshin')

    def actions(window, result):
        _click_notebook(window, '原神')
        wait_for_js(window, "document.querySelectorAll('#note-list .note-item').length", 1)
        # Ctrl+P → 输入 → 回车
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key: 'p', ctrlKey: true, bubbles: true, cancelable: true}));")
        wait_for_js(window, "document.getElementById('quick-switch-panel').style.display", 'flex')
        window.evaluate_js(
            "const i = document.getElementById('quick-switch-input'); i.value = 'MATS';"
            "i.dispatchEvent(new Event('input', { bubbles: true }));")
        time.sleep(0.8)
        result['candidates'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#quick-switch-list .quick-switch-item')]"
            ".map(e => e.textContent.trim()))")
        window.evaluate_js(
            "document.getElementById('quick-switch-input')"
            ".dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));")
        wait_for_js(window, "String(__app.getCurrentNotebookId())", 'null')
        time.sleep(1.5)
        result['scope'] = window.evaluate_js("String(__app.getCurrentNotebookId())")
        result['active_title'] = window.evaluate_js(
            "document.getElementById('note-title-input').value")
        result['list_count'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item').length")
        result['active_in_list'] = window.evaluate_js(
            "!!document.querySelector('#note-list .note-item.active')")

    r = _run(ns, actions)
    assert 'error' not in r, r
    assert 'MATS' in r['candidates'], r['candidates']
    assert r['scope'] == 'null', '未分类的笔记 → 视角应回「全部笔记」'
    assert r['active_title'] == 'MATS1192 复习', r['active_title']
    assert r['list_count'] == 3, '全部笔记视角下三篇都在'
    assert r['active_in_list'] is True, '正在编辑的这篇必须出现在列表里（否则列表与编辑区不一致）'


def test_tag_filter_stays_inside_the_notebook(tmp_path, monkeypatch):
    """标签与笔记本叠加：在「原神」里点一个标签，别的笔记本的同标签笔记不许倒进来"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    api = backend.api
    nb = api.notebooks_create('原神')['id']
    mine = _mk(api, '原神里的', nb)
    _mk(api, '原神里没标签的', nb)
    other = _mk(api, '别的本里的')
    tag = api.tags_create('重要')['id']
    api.note_tags_set(mine, [tag])
    api.note_tags_set(other, [tag])

    def actions(window, result):
        _click_notebook(window, '原神')
        wait_for_js(window, "document.querySelectorAll('#note-list .note-item').length", 2)
        window.evaluate_js("document.querySelector('.tag-filter-chip').click()")
        time.sleep(2)
        result['titles'] = _titles(window)
        result['badge'] = window.evaluate_js(
            "document.getElementById('notebook-count').textContent")
        # 清掉标签筛选 → 回到原神的两篇
        window.evaluate_js("document.getElementById('btn-clear-tag-filter').click()")
        time.sleep(2)
        result['after_clear'] = _titles(window)

    r = _run(ns, actions)
    assert 'error' not in r, r
    assert r['titles'] == '["原神里的"]', \
        '标签筛选把别的笔记本的同标签笔记也带进来了：%s' % r['titles']
    assert r['badge'] == '(1)'
    assert set(__import__('json').loads(r['after_clear'])) == {'原神里的', '原神里没标签的'}
