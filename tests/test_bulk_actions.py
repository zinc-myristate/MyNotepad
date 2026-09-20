# -*- coding: utf-8 -*-
"""批量操作（多选）：后端批量接口 + 前端多选交互。

后端断言落在"批量与逐个调用的效果一致"与"过滤条件不被绕过"上；
交互断言落在"Ctrl/Shift 真的能选中、选中后条子出现、动作真的作用到这些笔记"上。
"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial


def delta(text):
    return json.dumps({"ops": [{"insert": text + "\n"}]}, ensure_ascii=False)


def _mk(api, title, notebook_id=None, content=None):
    nid = api.notes_create()['id']
    fields = {'title': title, 'content': content or delta(title)}
    if notebook_id:
        fields['notebook_id'] = notebook_id
    api.notes_update(nid, fields)
    return nid


class TestBatchBackend:
    def test_delete_many_goes_to_trash(self, api):
        a, b, c = (_mk(api, t) for t in ('甲', '乙', '丙'))
        assert api.notes_delete_many([a, b]) == 2
        live = {n['id'] for n in api.notes_list()}
        assert live == {c}, '只应删掉选中的两篇'
        trash = {t['id'] for t in api.notes_trash_list()}
        assert {a, b} <= trash, '应进回收站而不是彻底删除'

    def test_delete_many_matches_single_delete_side_effects(self, api):
        """批量与逐个调用的副作用必须一致：搜索索引与回收站都要跟上"""
        a = _mk(api, '甲', content=delta('独一无二的检索词'))
        assert a in set(api.notes_search('独一无二的检索词')['ids'])
        api.notes_delete_many([a])
        assert a not in set(api.notes_search('独一无二的检索词')['ids']), \
            '批量删除也要把搜索索引清掉（否则搜出来点不开）'
        assert a in {t['id'] for t in api.notes_trash_list()}

    def test_delete_many_ignores_bad_ids(self, api):
        a = _mk(api, '甲')
        assert api.notes_delete_many([a, '不存在', None, '']) == 1
        assert api.notes_delete_many([]) == 0
        assert api.notes_delete_many(None) == 0

    def test_delete_many_skips_already_trashed(self, api):
        a, b = _mk(api, '甲'), _mk(api, '乙')
        api.notes_delete(a)
        assert api.notes_delete_many([a, b]) == 1, '已在回收站的不该重复计数'

    def test_move_many(self, api):
        nb1 = api.notebooks_create('课程A')['id']
        nb2 = api.notebooks_create('课程B')['id']
        a, b = _mk(api, '甲', nb1), _mk(api, '乙', nb1)
        c = _mk(api, '丙', nb2)
        assert api.notes_move_many([a, b], nb2) == 2
        got = {n['id']: n['notebook_id'] for n in api.notes_list()}
        assert got[a] == nb2 and got[b] == nb2 and got[c] == nb2

    def test_move_many_to_uncategorized(self, api):
        nb = api.notebooks_create('课程A')['id']
        a = _mk(api, '甲', nb)
        assert api.notes_move_many([a], None) == 1
        assert [n for n in api.notes_list() if n['id'] == a][0]['notebook_id'] is None

    def test_move_many_skips_trashed(self, api):
        nb = api.notebooks_create('课程A')['id']
        a = _mk(api, '甲')
        api.notes_delete(a)
        assert api.notes_move_many([a], nb) == 0
        assert api.notes_move_many([], nb) == 0

    def test_add_tag_many(self, api):
        tid = api.tags_create('数学')['id']
        a, b = _mk(api, '甲'), _mk(api, '乙')
        assert api.notes_add_tag_many([a, b], tid) == 2
        assert [t['id'] for t in api.note_tags_get(a)] == [tid]
        assert [t['id'] for t in api.note_tags_get(b)] == [tid]

    def test_add_tag_many_is_idempotent(self, api):
        tid = api.tags_create('数学')['id']
        a = _mk(api, '甲')
        assert api.notes_add_tag_many([a], tid) == 1
        assert api.notes_add_tag_many([a], tid) == 0, '重复打同一个标签不该再加一条关联'
        assert len(api.note_tags_get(a)) == 1

    def test_add_tag_many_skips_trashed_and_bad_input(self, api):
        tid = api.tags_create('数学')['id']
        a = _mk(api, '甲')
        api.notes_delete(a)
        assert api.notes_add_tag_many([a], tid) == 0
        assert api.notes_add_tag_many([], tid) == 0
        assert api.notes_add_tag_many([_mk(api, '乙')], '') == 0
        assert api.notes_add_tag_many([_mk(api, '丙')], '不存在的标签') == 0

    def test_tagged_notes_appear_in_filter(self, api):
        tid = api.tags_create('重点')['id']
        a, b = _mk(api, '甲'), _mk(api, '乙')
        api.notes_add_tag_many([a, b], tid)
        assert {n['id'] for n in api.notes_by_tag(tid)} == {a, b}


# ---------- 前端交互（e2e） ----------

def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('批量回归', os.path.join(PROJECT_ROOT, 'renderer', 'index.html'),
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


def _click_nth(window, n, ctrl=False):
    window.evaluate_js(
        "var it = document.querySelectorAll('.note-item')[%d];"
        "it.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:%s}));"
        % (n, 'true' if ctrl else 'false'))


@pytest.mark.e2e
def test_ctrl_click_selects_and_bar_appears(tmp_path, monkeypatch):
    """Ctrl 点击进入多选：出现操作条、计数正确、可取消，且不改变当前打开的笔记"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    for t in ('甲', '乙', '丙'):
        _mk(backend.api, t)

    def actions(window, result):
        result['bar_hidden_before'] = window.evaluate_js(
            "document.getElementById('bulk-bar').classList.contains('hidden')")
        result['active_before'] = window.evaluate_js("__app.state.activeNoteId")
        _click_nth(window, 0, ctrl=True)
        time.sleep(0.8)
        _click_nth(window, 1, ctrl=True)
        time.sleep(0.8)
        result['count'] = window.evaluate_js(
            "document.getElementById('bulk-count').textContent")
        result['selected'] = window.evaluate_js(
            "document.querySelectorAll('.note-item.selected').length")
        result['active_after'] = window.evaluate_js("__app.state.activeNoteId")
        window.evaluate_js("document.getElementById('bulk-cancel').click()")
        time.sleep(0.6)
        result['after_cancel'] = window.evaluate_js(
            "document.querySelectorAll('.note-item.selected').length")
        result['bar_hidden_after'] = window.evaluate_js(
            "document.getElementById('bulk-bar').classList.contains('hidden')")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['bar_hidden_before'] is True, '没多选时操作条应隐藏'
    assert res['count'] == '已选 2 项', res['count']
    assert res['selected'] == 2
    assert res['active_after'] == res['active_before'], \
        'Ctrl 点击是选择，不该顺带切换当前打开的笔记'
    assert res['after_cancel'] == 0 and res['bar_hidden_after'] is True


@pytest.mark.e2e
def test_bulk_delete_removes_selected_only(tmp_path, monkeypatch):
    """批量删除只动选中的那两篇（列表顺序 == 界面顺序，故从 DOM 读顺序）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    for t in ('甲', '乙', '丙'):
        _mk(backend.api, t)
    shown = []

    def actions(window, result):
        shown.extend(json.loads(window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.note-item')]"
            ".map(e => e.dataset.noteId))")))
        _click_nth(window, 0, ctrl=True)
        time.sleep(0.7)
        _click_nth(window, 1, ctrl=True)
        time.sleep(0.7)
        window.evaluate_js("document.getElementById('bulk-delete').click()")
        time.sleep(1.0)
        window.evaluate_js("document.getElementById('btn-confirm-ok').click()")
        time.sleep(2.0)
        result['left'] = window.evaluate_js("document.querySelectorAll('.note-item').length")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert len(shown) == 3, shown
    assert res['left'] == 1, '应只剩 1 篇（另两篇进回收站）'
    live = {n['id'] for n in backend.api.notes_list()}
    assert live == {shown[2]}, '被删的应是界面前两篇，留下的是第三篇'
    trashed = {t['id'] for t in backend.api.notes_trash_list()}
    assert set(shown[:2]) <= trashed, '删掉的应进回收站而不是彻底消失'


@pytest.mark.e2e
def test_shift_click_selects_range(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    for t in ('甲', '乙', '丙', '丁'):
        _mk(backend.api, t)

    def actions(window, result):
        _click_nth(window, 0, ctrl=True)          # 锚点
        time.sleep(0.6)
        window.evaluate_js(
            "document.querySelectorAll('.note-item')[2]"
            ".dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,shiftKey:true}));")
        time.sleep(0.8)
        result['count'] = window.evaluate_js(
            "document.querySelectorAll('.note-item.selected').length")
        result['all_selected'] = window.evaluate_js(
            "document.getElementById('bulk-all').click();"
            "document.querySelectorAll('.note-item.selected').length")

    res = _run(ns, actions)
    assert 'error' not in res, res
    assert res['count'] == 3, 'Shift 连选应选中 1~3 共 3 篇'
    assert res['all_selected'] == 4, '全选应选中全部 4 篇'
