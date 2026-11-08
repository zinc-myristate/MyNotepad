# -*- coding: utf-8 -*-
"""改密码、笔记本改名，以及几个改名对话框。

· 改密码：笔记已解锁时，列表行钥匙按钮打开的「修改密码」以前走 note_set_password，
  不知道旧密码也能把密码换掉。现在已有密码时面板多一栏「当前密码」，修改走
  note_change_password（后端验旧密码），移除也读这一栏。
· 笔记本以前只能新建和删除：下拉里每一行加了改名按钮（F2 也行），规矩在后端 notebooks_update。
· 标签、视图的改名对话框传的是 `value`，而 showInputDialog 认的是 `defaultValue`：对话框弹出来是空的。
  视图菜单传了 `cancelText: '删除'`，showConfirmAsync 不认，按钮上写的仍是「取消」，点下去却是删除流程。
"""
import json
import os
import re

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_for_js
from test_ui_layout import _run

APP_JS = os.path.join(PROJECT_ROOT, 'renderer', 'js', 'app')
KEY = ("window.key = (key, opts={}) => (opts.on || document.activeElement).dispatchEvent("
       "new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true, ...opts}));")
TOAST = "document.getElementById('save-toast')?.textContent || ''"
PASSWORD_PANEL = "document.getElementById('password-panel').style.display"
INPUT_DIALOG = "document.getElementById('input-dialog').style.display"
INPUT_VALUE = "document.getElementById('input-dialog-input').value"


def _set(window, element_id, value):
    """按 id 填输入框。值走 JSON：引号、中文都不用另外转义"""
    window.evaluate_js('document.getElementById(%s).value = %s' % (json.dumps(element_id), json.dumps(value)))


def _click(window, element_id):
    window.evaluate_js('document.getElementById(%s).click()' % json.dumps(element_id))


def _clear_toast(window):
    """提示条只有一个元素：连着两次同样的提示，不先清掉就分不出第二次到没到"""
    window.evaluate_js("document.getElementById('save-toast') && (document.getElementById('save-toast').textContent = '')")


# ---------------- 静态：对话框的参数名 ----------------

def _scan_object_literal(src, start):
    """从 `{` 开始找到配对的 `}`，跳过引号和模板字符串里的括号"""
    depth, i, quote = 0, start, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == '\\':
                i += 1
            elif ch == quote:
                quote = None
        elif ch in '\'"`':
            quote = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError('对象字面量没有闭合：%r' % src[start:start + 60])


def test_dialog_calls_only_pass_parameters_the_dialog_reads():
    """showInputDialog 认 defaultValue、showConfirmAsync 以前不认 cancelText：传错了名字不报错，
    只是对话框悄悄少了东西（标签、视图改名弹出来是空的）。参数表从 01-core.js 的函数签名里读。"""
    core = open(os.path.join(APP_JS, '01-core.js'), encoding='utf-8').read()
    accepted = {}
    for fn in ('showInputDialog', 'showConfirmAsync', 'showInfoDialog'):
        m = re.search(r'export function ' + fn + r'\(\{(.*?)\}\s*=\s*\{\}\)', core, re.S)
        assert m, '01-core.js 里找不到 %s 的签名' % fn
        accepted[fn] = {part.split('=')[0].strip() for part in m.group(1).split(',') if part.strip()}
    assert 'defaultValue' in accepted['showInputDialog'] and 'cancelText' in accepted['showConfirmAsync']

    offences, calls = [], 0
    for name in sorted(os.listdir(APP_JS)):
        if not name.endswith('.js'):
            continue
        src = open(os.path.join(APP_JS, name), encoding='utf-8').read()
        for m in re.finditer(r'\b(showInputDialog|showConfirmAsync|showInfoDialog)\(\{', src):
            calls += 1
            literal = _scan_object_literal(src, m.end() - 1)
            keys = set(re.findall(r'(?:^|[{,])\s*([A-Za-z_]\w*)\s*:', literal))
            unknown = keys - accepted[m.group(1)]
            if unknown:
                offences.append('%s：%s 不认 %s' % (name, m.group(1), sorted(unknown)))
    assert calls >= 20, '对话框调用数量异常少（%d）：是不是解析错了' % calls
    assert not offences, '\n'.join(offences)


# ---------------- 真窗口：改密码 ----------------

@pytest.mark.e2e
def test_change_and_remove_password_ask_for_the_current_one(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    locked = backend.api.notes_create()
    backend.api.notes_update(locked['id'], {'title': '加密', 'content': '秘密正文'})
    assert backend.api.note_set_password(locked['id'], 'old-pass-1')
    backend.api.note_lock(locked['id'])
    plain = backend.api.notes_create()          # 后建的排在最前：启动时打开的是它，不是加密笔记
    backend.api.notes_update(plain['id'], {'title': '普通', 'content': '普通正文'})
    nid = locked['id']
    key_button = "document.querySelector('[data-note-id=\"%s\"] .note-item-password')"
    panel_state = """JSON.stringify({
      title: document.getElementById('password-panel-title').textContent,
      current: getComputedStyle(document.getElementById('password-current')).display !== 'none',
      remove: getComputedStyle(document.getElementById('btn-remove-password')).display !== 'none',
      focus: document.activeElement.id,
      newHint: document.getElementById('password-input1').placeholder,
    })"""

    def submit(window, current, new, confirm=None):
        _clear_toast(window)
        _set(window, 'password-current', current)
        _set(window, 'password-input1', new)
        _set(window, 'password-input2', new if confirm is None else confirm)
        _click(window, 'btn-save-password')

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.activeNoteId', plain['id'])
        window.evaluate_js(KEY)
        result['row_titles'] = window.evaluate_js(
            '[%s.title, %s.title]' % (key_button % nid, key_button % plain['id']))
        # 锁着：先弹解锁面板，解开后接着打开密码面板
        window.evaluate_js(key_button % nid + '.click()')
        assert wait_for_js(window, "document.getElementById('password-verify-panel').style.display", 'flex')
        _set(window, 'password-verify-input', 'old-pass-1')
        _click(window, 'btn-verify-password')
        assert wait_for_js(window, PASSWORD_PANEL, 'flex')
        result['manage'] = json.loads(window.evaluate_js(panel_state))

        submit(window, 'wrong-pass', 'new-pass-2')
        result['wrong'] = wait_for_js(window, TOAST, '当前密码错误')
        submit(window, '', 'new-pass-2')
        result['empty'] = wait_for_js(window, TOAST, '请输入当前密码')
        submit(window, 'old-pass-1', 'old-pass-1')
        result['same'] = wait_for_js(window, TOAST, '新密码与当前密码相同')
        result['still_open'] = window.evaluate_js(PASSWORD_PANEL)
        result['old_after_wrong'] = backend.api.note_verify_password(nid, 'old-pass-1')

        # 回车 = 修改
        _clear_toast(window)
        _set(window, 'password-current', 'old-pass-1')
        _set(window, 'password-input1', 'new-pass-2')
        _set(window, 'password-input2', 'new-pass-2')
        window.evaluate_js("key('Enter', {on: document.getElementById('password-input2')})")
        assert wait_for_js(window, PASSWORD_PANEL, 'none')
        result['changed'] = wait_for_js(window, TOAST, '密码已修改')
        backend.api.note_lock(nid)
        result['old_works'] = backend.api.note_verify_password(nid, 'old-pass-1')
        result['new_works'] = backend.api.note_verify_password(nid, 'new-pass-2')   # 顺带解回来

        # 移除：读的是「当前密码」那一栏，新密码两栏空着也不影响
        window.evaluate_js(key_button % nid + '.click()')
        assert wait_for_js(window, PASSWORD_PANEL, 'flex')
        _clear_toast(window)
        _set(window, 'password-current', 'old-pass-1')
        _click(window, 'btn-remove-password')
        result['remove_wrong'] = wait_for_js(window, TOAST, '当前密码错误')
        _clear_toast(window)
        _set(window, 'password-current', 'new-pass-2')
        _click(window, 'btn-remove-password')
        assert wait_for_js(window, PASSWORD_PANEL, 'none')
        result['removed'] = wait_for_js(window, TOAST, '密码已移除')
        result['still_has_pwd'] = backend.api.note_has_password(nid)

        # 没有密码的笔记：只有新密码两栏
        window.evaluate_js(key_button % plain['id'] + '.click()')
        assert wait_for_js(window, PASSWORD_PANEL, 'flex')
        result['set'] = json.loads(window.evaluate_js(panel_state))
        _set(window, 'password-input1', 'plain-pass-1')
        _set(window, 'password-input2', 'plain-pass-1')
        window.evaluate_js("key('Enter', {on: document.getElementById('password-input2')})")
        assert wait_for_js(window, "document.getElementById('confirm-dialog').style.display", 'flex')
        result['set_title'] = window.evaluate_js("document.getElementById('confirm-dialog-title').textContent")
        _click(window, 'btn-confirm-ok')
        result['plain_has_pwd'] = backend.api.note_has_password(plain['id'])

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert result['row_titles'] == ['修改密码', '设置密码'], result
    assert result['manage'] == {'title': '修改密码', 'current': True, 'remove': True,
                                'focus': 'password-current', 'newHint': '新密码'}, result
    assert result['wrong'] == '当前密码错误' and result['old_after_wrong'] is True, result
    assert result['empty'] == '请输入当前密码' and result['same'] == '新密码与当前密码相同', result
    assert result['still_open'] == 'flex', '验不过时面板要留着，用户好改：%s' % result
    assert result['changed'] == '密码已修改', result
    assert result['old_works'] is False and result['new_works'] is True, result
    assert result['remove_wrong'] == '当前密码错误' and result['removed'] == '密码已移除', result
    assert result['still_has_pwd'] is False, result
    assert result['set'] == {'title': '设置密码', 'current': False, 'remove': False,
                             'focus': 'password-input1', 'newHint': '输入密码'}, result
    assert result['set_title'] == '密码设置成功' and result['plain_has_pwd'] is True, result


# ---------------- 真窗口：笔记本、标签、视图的改名对话框 ----------------

@pytest.mark.e2e
def test_rename_notebook_tag_and_view_dialogs(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    genshin = backend.api.notebooks_create('原神')['id']
    backend.api.notebooks_create('工作')
    backend.api.notebook_id_by_name('收件箱')
    note = backend.api.notes_create(genshin)
    backend.api.notes_update(note['id'], {'title': '原神里的笔记', 'content': '正文'})
    backend.api.tags_create('项目/甲', '#888888')
    backend.api.saved_search_create('我的视图', 'tag:项目')
    item = ("[...document.querySelectorAll('#notebook-dropdown .notebook-drop-item')]"
            ".find(i => i.textContent.includes(%s))")

    def open_dropdown(window):
        _click(window, 'btn-notebook-select')
        assert wait_for_js(window, "document.getElementById('notebook-dropdown').style.display", 'block')

    def rename(window, name, new_name=None):
        """点那一行的改名按钮；给了 new_name 就填进去并确定"""
        open_dropdown(window)
        assert wait_for_js(window, 'Boolean(%s)' % (item % json.dumps(name)))
        window.evaluate_js("(%s).querySelector('.notebook-rename-btn').click()" % (item % json.dumps(name)))
        assert wait_for_js(window, INPUT_DIALOG, 'flex')
        if new_name is not None:
            _clear_toast(window)
            _set(window, 'input-dialog-input', new_name)
            _click(window, 'btn-input-ok')

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app && !document.querySelector(".note-skeleton"))')
        assert wait_for_js(window, "Boolean(document.querySelector('[data-ss-menu]'))")
        window.evaluate_js(KEY)

        rename(window, '原神')
        result['dialog'] = window.evaluate_js(
            "[document.getElementById('input-dialog-title').textContent, %s,"
            " document.getElementById('input-dialog-message').textContent, document.activeElement.id]" % INPUT_VALUE)
        _clear_toast(window)
        _set(window, 'input-dialog-input', '  星穹铁道 ')
        _click(window, 'btn-input-ok')
        result['renamed'] = wait_for_js(window, TOAST, '已重命名为「星穹铁道」')
        result['focus_back'] = wait_for_js(window, 'document.activeElement.id', 'btn-notebook-select')

        rename(window, '星穹铁道', '工作')
        result['duplicate'] = wait_for_js(window, TOAST, '重命名失败：已有同名的笔记本')

        # F2 打开同一个对话框；收件箱带提示，Esc 什么都不改
        open_dropdown(window)
        window.evaluate_js("(%s).focus(); key('F2')" % (item % json.dumps('收件箱')))
        assert wait_for_js(window, INPUT_DIALOG, 'flex')
        result['inbox'] = window.evaluate_js(
            "[%s, document.getElementById('input-dialog-message').textContent]" % INPUT_VALUE)
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, INPUT_DIALOG, 'none')

        # 当前笔记本改名：按钮上的名字跟着变
        open_dropdown(window)
        window.evaluate_js("(%s).click()" % (item % json.dumps('星穹铁道')))
        assert wait_for_js(window, "document.getElementById('notebook-name').textContent", '星穹铁道')
        rename(window, '星穹铁道', '崩坏')
        result['label'] = wait_for_js(window, "document.getElementById('notebook-name').textContent", '崩坏')

        # 标签改名：对话框带上原名
        _click(window, 'btn-tag-manager')
        assert wait_for_js(window, "Boolean(document.querySelector('[data-rename-tag]'))")
        window.evaluate_js("document.querySelector('[data-rename-tag]').click()")
        assert wait_for_js(window, INPUT_DIALOG, 'flex')
        result['tag'] = window.evaluate_js(INPUT_VALUE)
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, INPUT_DIALOG, 'none')

        # 视图菜单：取消按钮写的是「删除」，选「重命名」后对话框带上原名
        window.evaluate_js("document.querySelector('[data-ss-menu]').click()")
        assert wait_for_js(window, "document.getElementById('confirm-dialog').style.display", 'flex')
        result['view_buttons'] = window.evaluate_js(
            "[document.getElementById('btn-confirm-ok').textContent,"
            " document.getElementById('btn-confirm-cancel').textContent]")
        _click(window, 'btn-confirm-ok')
        assert wait_for_js(window, INPUT_DIALOG, 'flex')
        result['view'] = window.evaluate_js(INPUT_VALUE)
        window.evaluate_js("key('Escape')")
        assert wait_for_js(window, INPUT_DIALOG, 'none')

        # 共用的确认框：下一个调用方没传 cancelText，按钮要写回「取消」
        open_dropdown(window)
        window.evaluate_js("(%s).querySelector('.notebook-delete-btn').click()" % (item % json.dumps('工作')))
        assert wait_for_js(window, "document.getElementById('confirm-dialog').style.display", 'flex')
        result['delete_cancel'] = window.evaluate_js("document.getElementById('btn-confirm-cancel').textContent")
        _click(window, 'btn-confirm-cancel')
        assert wait_for_js(window, "document.getElementById('confirm-dialog').style.display", 'none')

    result = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in result, result
    assert result['dialog'] == ['重命名笔记本', '原神', '', 'input-dialog-input'], result
    assert result['renamed'] == '已重命名为「星穹铁道」', result
    assert result['focus_back'] == 'btn-notebook-select', '对话框关掉后焦点要回到笔记本按钮：%s' % result
    assert result['duplicate'] == '重命名失败：已有同名的笔记本', result
    assert result['inbox'][0] == '收件箱' and '另建一个「收件箱」' in result['inbox'][1], result
    assert result['label'] == '崩坏', result
    assert result['tag'] == '项目/甲', '标签改名对话框要带上原名：%s' % result
    assert result['view_buttons'] == ['重命名', '删除'] and result['view'] == '我的视图', result
    assert result['delete_cancel'] == '取消', result
    assert sorted(n['name'] for n in backend.api.notebooks_list()) == ['崩坏', '工作', '收件箱']
    assert backend.api.notes_list(genshin)[0]['title'] == '原神里的笔记', '改名不能动笔记的归属'
