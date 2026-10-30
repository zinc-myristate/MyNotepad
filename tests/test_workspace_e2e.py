"""Workspace modes must preserve edits, undo history and usable editor geometry."""
import json

import pytest
from conftest import load_app_partial, wait_for_js
from test_ui_layout import _run

pytestmark = pytest.mark.e2e


def test_workspace_modes_preserve_buffer_and_fill_available_height(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    original = '# 原稿\n\n切换视图不能丢失这段文字。\n'
    backend.api.notes_update(nid, {'title': '视图切换验证', 'content': original})
    backend.api.settings_set('workspace_markdown_view', 'source')

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        assert wait_for_js(window, 'document.body.dataset.mdView', 'source')
        # Use the live CodeMirror instance: programmatic view changes must not recreate it.
        window.evaluate_js("window.workspaceCM = document.querySelector('.CodeMirror').CodeMirror;"
                           "workspaceCM.replaceRange('新增的一行', {line: 3, ch: 0});"
                           "window.workspaceUndo = workspaceCM.historySize().undo;")
        window.evaluate_js("document.querySelector('button[data-md-view=preview]').click()")
        assert wait_for_js(window, 'document.body.dataset.mdView', 'preview')
        assert '新增的一行' in window.evaluate_js("document.getElementById('md-preview').textContent")
        assert window.evaluate_js("document.getElementById('md-source-pane').getClientRects().length") == 0
        window.evaluate_js("document.querySelector('button[data-md-view=split]').click()")
        assert wait_for_js(window, 'document.body.dataset.mdView', 'split')
        window.resize(900, 600)
        assert wait_for_js(window, 'document.body.dataset.mdView', 'source')
        assert window.evaluate_js("document.querySelector('button[data-md-view=split]').disabled")
        window.evaluate_js("document.querySelector('button[data-md-view=preview]').click()")
        assert wait_for_js(window, 'document.body.dataset.mdView', 'preview')
        assert window.evaluate_js("document.getElementById('md-preview-pane').clientWidth") > 400
        window.evaluate_js("document.querySelector('button[data-md-view=source]').click();"
                           "document.getElementById('btn-focus-mode').click()")
        assert wait_for_js(window, "getComputedStyle(document.getElementById('sidebar')).display", 'none')
        assert window.evaluate_js("workspaceCM === document.querySelector('.CodeMirror').CodeMirror")
        assert window.evaluate_js('workspaceCM.historySize().undo === workspaceUndo')
        window.evaluate_js("workspaceCM.undo(); document.getElementById('btn-focus-mode').click()")
        assert window.evaluate_js('workspaceCM.getValue()') == original
        # Grid regression: source editor fills the space down to the status bar, no blank 1fr row.
        result['geometry'] = json.loads(window.evaluate_js("""JSON.stringify((() => {
          const rect = id => document.getElementById(id).getBoundingClientRect();
          const md = rect('md-editor'), status = rect('editor-status');
          const area = rect('editor-area');
          return {height: md.height, gap: status.top - md.bottom,
            overflow: md.right - area.right, cmHeight: workspaceCM.getWrapperElement().clientHeight};
        })())"""))
        assert wait_for_js(window, 'document.getElementById("save-label").textContent', '已保存到本地')
        assert backend.api.notes_get(nid)['content'] == original
        assert backend.api.settings_get('workspace_markdown_view') == 'source'
        # Focus shortcut must not also open Ctrl+F's find UI.
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',"
                           "{key:'F', ctrlKey:true, shiftKey:true, bubbles:true}));")
        assert wait_for_js(window, "document.body.classList.contains('focus-mode')")
        assert window.evaluate_js("document.getElementById('find-bar').classList.contains('hidden')")

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert result['geometry']['height'] > 220, result
    assert result['geometry']['cmHeight'] > 180, result
    assert abs(result['geometry']['gap']) <= 1, result
    assert result['geometry']['overflow'] <= 1, result


def test_empty_workspace_actions_and_dark_theme(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app && !document.querySelector(".note-skeleton"))')
        assert window.evaluate_js('document.getElementById("title-row").getClientRects().length') == 0
        window.evaluate_js("document.getElementById('btn-welcome-browse').click()")
        assert wait_for_js(window, "document.getElementById('quick-switch-panel').style.display", 'flex')
        window.evaluate_js("document.getElementById('quick-switch-panel').click();"
                           "document.getElementById('btn-welcome-new').click()")
        assert wait_for_js(window, 'Boolean(window.__app.state.activeNoteId)')
        assert window.evaluate_js("document.getElementById('no-note-hint').classList.contains('hidden')")
        assert window.evaluate_js('document.getElementById("title-row").getClientRects().length') == 1
        window.evaluate_js("document.getElementById('btn-theme').click();"
                           "document.querySelector('.theme-option[data-theme=dark]').click()")
        assert wait_for_js(window, 'document.body.dataset.theme', 'dark')
        # Empty/title controls and footer must fit the application's supported minimum window.
        window.resize(900, 600)
        result['bounds'] = json.loads(window.evaluate_js("""JSON.stringify((() => {
          const buttons = [...document.querySelectorAll('.workspace-bar button, .sidebar-footer button')]
            .filter(b => b.getClientRects().length);
          return buttons.filter(b => { const r = b.getBoundingClientRect();
            return r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || r.left < 0;
          }).map(b => b.id);
        })())"""))

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert not result['bounds'], result
