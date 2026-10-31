"""Modal dismissal and virtual-list navigation run against real WebView2 DOM."""
import json
import time

import pytest
from conftest import load_app_partial, make_delta_note, wait_for_js
from test_ui_layout import _fill_sidebar, _resize, _run, _sidebar_ready

pytestmark = pytest.mark.e2e

ROW_IN_VIEW = """(() => {
  const row = document.activeElement.getBoundingClientRect();
  const list = document.getElementById('note-list').getBoundingClientRect();
  return row.top >= list.top - 3 && row.bottom <= list.bottom + 3;
})()"""
BOOT = 'Boolean(window.__app && !document.querySelector(".note-skeleton"))'
LOAD_CORE = "import('./js/app/01-core.js').then(m => window.core = m)"
KEY = ("window.key = (key, opts={}) => (opts.on || document.activeElement).dispatchEvent("
       "new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true, ...opts}));")


def test_modal_focus_dismissal_and_keyboard_context(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app && !document.querySelector(".note-skeleton"))')
        window.evaluate_js("import('./js/app/01-core.js').then(m => window.core = m)")
        assert wait_for_js(window, 'Boolean(window.core)')
        window.evaluate_js("window.key = (key, opts={}) => document.activeElement.dispatchEvent("
                           "new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true, ...opts}));"
                           "document.getElementById('btn-theme').focus();"
                           "document.getElementById('btn-theme').click();")
        assert window.evaluate_js("document.getElementById('theme-panel').contains(document.activeElement)")
        assert window.evaluate_js("document.getElementById('app').inert")
        window.evaluate_js("document.querySelector('.theme-option:last-child').focus(); key('Tab')")
        assert window.evaluate_js("document.activeElement.matches('#theme-panel .btn-close-panel')")
        window.evaluate_js("key('Tab', {shiftKey:true})")
        assert window.evaluate_js("document.activeElement.matches('.theme-option:last-child')")
        # A modal must not create a note or move focus to the background search.
        window.evaluate_js("key('n', {ctrlKey:true}); key('f', {ctrlKey:true})")
        assert not window.evaluate_js('Boolean(window.__app.state.activeNoteId)')
        window.evaluate_js("key('Escape', {isComposing:true})")
        assert window.evaluate_js("document.getElementById('theme-panel').style.display") == 'flex'
        # Nested panels close only the top layer and return to its trigger.
        window.evaluate_js("core.openPanel(document.getElementById('background-panel')); key('Escape')")
        assert window.evaluate_js("document.activeElement.matches('.theme-option:last-child')")
        window.evaluate_js("key('Escape')")
        assert window.evaluate_js('document.activeElement.id') == 'btn-theme'
        assert not window.evaluate_js("document.getElementById('app').inert")
        # Closing with X must settle a pending confirmation, not leave its caller hanging.
        window.evaluate_js("window.answer = 'pending'; core.showConfirmAsync({danger:true}).then(v=>answer=v)")
        assert window.evaluate_js('document.activeElement.id') == 'btn-confirm-cancel'
        window.evaluate_js("document.querySelector('#confirm-dialog .btn-close-panel').click()")
        assert wait_for_js(window, "window.answer === false")
        window.evaluate_js("window.answer = 'pending'; core.showInputDialog({defaultValue:'草稿'}).then(v=>answer=v)")
        assert window.evaluate_js('document.activeElement.id') == 'input-dialog-input'
        window.evaluate_js("key('Enter', {isComposing:true})")
        assert window.evaluate_js('window.answer') == 'pending'
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, 'window.answer === null')
        assert window.evaluate_js('document.activeElement.id') == 'btn-theme'
        # Quickly opening/closing command search must not steal focus via a delayed timer.
        window.evaluate_js("document.getElementById('btn-command-palette').focus();"
                           "document.getElementById('btn-command-palette').click()")
        assert wait_for_js(window, 'document.activeElement.id', 'quick-switch-input')
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, 'document.activeElement.id', 'btn-command-palette')
        # All shared overlays receive dialog labels and release background inertness.
        result['panels'] = window.evaluate_js("""(() => {
          const panels = [...document.querySelectorAll('.panel-overlay')];
          for (const p of panels) {
            core.openPanel(p);
            const dialog = p.querySelector('[role=dialog]');
            if (!dialog || !p.contains(document.activeElement) ||
                !(dialog.getAttribute('aria-label') || dialog.getAttribute('aria-labelledby'))) return p.id;
            core.closePanel(p);
          }
          return document.getElementById('app').inert ? 'still inert' : 'ok';
        })()""")

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert result['panels'] == 'ok', result


def test_virtual_list_keyboard_navigation_and_notebook_dropdown(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    for i in range(80):
        note = backend.api.notes_create()
        backend.api.notes_update(note['id'], {'title': '键盘笔记 %02d' % i, 'content': '正文 %02d' % i})
    backend.api.notebooks_create('键盘笔记本')

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        window.evaluate_js("window.key = key => document.activeElement.dispatchEvent("
                           "new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true}));"
                           "document.querySelector('.note-item').focus(); key('End')")
        last_id = window.evaluate_js('window.__app.state.notes.at(-1).id')
        assert window.evaluate_js('document.activeElement.dataset.noteId') == last_id
        assert window.evaluate_js("document.querySelectorAll('.note-item').length") < 40
        # focus() runs with preventScroll, so the row is only visible if the list scrolled to it.
        assert window.evaluate_js(ROW_IN_VIEW), 'End 之后焦点行应完整落在列表视口内'
        window.evaluate_js("key('Enter')")
        assert wait_for_js(window, 'window.__app.state.activeNoteId', last_id)
        window.evaluate_js("key('ArrowUp')")
        assert window.evaluate_js('document.activeElement.dataset.noteId === window.__app.state.notes.at(-2).id')
        window.evaluate_js("key('Home')")
        assert window.evaluate_js('document.activeElement.dataset.noteId === window.__app.state.notes[0].id')
        assert window.evaluate_js(ROW_IN_VIEW), 'Home 之后焦点行应完整落在列表视口内'
        for _ in range(25):
            window.evaluate_js("key('ArrowDown')")
        assert window.evaluate_js('document.activeElement.dataset.noteId === window.__app.state.notes[25].id')
        assert window.evaluate_js(ROW_IN_VIEW), '连续下移之后焦点行应完整落在列表视口内'
        window.evaluate_js("key('Home')")
        window.evaluate_js("window.focusedId = document.activeElement.dataset.noteId; window.__app.loadNotes()")
        assert wait_for_js(window, 'document.activeElement.dataset.noteId === window.focusedId')
        # Filtering must navigate within the current result set, including virtualized End.
        window.evaluate_js("document.getElementById('search-input').value = '键盘笔记 0';"
                           "document.getElementById('search-input').dispatchEvent(new Event('input',{bubbles:true}))")
        assert wait_for_js(window, 'window.__app.state.searchMatched.size', 10)
        window.evaluate_js("document.querySelector('.note-item').focus(); key('End')")
        assert window.evaluate_js('window.__app.state.searchMatched.has(document.activeElement.dataset.noteId)')
        # Row action buttons keep their own Enter behavior; the row never steals their key events.
        # Synthetic keys are not keyboard modality, so :focus-visible cannot reveal the button here.
        window.evaluate_js("const copy = document.activeElement.querySelector('.note-item-copy');"
                           "copy.style.display = 'flex'; copy.focus()")
        assert window.evaluate_js("document.activeElement.getAttribute('aria-label')") == '复制这篇笔记'
        window.evaluate_js("document.getElementById('btn-notebook-select').click()")
        assert wait_for_js(window, "document.activeElement.classList.contains('notebook-drop-item')")
        window.evaluate_js("key('ArrowUp'); key('Escape')")
        assert window.evaluate_js('document.activeElement.id') == 'btn-notebook-select'
        assert window.evaluate_js("document.getElementById('btn-notebook-select').getAttribute('aria-expanded')") == 'false'

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result


def test_decorations_release_the_modal_and_keep_typing_in_the_editor(tmp_path, monkeypatch):
    """贴纸/分割线曾直接写 style.display：面板留在栈里，#app 一直 inert，整个界面点不动。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    make_delta_note(backend, '装饰', json.dumps({'ops': [{'insert': 'abcdef\n'}]}))

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.noteFormat', 'delta')
        assert wait_for_js(window, 'Boolean(window.__app.state.quill)')
        for opener, panel, item, blot in (('btn-sticker', 'sticker-panel', '#sticker-grid .sticker-item', 'sticker'),
                                          ('btn-divider', 'divider-panel', '#divider-grid .divider-option', 'divider')):
            window.evaluate_js("__app.state.quill.setSelection(3, 0); __app.state.quill.focus();"
                               "document.getElementById('%s').click()" % opener)
            assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'flex')
            assert window.evaluate_js("document.getElementById('app').inert")
            window.evaluate_js("document.querySelector('%s').click()" % item)
            assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'none')
            assert not window.evaluate_js("document.getElementById('app').inert"), panel + ' 关闭后界面不能还是 inert'
            assert window.evaluate_js("document.activeElement.classList.contains('ql-editor')"), panel
            assert window.evaluate_js("JSON.stringify(__app.state.quill.getContents().ops).includes('%s')" % blot)
        # 面板栈是干净的：还能继续打开新面板，关闭后也不残留
        window.evaluate_js("document.getElementById('btn-math').click()")
        assert wait_for_js(window, "document.getElementById('math-panel').style.display", 'flex')
        window.evaluate_js("document.querySelector('#math-panel .btn-close-panel').click()")
        assert not window.evaluate_js("document.getElementById('app').inert")

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result


def test_escape_x_and_backdrop_all_run_the_panels_own_cancel(tmp_path, monkeypatch):
    """取消动作（清临时文件）不能只有"取消"按钮才走：Esc / X / 遮罩都是取消。"""
    ns = load_app_partial(monkeypatch, tmp_path)

    def actions(window, result):
        assert wait_for_js(window, BOOT)
        window.evaluate_js(KEY + """(() => {
          const a = window.pywebview.api; window.calls = [];
          a.pick_and_preview_icon = async () => ({success:true, tempPath:'icon.png', radiusPct:15, preview:'', origSize:'1x1'});
          a.cancel_icon = async p => { calls.push('icon:' + p); return true; };
          a.ocr_pick_image = async () => 'ocr.png';
          a.ocr_languages = async () => [{tag:'zh-Hans-CN', label:'中文'}];
          a.read_file_base64 = async () => '';
          a.ocr_recognize = async () => ({ok:true, text:'', has_text:false, ms:1, lines:[]});
          a.ocr_release = async p => { calls.push('ocr:' + p); return true; };
        })()""")
        routes = {
            'esc': "key('Escape')",
            'x': "document.querySelector('#%s .btn-close-panel').click()",
            'backdrop': "document.getElementById('%s').click()",
        }
        for route, how in routes.items():
            for opener, panel, expected in (('btn-change-icon', 'icon-preview-panel', 'icon:icon.png'),
                                            ('cap-ocr-file', 'ocr-panel', 'ocr:ocr.png')):
                window.evaluate_js("window.calls = []; document.getElementById('%s').click()" % opener)
                assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'flex')
                time.sleep(.15)     # 让打开流程跑完（OCR 打开后会接着识别一次）
                window.evaluate_js(how % panel if '%s' in how else how)
                assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'none'), (route, panel)
                assert wait_for_js(window, 'window.calls.length', 1), (route, panel)
                assert window.evaluate_js('window.calls[0]') == expected, (route, panel)

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result


def test_cancelling_unlock_keeps_the_open_note_saving(tmp_path, monkeypatch):
    """开着 A、点加密的 B、取消：A 还开着，所以 activeNoteId 不能被清掉——否则 A 的输入再也存不进去。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    locked = backend.api.notes_create()
    backend.api.notes_update(locked['id'], {'title': '加密', 'content': '秘密'})
    backend.api.note_set_password(locked['id'], 'secret-1')
    backend.api.note_lock(locked['id'])
    plain = backend.api.notes_create()          # 后建的排在最前：启动时打开的是它，不是加密笔记
    backend.api.notes_update(plain['id'], {'title': '普通', 'content': '普通正文'})

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.activeNoteId', plain['id'])
        window.evaluate_js(KEY)
        row = "document.querySelector('[data-note-id=\"%s\"]')" % locked['id']
        for route in ("key('Escape')", "document.getElementById('btn-cancel-verify').click()"):
            window.evaluate_js(row + '.click()')
            assert wait_for_js(window, "document.getElementById('password-verify-panel').style.display", 'flex')
            window.evaluate_js(route)
            assert wait_for_js(window, "document.getElementById('password-verify-panel').style.display", 'none')
            assert window.evaluate_js('window.__app.state.activeNoteId') == plain['id'], route
        # 笔记 A 仍然能存：这才是用户在意的结果
        window.evaluate_js("document.getElementById('note-title-input').value = '普通改过';"
                           "document.getElementById('note-title-input').dispatchEvent(new Event('input', {bubbles:true}));"
                           "window.__app.flushSave()")
        assert wait_for_js(window, "window.__saved = window.pywebview.api.notes_get(%r).then(n => window.__saved = n.title),"
                                   " typeof window.__saved === 'string' ? window.__saved : ''" % plain['id'], '普通改过') is not None
        assert backend.api.notes_get(plain['id'])['title'] == '普通改过'
        # 点密码按钮时 activeNoteId 已经指向加密笔记：这时取消才需要收起，回到能编辑的笔记
        window.evaluate_js(row + ".querySelector('[data-password-id], .note-item-password')?.click()")
        assert wait_for_js(window, "document.getElementById('password-verify-panel').style.display", 'flex')
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, 'window.__app.state.activeNoteId', plain['id'])

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result


def test_shortcuts_stay_behind_a_modal_even_when_focus_fell_to_body(tmp_path, monkeypatch):
    """弹窗里的控件被重建后焦点落到 body，按键不再经过弹窗：判据必须是面板栈，不能是事件路径。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    note = backend.api.notes_create()
    backend.api.notes_update(note['id'], {'title': '第一篇', 'content': '# 标题\n\n正文'})

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.activeNoteId', note['id'])
        window.evaluate_js(LOAD_CORE)
        assert wait_for_js(window, 'Boolean(window.core)')
        window.evaluate_js(KEY)
        window.evaluate_js("core.openPanel(document.getElementById('theme-panel')); document.activeElement.blur()")
        assert window.evaluate_js('document.activeElement === document.body')
        count = window.evaluate_js('window.__app.state.notes.length')
        window.evaluate_js("key('n', {ctrlKey:true}); key('p', {ctrlKey:true}); key('F', {ctrlKey:true, shiftKey:true})")
        time.sleep(.3)
        assert window.evaluate_js('window.__app.state.notes.length') == count, 'Ctrl+N 穿透到了弹窗背后'
        assert window.evaluate_js("document.getElementById('quick-switch-panel').style.display") == 'none'
        assert not window.evaluate_js("document.body.classList.contains('focus-mode')")
        window.evaluate_js("key('Escape')")
        assert window.evaluate_js("document.getElementById('theme-panel').style.display") == 'none'
        # Ctrl+P 在命令面板里仍然是开关；从源码编辑器进去，关掉要回到源码编辑器
        window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.focus()")
        assert window.evaluate_js("Boolean(document.activeElement.closest('.CodeMirror'))")
        window.evaluate_js("key('p', {ctrlKey:true})")
        assert wait_for_js(window, 'document.activeElement.id', 'quick-switch-input')
        window.evaluate_js("key('p', {ctrlKey:true})")
        assert wait_for_js(window, "document.getElementById('quick-switch-panel').style.display", 'none')
        assert window.evaluate_js("Boolean(document.activeElement.closest('.CodeMirror'))"), '关闭后焦点应回到源码编辑器'

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result


AUDIT = r"""
window.audit = root => {
  const focusable = 'button,input,textarea,select,a[href],summary,[tabindex],[contenteditable="true"]';
  const bad = new Set();
  const label = el => el.tagName.toLowerCase() + '.' + (el.id || String(el.className).split(/\s+/)[0]);
  root.querySelectorAll('*').forEach(el => {
    if (!el.getClientRects().length) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.pointerEvents === 'none') return;
    const reachable = el.matches(focusable) || el.closest(focusable) ||
      [...el.children].some(child => child.matches(focusable));
    // .ss-count sits in the same clickable row as .ss-name, which is the keyboard entry for it.
    if (cs.cursor === 'pointer' && !reachable && !el.matches('.ss-count')) bad.add('unreachable ' + label(el));
    if (el.matches('button, [role=button], a[href], input:not([type=hidden]), select, textarea')) {
      const name = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.title ||
        el.textContent.trim() || el.placeholder || el.closest('label') ||
        (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
      if (!name) bad.add('unnamed ' + label(el));
    }
  });
  return [...bad];
};
"""


def test_every_visible_control_is_reachable_and_named(tmp_path, monkeypatch):
    """看得见、点得动的东西，键盘也要够得着、读屏也要叫得出名字。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    note = backend.api.notes_create()
    backend.api.notes_update(note['id'], {'title': '笔记', 'content': '- [ ] 待办 @2026-11-01\n\n正文'})
    tag = backend.api.tags_create('审计', '#888888')
    backend.api.note_tags_set(note['id'], [tag['id'] if isinstance(tag, dict) else tag])
    backend.api.saved_search_create('视图', 'tag:审计')
    backend.api.reminder_create(note['id'], '提醒', '2030-01-01 09:00:00', 'none', 1)
    make_delta_note(backend, '富文本', json.dumps({'ops': [{'insert': '正文\n'}]}))

    def actions(window, result):
        assert wait_for_js(window, BOOT)
        assert wait_for_js(window, "Boolean(document.querySelector('.note-item'))")
        window.evaluate_js(LOAD_CORE)
        assert wait_for_js(window, 'Boolean(window.core)')
        window.evaluate_js(AUDIT + KEY)
        result['bad'] = {}
        for fmt in ('delta', 'md'):
            window.evaluate_js("__app.revealAndSelectNote(__app.state.notes.find(n => n.format === %r).id)" % fmt)
            assert wait_for_js(window, 'window.__app.state.noteFormat', fmt)
            assert wait_for_js(window, "Boolean(document.querySelector('.ss-name'))")
            if fmt == 'md':
                assert wait_for_js(window, "Boolean(document.querySelector('.tag-remove'))")
            result['bad'][fmt] = window.evaluate_js("audit(document.getElementById('app'))")
        panels = window.evaluate_js("[...document.querySelectorAll('.panel-overlay')].map(p => p.id)")
        openers = {'todo-panel': 'btn-todos', 'reminder-list-panel': 'btn-reminder-list', 'trash-panel': 'btn-trash',
                   'tag-manager-panel': 'btn-tag-manager', 'emoji-panel': 'btn-emoji', 'calendar-panel': 'btn-calendar'}
        for panel in panels:
            opener = openers.get(panel)
            if opener and window.evaluate_js("Boolean(document.getElementById('%s'))" % opener):
                window.evaluate_js("document.getElementById('%s').click()" % opener)
                time.sleep(.25)
            if window.evaluate_js("document.getElementById('%s').style.display" % panel) != 'flex':
                window.evaluate_js("core.openPanel(document.getElementById('%s'))" % panel)
            result['bad'][panel] = window.evaluate_js("audit(document.getElementById('%s'))" % panel)
            window.evaluate_js('core.closeAllPanels()')
        # 非原生按钮真的能用键盘触发
        window.evaluate_js("document.querySelector('.ss-name').focus(); key('Enter')")
        assert wait_for_js(window, "document.getElementById('search-input').value", 'tag:审计')
        window.evaluate_js("document.querySelector('.tag-remove').focus(); key(' ')")
        assert wait_for_js(window, "document.querySelectorAll('.tag-remove').length", 0) == 0
        window.evaluate_js("document.getElementById('btn-todos').click()")
        assert wait_for_js(window, "Boolean(document.querySelector('.todo-note'))")
        window.evaluate_js("document.querySelector('.todo-note').focus(); key('Enter')")
        assert wait_for_js(window, "document.getElementById('todo-panel').style.display", 'none')

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert not any(result['bad'].values()), result['bad']


def test_short_window_keeps_every_sidebar_control_reachable_and_named(tmp_path, monkeypatch):
    """矮窗口只改变摆法：工具收成图标、标签和视图收成窄条，按钮、名字、键盘路径都不能少。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _fill_sidebar(backend, 12, 3)

    def actions(window, result):
        _sidebar_ready(window, 12, 3)
        assert wait_for_js(window, 'Boolean(window.__app.state.activeNoteId)')
        _resize(window, 900, 600)
        window.evaluate_js(AUDIT + KEY)
        result['bad'] = window.evaluate_js("audit(document.getElementById('app'))")
        # 只有图标时，读屏名称全靠 aria-label；顺序还要和拖拽存下来的一致（都在 .sidebar-footer 下）
        result['tools'] = window.evaluate_js("""JSON.stringify([...document.querySelectorAll('.btn-sidebar-footer')].map(
          b => [b.tagName, b.tabIndex, b.parentElement.className, Boolean(b.getAttribute('aria-label'))]))""")
        # 滚到窄条外面的标签也要能用 Tab 走到：聚焦后它得自己进入可视范围
        result['chip'] = window.evaluate_js("""(() => {
          const rail = document.getElementById('tag-filter-list');
          const chip = rail.lastElementChild;
          chip.focus();
          const r = chip.getBoundingClientRect(), box = rail.getBoundingClientRect();
          return document.activeElement === chip && r.left >= box.left - 0.5 && r.right <= box.right + 0.5;
        })()""")
        # 列表变高之后，虚拟列表的键盘浏览仍然落在视口内
        window.evaluate_js("document.querySelector('.note-item').focus(); key('End')")
        result['end'] = window.evaluate_js(ROW_IN_VIEW)
        window.evaluate_js("key('Home')")
        result['home'] = window.evaluate_js(ROW_IN_VIEW)

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert result['bad'] == [], result['bad']
    tools = json.loads(result['tools'])
    assert len(tools) == 9, tools
    assert all(t == ['BUTTON', 0, 'sidebar-footer', True] for t in tools), tools
    assert result['chip'], '聚焦窄条外的标签时，它要滚进可视范围'
    assert result['end'] and result['home'], result
