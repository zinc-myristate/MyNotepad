# -*- coding: utf-8 -*-
"""第十轮「捕获与模板」的实机回归（主界面这一侧，真 WebView2）。

覆盖三件只有真浏览器能验的事：
  · 模板抽屉的**自动保存**（输入 → 防抖 → 落库）与**预览走的是后端渲染器**；
  · 捕获菜单四个入口真的接上了后端（剪贴板 / 今日日记 / 截图 / 模板新建）；
  · 截图那条跨窗口链路回到主窗之后落的动作（插入当前笔记 / 存成新笔记后刷新列表）——
    覆盖窗本身在 tests/test_capture_windows_e2e.py 里单独测。

截图入口只验"点了会去调桥接并带上当前笔记 id"：真抓屏要动整个屏幕，留给打包后的手工探针。
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
    window = webview.create_window('捕获与模板回归', RENDERER + '/index.html',
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


def _click(window, selector):
    window.evaluate_js(
        "(function(){var el=document.querySelector(%s); if(el) el.click();})()"
        % json.dumps(selector))


def _visible(window, selector):
    return window.evaluate_js(
        "(function(){var el=document.querySelector(%s);"
        "return !!el && !el.classList.contains('hidden');})()" % json.dumps(selector))


def _source(window):
    return window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.getValue()")


def _type(window, selector, value):
    window.evaluate_js(
        "(function(){var el=document.querySelector(%s); if(!el) return;"
        "el.value=%s; el.dispatchEvent(new Event('input',{bubbles:true}));})()"
        % (json.dumps(selector), json.dumps(value)))


# ---------------- 模板抽屉 ----------------

@pytest.mark.e2e
def test_templates_drawer_preview_autosave_and_actions(tmp_path, monkeypatch):
    """抽屉：列表 / 预览（后端渲染）/ 自动保存 / 用模板新建 / 插入当前笔记。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    tpl = backend.api.template_create('会议记录', '# {{title}}\n\n日期：{{date}}\n')
    note_id = backend.api.notes_create()['id']
    backend.api.notes_update(note_id, {'title': '现有笔记', 'content': '原有正文\n'})

    def actions(window, result):
        time.sleep(2.0)
        result['menu_hidden_at_start'] = not _visible(window, '#capture-menu')
        _click(window, '#btn-capture')
        time.sleep(0.6)
        result['menu_open'] = _visible(window, '#capture-menu')
        result['menu_tpl_items'] = window.evaluate_js(
            "document.querySelectorAll('#capture-tpl-list [data-cap-tpl]').length")
        _click(window, '#cap-tpl-manage')
        time.sleep(0.8)
        result['drawer_open'] = _visible(window, '#templates-drawer')
        result['menu_closed_after'] = not _visible(window, '#capture-menu')
        result['chips'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#tpl-list .tpl-chip')].map(c => c.textContent))")
        _click(window, '#tpl-list .tpl-chip')
        time.sleep(0.8)
        result['name'] = window.evaluate_js("document.getElementById('tpl-name').value")
        result['content'] = window.evaluate_js("document.getElementById('tpl-content').value")
        # 预览用的标题 → {{title}} 跟着变，且日期由**后端**渲染出来
        _type(window, '#tpl-title', '周一例会')
        time.sleep(0.8)
        result['preview'] = window.evaluate_js("document.getElementById('tpl-preview').textContent")
        # 改内容 → 自动保存
        _type(window, '#tpl-content', '# {{title}}\n\n改过的模板 {{weekday}}\n')
        time.sleep(1.6)
        result['saved'] = window.evaluate_js("document.getElementById('tpl-saved').textContent")
        # 用模板新建
        _click(window, '#tpl-create-note')
        time.sleep(2.0)
        notes = backend.api.notes_list()
        result['titles'] = [n['title'] for n in notes]
        # 列表接口**不下发正文**（有意为之），要看正文得单独取
        hit = [n for n in notes if n['title'] == '周一例会']
        result['tpl_body'] = backend.api.notes_get(hit[0]['id'])['content'] if hit else ''
        result['active_after'] = window.evaluate_js(
            "window.__app.state.activeNoteId || ''")
        # 插入当前笔记（新建出来的那篇也是 Markdown）
        _click(window, '#tpl-insert')
        time.sleep(1.2)
        result['source'] = _source(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['menu_hidden_at_start'] is True
    assert r['menu_open'] is True
    assert r['menu_tpl_items'] == 1, '捕获菜单里应当直接列出模板'
    assert r['drawer_open'] is True and r['menu_closed_after'] is True
    assert json.loads(r['chips']) == ['会议记录']
    assert r['name'] == '会议记录'
    assert '日期：' in r['content']
    assert '# 周一例会' in r['preview'], '预览必须把 {{title}} 换成输入框里的标题'
    import datetime
    assert datetime.datetime.now().strftime('%Y-%m-%d') in r['preview'], '日期由后端渲染'
    assert '{{' not in r['preview'], '认识的变量都该被替换掉'
    assert r['saved'] == '已保存'
    assert backend.api.template_get(tpl['id'])['content'] == '# {{title}}\n\n改过的模板 {{weekday}}\n'
    assert '周一例会' in r['titles'], '用模板新建时标题取输入框的值'
    assert '# 周一例会' in r['tpl_body'] and '{{' not in r['tpl_body']
    # 插入当前笔记：渲染后的模板落进正文
    assert '改过的模板' in r['source']
    assert '{{' not in r['source']


@pytest.mark.e2e
def test_templates_drawer_mutually_exclusive_with_other_drawers(tmp_path, monkeypatch):
    """三个右侧抽屉互斥：开了链接抽屉再开模板，链接必须自己关掉。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.template_create('日记', '正文')
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '笔记', 'content': '# 标题\n正文\n'})

    def actions(window, result):
        time.sleep(2.0)
        _click(window, '#btn-links')
        time.sleep(0.6)
        result['links_open'] = _visible(window, '#links-drawer')
        _click(window, '#btn-capture')
        time.sleep(0.5)
        _click(window, '#cap-tpl-manage')
        time.sleep(0.8)
        result['tpl_open'] = _visible(window, '#templates-drawer')
        result['links_closed'] = not _visible(window, '#links-drawer')
        _click(window, '#templates-close')
        time.sleep(0.4)
        result['tpl_closed'] = not _visible(window, '#templates-drawer')

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['links_open'] is True
    assert r['tpl_open'] is True and r['links_closed'] is True
    assert r['tpl_closed'] is True


# ---------------- 捕获菜单 ----------------

@pytest.mark.e2e
def test_capture_menu_daily_note_and_idempotence(tmp_path, monkeypatch):
    """「今日日记」：建在「日记」笔记本里；再点一次打开同一篇（不会越点越多）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.template_create('日记', '# {{date}}\n\n- [ ] 今天要做的\n')

    def actions(window, result):
        time.sleep(2.0)
        _click(window, '#btn-capture')
        time.sleep(0.5)
        _click(window, '#cap-daily')
        time.sleep(2.0)
        notes = backend.api.notes_list()
        result['first'] = [(n['id'], n['title']) for n in notes]
        result['first_body'] = backend.api.notes_get(notes[0]['id'])['content']
        result['notebooks'] = [nb['name'] for nb in backend.api.notebooks_list()]
        # 再点一次：还是同一篇
        _click(window, '#btn-capture')
        time.sleep(0.5)
        _click(window, '#cap-daily')
        time.sleep(1.8)
        result['second'] = [(n['id'], n['title']) for n in backend.api.notes_list()]
        result['active'] = window.evaluate_js("window.__app.state.activeNoteId || ''")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert len(r['first']) == 1, '第一次点应当建一篇'
    assert len(r['second']) == 1, '第二次点不该再建一篇'
    assert r['first'][0][0] == r['second'][0][0]
    assert '日记' in r['notebooks']
    assert '今天要做的' in r['first_body'], '日记模板要套上'
    assert r['active'] == r['first'][0][0], '建完要跳过去'


@pytest.mark.e2e
def test_capture_menu_screenshot_passes_active_note(tmp_path, monkeypatch):
    """「截图选区」把**当前笔记 id** 交给 Python（插入时要往它的附件目录里放图）。

    真抓屏要动整个屏幕，自动化里换成记录入参的假实现。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '要插图的笔记', 'content': '正文\n'})
    calls = []
    ns['api'].capture_begin = lambda note_id=None: (calls.append(note_id), True)[1]

    def actions(window, result):
        time.sleep(2.0)
        _click(window, '#btn-capture')
        time.sleep(0.5)
        _click(window, '#cap-screenshot')
        time.sleep(0.8)
        result['calls'] = list(calls)
        result['menu_closed'] = not _visible(window, '#capture-menu')

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['calls'] == [nid], '必须把当前笔记 id 带过去，否则"插入当前笔记"会插错地方'
    assert r['menu_closed'] is True


@pytest.mark.e2e
def test_capture_bridge_inserts_image_into_current_note(tmp_path, monkeypatch):
    """跨窗口回来之后的插入：Markdown 写图片语法、富文本走 Quill embed（各一份附件）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import os

    from PIL import Image

    import backend

    src = os.path.join(str(tmp_path), 'shot.png')
    Image.new('RGB', (40, 30), (200, 100, 50)).save(src)

    md_id = backend.api.notes_create()['id']
    backend.api.notes_update(md_id, {'title': 'Markdown 笔记', 'content': '正文\n'})
    delta_id = make_delta_note(backend, '富文本笔记', '')

    def insert_for(window, note_id):
        saved = backend.api.file_copy_to_note(src, note_id, 'image')
        rel = 'attachments/%s/%s' % (note_id, saved['filename'])
        window.evaluate_js(
            "window.__capture && window.__capture.insertImage(%s, %s, %s)"
            % (json.dumps(rel), json.dumps(saved), json.dumps(note_id)))
        time.sleep(1.6)                       # 保存防抖 500ms + 余量
        return rel

    def actions(window, result):
        time.sleep(2.0)
        result['bridge'] = window.evaluate_js("typeof (window.__capture || {}).insertImage")
        # Markdown 笔记
        window.evaluate_js(
            "(function(){var el=[...document.querySelectorAll('.note-item')]"
            ".find(n => n.textContent.indexOf('Markdown 笔记') >= 0); if(el) el.click();})()")
        time.sleep(1.8)
        rel_md = insert_for(window, md_id)
        result['md_source'] = _source(window)
        result['md_rel'] = rel_md
        # 富文本笔记
        window.evaluate_js(
            "(function(){var el=[...document.querySelectorAll('.note-item')]"
            ".find(n => n.textContent.indexOf('富文本笔记') >= 0); if(el) el.click();})()")
        time.sleep(2.2)
        rel_delta = insert_for(window, delta_id)
        result['delta_content'] = window.evaluate_js(
            "JSON.stringify(window.__app.state.quill.getContents())")
        result['delta_rel'] = rel_delta
        result['delta_attachments'] = len(backend.api.attachments_list(delta_id))

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['bridge'] == 'function'
    assert r['md_rel'] in r['md_source'], 'Markdown 笔记里要写相对路径的图片语法'
    assert r['md_source'].startswith('![截图]')
    assert '"image"' in r['delta_content'], '富文本笔记要插 image embed'
    assert r['delta_rel'].split('/')[-1] in r['delta_content'], 'embed 里要带附件文件名'
    assert r['delta_attachments'] == 1, '跨窗口插入不能重复复制附件'


@pytest.mark.e2e
def test_calendar_today_opens_daily_note(tmp_path, monkeypatch):
    """日历里点「今天」= 每日笔记（幂等 + 落「日记」本），点别的日期保持原样。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()

    def actions(window, result):
        time.sleep(2.0)
        _click(window, '#btn-calendar')
        time.sleep(0.8)
        result['today_cell'] = window.evaluate_js(
            "document.querySelectorAll('#cal-grid .cal-day.today').length")
        window.evaluate_js(
            "document.querySelector('#cal-grid .cal-day.today').click();")
        time.sleep(2.2)
        notes = backend.api.notes_list()
        result['titles'] = [n['title'] for n in notes]
        result['notebooks'] = [nb['name'] for nb in backend.api.notebooks_list()]
        # 再点一次（日历可能已经关了，重新打开）
        _click(window, '#btn-calendar')
        time.sleep(0.6)
        window.evaluate_js(
            "document.querySelector('#cal-grid .cal-day.today').click();")
        time.sleep(1.8)
        result['count'] = len(backend.api.notes_list())

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['today_cell'] == 1
    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    assert any(t.startswith(today) for t in r['titles']), '点今天要建/打开每日笔记'
    assert '日记' in r['notebooks']
    assert r['count'] == 2, '原本那篇 + 今天的日记，不能越点越多'


@pytest.mark.e2e
def test_capture_hotkey_switch_persists(tmp_path, monkeypatch):
    """提醒面板第 4 个开关：勾上会落库（e2e 进程里没有热键控制器，返回即请求值）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend

    def actions(window, result):
        time.sleep(2.0)
        result['default_off'] = backend.api.settings_get('capture_hotkey')
        _click(window, '#btn-reminder-list')
        time.sleep(1.0)
        result['checked'] = window.evaluate_js(
            "document.getElementById('chk-capture-hotkey').checked")
        result['row_exists'] = window.evaluate_js(
            "!!document.getElementById('row-capture-hotkey')")
        result['hint'] = window.evaluate_js(
            "document.getElementById('capture-hotkey-hint').textContent")
        window.evaluate_js(
            "(function(){var c=document.getElementById('chk-capture-hotkey');"
            "c.checked=true; c.dispatchEvent(new Event('change',{bubbles:true}));})()")
        time.sleep(1.2)
        result['stored'] = backend.api.settings_get('capture_hotkey')

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['default_off'] in (None, '0'), '默认必须是关'
    assert r['checked'] is False and r['row_exists'] is True
    assert 'Ctrl+Alt+S' in r['hint']
    assert r['stored'] == '1'
