# -*- coding: utf-8 -*-
"""第 9 轮「知识网络」的实机回归（无头 pywebview）。

这一轮的三个功能都**必须在真浏览器里验**，因为它们的关键契约是"文本 ↔ 界面 ↔ 后端"三方一致：
  · 属性面板写出的 front-matter，必须能被**后端解析器**读回同样的字典（跨语言往返）；
  · 预览里的 [[双链]] 要真的可点、未创建的标红、点了能建笔记并跳过去；
  · 表格视图的行数必须等于列表筛选后的行数（"列表看到的 = 表格看到的"）。

单元测试只能验后端那一半，界面这一半（CodeMirror 文本改写、事件委托、点击跳转）只有这里能验。
"""
import json
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note

RENDERER = PROJECT_ROOT + '/renderer'


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('知识网络回归', RENDERER + '/index.html',
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


def _md(api, title, content):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': content})
    return nid


def _set_input(window, selector, value):
    window.evaluate_js(
        "(function(){var el=document.querySelector(%s); if(!el) return;"
        "el.value=%s; el.dispatchEvent(new Event('input',{bubbles:true}));})()"
        % (json.dumps(selector), json.dumps(value)))


def _source(window):
    return window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.getValue()")


# ---------------- 属性面板 ----------------

@pytest.mark.e2e
def test_properties_panel_round_trips_through_backend(tmp_path, monkeypatch):
    """面板改一个值 → 写回正文 front-matter → 后端解析出同样的字典（跨语言往返）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md(backend.api, '属性笔记', '---\n状态: 进行中\n截止: 2026-10-01\n---\n\n正文\n')

    def actions(window, result):
        time.sleep(1.5)
        result['chips'] = window.evaluate_js(
            "document.getElementById('prop-chips').textContent")
        window.evaluate_js("document.getElementById('btn-prop-edit').click();")
        time.sleep(0.4)
        result['panel'] = window.evaluate_js(
            "!document.getElementById('prop-editor').classList.contains('hidden')")
        result['rows'] = window.evaluate_js("document.querySelectorAll('#prop-rows .prop-row').length")
        result['types'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#prop-rows .prop-type')].map(s => s.value))")
        # 改第一个值
        _set_input(window, '#prop-rows .prop-row .prop-val', '已完成')
        time.sleep(1.6)                      # 面板 350ms 防抖 + 保存 500ms 防抖
        result['source'] = _source(window)
        result['chips_after'] = window.evaluate_js(
            "document.getElementById('prop-chips').textContent")
        result['panel_still_open'] = window.evaluate_js(
            "!document.getElementById('prop-editor').classList.contains('hidden')")
        # 完成 → 面板收起，属性行还在
        window.evaluate_js("document.getElementById('btn-prop-done').click();")
        time.sleep(0.3)
        result['panel_closed'] = window.evaluate_js(
            "document.getElementById('prop-editor').classList.contains('hidden')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert '状态' in r['chips'] and '进行中' in r['chips'], r['chips']
    assert r['panel'] is True and r['rows'] == 2, r
    assert json.loads(r['types']) == ['text', 'date'], r['types']
    assert r['panel_still_open'] is True, '编辑过程中不该把面板重建掉（会丢焦点）'
    assert '已完成' in r['chips_after']
    assert r['panel_closed'] is True

    stored = backend.api.notes_get(nid)['content']
    assert stored.startswith('---\n'), stored
    props = backend.parse_front_matter(stored)
    assert props == {'状态': '已完成', '截止': '2026-10-01'}, props
    # 面板写入的内容也要能被后端的属性查询看见
    assert backend.api.notes_search('prop:状态=已完成')['ids'] == [nid]


@pytest.mark.e2e
def test_properties_add_and_delete_row(tmp_path, monkeypatch):
    """添加一行 → 正文多一个键；删掉那一行 → 正文里的键也消失（不留空壳 front-matter）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md(backend.api, '无属性', '正文\n')

    def actions(window, result):
        time.sleep(1.5)
        result['chips_before'] = window.evaluate_js(
            "document.getElementById('prop-chips').textContent")
        window.evaluate_js("document.getElementById('btn-prop-edit').click();")
        time.sleep(0.3)
        window.evaluate_js("document.getElementById('btn-prop-add').click();")
        time.sleep(0.2)
        _set_input(window, '#prop-rows .prop-row .prop-key', '来源')
        _set_input(window, '#prop-rows .prop-row .prop-val', '课堂')
        time.sleep(1.6)
        result['source'] = _source(window)
        result['chips'] = window.evaluate_js("document.getElementById('prop-chips').textContent")
        # 删掉这一行
        window.evaluate_js("document.querySelector('#prop-rows .prop-del').click();")
        time.sleep(1.6)
        result['source_after'] = _source(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['chips_before'] == '', '没有属性时不该显示任何 chip'
    assert '来源: 课堂' in r['source'], r['source']
    assert r['source'].startswith('---\n')
    assert '来源' in r['chips']
    assert backend.parse_front_matter(r['source']) == {'来源': '课堂'}
    assert '来源' not in r['source_after'], r['source_after']
    assert not r['source_after'].startswith('---'), '删光了属性就该把整段 front-matter 去掉'
    assert backend.api.notes_get(nid)['content'] == r['source_after']


@pytest.mark.e2e
def test_delta_note_offers_conversion(tmp_path, monkeypatch):
    """富文本笔记：属性行明确说明"没有属性"并给出转换入口（不是把功能藏起来）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    make_delta_note(backend, '富文本', json.dumps({'ops': [{'insert': '正文\n'}]}))

    def actions(window, result):
        time.sleep(1.5)
        result['note_format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['block_hidden'] = window.evaluate_js(
            "document.getElementById('prop-block').classList.contains('hidden')")
        result['chips'] = window.evaluate_js("document.getElementById('prop-chips').textContent")
        result['btn'] = window.evaluate_js("document.getElementById('btn-prop-edit').textContent")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['note_format'] == 'delta'
    assert r['block_hidden'] is False, '富文本也要显示属性行（否则用户不知道有这功能）'
    assert '富文本' in r['chips'], r['chips']
    assert r['btn'] == '转成 Markdown', r['btn']


# ---------------- 双链：预览、标红、点击 ----------------

@pytest.mark.e2e
def test_preview_renders_wikilinks_and_marks_missing(tmp_path, monkeypatch):
    """预览：front-matter 不显示；[[链接]] 变成可点元素；指向不存在的笔记要标红。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '目标笔记', '# 目标\n\n正文\n')
    _md(backend.api, '源笔记', (
        '---\n状态: 进行中\n---\n\n'
        '见 [[目标笔记|别名显示]] 与 [[不存在的笔记]]\n'))

    def actions(window, result):
        time.sleep(2.0)
        result['preview_text'] = window.evaluate_js(
            "document.getElementById('md-preview').textContent")
        result['links'] = window.evaluate_js(
            "document.querySelectorAll('#md-preview a.md-wikilink').length")
        result['labels'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#md-preview a.md-wikilink')]"
            ".map(a => a.textContent))")
        time.sleep(0.8)          # 等 hydrateWikilinks 问完后端
        result['missing'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#md-preview a.md-wikilink-missing')]"
            ".map(a => a.getAttribute('data-wikilink')))")
        result['saw_front_matter'] = '进行中' in (result['preview_text'] or '')

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['saw_front_matter'] is False, 'front-matter 不该出现在预览正文里'
    assert r['links'] == 2, r
    assert json.loads(r['labels']) == ['别名显示', '不存在的笔记'], r['labels']
    assert json.loads(r['missing']) == ['不存在的笔记'], r['missing']


@pytest.mark.e2e
def test_click_wikilink_opens_target_note(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    tgt = _md(backend.api, '目标笔记', '# 目标\n\n正文\n')
    _md(backend.api, '源笔记', '见 [[目标笔记]]\n')

    def actions(window, result):
        time.sleep(2.0)
        result['before'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['title_before'] = window.evaluate_js(
            "document.getElementById('note-title-input').value")
        window.evaluate_js("document.querySelector('#md-preview a.md-wikilink').click();")
        time.sleep(1.5)
        result['after'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['title_after'] = window.evaluate_js(
            "document.getElementById('note-title-input').value")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['title_before'] == '源笔记'
    assert r['after'] == tgt, '点双链应当切到目标笔记'
    assert r['title_after'] == '目标笔记'


@pytest.mark.e2e
def test_click_missing_wikilink_creates_note(tmp_path, monkeypatch):
    """点一个"尚未创建"的链接 = 直接建同名笔记并跳过去（用户选定的行为）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '源笔记', '见 [[还没写的笔记]]\n')
    before = len(backend.api.notes_list())

    def actions(window, result):
        time.sleep(2.0)
        window.evaluate_js("document.querySelector('#md-preview a.md-wikilink').click();")
        time.sleep(2.0)
        result['title'] = window.evaluate_js("document.getElementById('note-title-input').value")
        result['format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['active'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['list_count'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item').length")
        result['toast'] = window.evaluate_js(
            "(document.getElementById('save-toast')||{}).textContent || ''")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['title'] == '还没写的笔记', r['title']
    assert r['format'] == 'md'
    assert r['active'], '应当已经切到新笔记'
    assert '已新建' in r['toast'], r['toast']
    titles = [n['title'] for n in backend.api.notes_list()]
    assert '还没写的笔记' in titles and len(titles) == before + 1, titles


@pytest.mark.e2e
def test_heading_anchor_navigates_to_section(tmp_path, monkeypatch):
    """[[目标#小节]]：切到目标笔记**并且**把光标定位到那一节（预览那边也生成的同一个 id）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    tgt = _md(backend.api, '目标笔记', '# 一级\n\n正文\n\n## 小节二\n\n更深的内容\n')
    _md(backend.api, '源笔记', '见 [[目标笔记#小节二]]\n')

    def actions(window, result):
        time.sleep(2.0)
        window.evaluate_js("document.querySelector('#md-preview a.md-wikilink').click();")
        time.sleep(1.6)
        result['active'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['cursor_line'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getCursor().line")
        result['has_anchor'] = window.evaluate_js(
            "!!document.getElementById('md-h-' + 'x') || "
            "document.querySelectorAll('#md-preview h2[id]').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['active'] == tgt
    assert r['cursor_line'] == 4, '小节二在第 5 行（0-based 4），实际 %r' % r['cursor_line']
    assert r['has_anchor'] >= 1, '预览标题应当带 id 锚点'


@pytest.mark.e2e
def test_same_note_anchor_jumps_without_switching(tmp_path, monkeypatch):
    """`[[#小节]]`：本笔记内跳转，不切换笔记。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md(backend.api, '本笔记', '# 一级\n\n正文\n\n## 目标小节\n\n内容 [[#目标小节]]\n')

    def actions(window, result):
        time.sleep(2.0)
        result['before'] = window.evaluate_js("window.__app.state.activeNoteId")
        window.evaluate_js("document.querySelector('#md-preview a.md-wikilink').click();")
        time.sleep(1.0)
        result['after'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['cursor_line'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getCursor().line")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['after'] == r['before'] == nid
    assert r['cursor_line'] == 4, r['cursor_line']


# ---------------- 链接抽屉 ----------------

@pytest.mark.e2e
def test_links_drawer_lists_and_navigates(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    tgt = _md(backend.api, '目标笔记', '# 目标\n\n正文\n')
    src = _md(backend.api, '源笔记', '见 [[目标笔记|那边]] 与 [[没有的]]\n')

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("document.getElementById('btn-links').click();")
        time.sleep(1.2)
        result['visible'] = window.evaluate_js(
            "!document.getElementById('links-drawer').classList.contains('hidden')")
        result['sections'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#links-list .link-section-head')]"
            ".map(e => e.textContent))")
        result['items'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#links-list .link-title')]"
            ".map(e => e.textContent))")
        # 「尚未创建」是最后一个分组；注意同一个链接在"指向"里也会出现一次（两种视角）
        result['missing'] = window.evaluate_js(
            "document.querySelectorAll('#links-list .link-section:last-child .link-item').length")
        # 点"没有的" → 建笔记并跳过去
        window.evaluate_js(
            "document.querySelector('#links-list .link-section:last-child .link-item').click();")
        time.sleep(2.0)
        result['title'] = window.evaluate_js("document.getElementById('note-title-input').value")
        # 回到源笔记（点列表项，不依赖调试桥），再看反向链接
        window.evaluate_js(
            "document.querySelector('#note-list .note-item[data-note-id=\"%s\"]').click()" % tgt)
        time.sleep(1.5)
        window.evaluate_js("document.getElementById('btn-links').click();")
        time.sleep(1.2)
        result['back'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#links-list .link-title')]"
            ".map(e => e.textContent))")
        result['context'] = window.evaluate_js(
            "(document.querySelector('#links-list .link-context')||{}).textContent || ''")
        # 点反向链接 → 切回源笔记
        window.evaluate_js("document.querySelector('#links-list .link-item').click();")
        time.sleep(1.5)
        result['active'] = window.evaluate_js("window.__app.state.activeNoteId")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['visible'] is True
    joined = ' '.join(json.loads(r['sections']))
    assert '指向' in joined and '反向链接' in joined and '尚未创建' in joined, r['sections']
    items = json.loads(r['items'])
    assert '那边' in items, items
    assert r['missing'] == 1
    assert r['title'] == '没有的'
    assert '源笔记' in json.loads(r['back']), r['back']
    assert '目标笔记' in r['context'], r['context']
    assert r['active'] == src


@pytest.mark.e2e
def test_outline_and_links_are_mutually_exclusive(tmp_path, monkeypatch):
    """两个抽屉都在右侧：开一个必须关另一个，否则互相压住。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '笔记', '# 标题\n\n正文\n')

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("document.getElementById('btn-outline').click();")
        time.sleep(0.4)
        window.evaluate_js("document.getElementById('btn-links').click();")
        time.sleep(1.0)
        result['outline_hidden'] = window.evaluate_js(
            "document.getElementById('outline-drawer').classList.contains('hidden')")
        result['links_visible'] = window.evaluate_js(
            "!document.getElementById('links-drawer').classList.contains('hidden')")
        window.evaluate_js("document.getElementById('btn-outline').click();")
        time.sleep(0.4)
        result['links_hidden'] = window.evaluate_js(
            "document.getElementById('links-drawer').classList.contains('hidden')")
        result['outline_visible'] = window.evaluate_js(
            "!document.getElementById('outline-drawer').classList.contains('hidden')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['outline_hidden'] is True and r['links_visible'] is True, r
    assert r['links_hidden'] is True and r['outline_visible'] is True, r


# ---------------- 表格视图 ----------------

@pytest.mark.e2e
def test_table_view_rows_sort_and_open(tmp_path, monkeypatch):
    """表格 = 当前筛选后的笔记：行数一致、属性列在、点表头能排序、点行能打开笔记。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '甲笔记', '---\n状态: 进行中\n---\n\n正文\n')
    _md(backend.api, '乙笔记', '正文\n')
    _md(backend.api, '丙笔记', '正文\n')

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("document.getElementById('btn-table-view').click();")
        time.sleep(1.5)
        result['visible'] = window.evaluate_js(
            "!document.getElementById('table-view').classList.contains('hidden')")
        result['rows'] = window.evaluate_js("document.querySelectorAll('#table-view-body tr').length")
        result['headers'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#table-view-head th')]"
            ".map(e => e.textContent.trim()))")
        result['meta'] = window.evaluate_js("document.getElementById('table-view-meta').textContent")
        result['first_before'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr td')||{}).textContent")
        # 点「标题」表头排序
        window.evaluate_js(
            "[...document.querySelectorAll('#table-view-head th')]"
            ".find(th => th.textContent.indexOf('标题') === 0).click();")
        time.sleep(0.4)
        result['first_asc'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr td')||{}).textContent")
        window.evaluate_js(
            "[...document.querySelectorAll('#table-view-head th')]"
            ".find(th => th.textContent.indexOf('标题') === 0).click();")
        time.sleep(0.4)
        result['first_desc'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr td')||{}).textContent")
        # 点一行 → 关闭表格并打开那篇笔记（点的是当前排序下的第一行）
        result['first_row'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr td')||{}).textContent")
        result['first_id'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr')||{getAttribute:function(){return '';}})"
            ".getAttribute('data-note')")
        window.evaluate_js("document.querySelector('#table-view-body tr').click();")
        time.sleep(1.5)
        result['closed'] = window.evaluate_js(
            "document.getElementById('table-view').classList.contains('hidden')")
        result['active'] = window.evaluate_js("window.__app.state.activeNoteId")
        result['title'] = window.evaluate_js("document.getElementById('note-title-input').value")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['visible'] is True
    assert r['rows'] == 3, r
    headers = json.loads(r['headers'])
    assert headers[0].startswith('标题') and '字数' in ' '.join(headers), headers
    assert '状态' in headers, '属性列必须出现在表头里：%r' % headers
    assert '3 篇' in r['meta'], r['meta']
    # 标题按 zh 排序：丙(bing) < 甲(jia) < 乙(yi)
    assert r['first_asc'] == '丙笔记' and r['first_desc'] == '乙笔记', (r['first_asc'], r['first_desc'])
    assert r['closed'] is True, '点一行后表格应当关闭'
    assert r['title'] == r['first_row'], '打开的应该是被点的那一行：%r' % r
    assert r['active'] == r['first_id'], '打开的笔记 id 应当等于被点那一行的 data-note'


@pytest.mark.e2e
def test_table_view_follows_search_filter(tmp_path, monkeypatch):
    """表格显示的永远是列表里看到的那批（搜索语法/保存的视图直接复用）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '苹果笔记', '---\n状态: 进行中\n---\n\n甲\n')
    _md(backend.api, '汽车笔记', '乙\n')

    def actions(window, result):
        time.sleep(1.5)
        _set_input(window, '#search-input', 'prop:状态=进行中')
        time.sleep(1.2)
        result['list_rows'] = window.evaluate_js(
            "document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)').length")
        window.evaluate_js("document.getElementById('btn-table-view').click();")
        time.sleep(1.4)
        result['table_rows'] = window.evaluate_js(
            "document.querySelectorAll('#table-view-body tr').length")
        result['meta'] = window.evaluate_js("document.getElementById('table-view-meta').textContent")
        result['title'] = window.evaluate_js(
            "(document.querySelector('#table-view-body tr td')||{}).textContent")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['list_rows'] == 1, r
    assert r['table_rows'] == 1, '表格行数必须跟着筛选：%r' % r
    assert '苹果笔记' in r['title']
    assert '当前筛选' in r['meta'], r['meta']


# ---------------- 消毒：id 不能被用户内容劫持 ----------------

@pytest.mark.e2e
def test_injected_id_cannot_hijack_app_dom(tmp_path, monkeypatch):
    """内嵌 HTML 里的 `id` 必须被剥掉：`<div id="editor-status">` 能直接顶掉状态栏。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md(backend.api, '劫持尝试', (
        '<div id="editor-status">假的</div>\n'
        '<div id="find-bar">假的</div>\n'
        '# 正常标题\n\n正文\n'))

    def actions(window, result):
        time.sleep(1.8)
        result['dup'] = window.evaluate_js(
            "document.querySelectorAll('[id=editor-status]').length + '/' +"
            "document.querySelectorAll('[id=find-bar]').length")
        result['injected_has_id'] = window.evaluate_js(
            "!!document.querySelector('#md-preview [id]')")
        result['status_ok'] = window.evaluate_js(
            "document.getElementById('status-text').textContent.length > 0")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['dup'] == '1/1', '注入的 id 让应用节点出现了重复：%r' % r['dup']
    assert r['injected_has_id'] is False, '预览里的用户内容不该带 id'
    assert r['status_ok'] is True, '状态栏应当照常工作'
