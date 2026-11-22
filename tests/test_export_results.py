# -*- coding: utf-8 -*-
"""导出 / 导入之后说什么：在文件对话框里按「取消」不说话，做成了说做成了，没做成说真的原因。

以前桥接的导出方法取消和失败都返回 None，页面分不出来：
· 导出 Markdown：拿到 None 一律提示"导出失败：加密笔记需要先解锁"，按「取消」也是这一句；
· 导出 HTML / TXT / Word / Excel：页面根本不看返回值。导出了不提示，Word 文件正在 Word 里开着、写不进去也不提示；
  HTML 写不进去时异常一路抛到页面，提示条里是英文原文加整条路径；
· 备份全部数据、按笔记本 / 标签导出、导入 Markdown、表格视图导出 CSV：没做成和取消一样，一声不吭。
现在后端返回 {'ok': True, ...} 或 {'ok': False, 'error': 原因}，桥接只在取消时返回 None（app.pyw 的 `_saved`），
页面统一走 `showResultToast`。
"""
import base64
import builtins
import contextlib
import csv
import ctypes
import errno
import json
import os
import re
import sqlite3
import sys
import time
import zipfile

import pytest
from conftest import load_app_partial, wait_for_js, wait_until
from test_export_formats import _click_export, _run

BUSY = '文件可能正在别的程序里打开，或者这个位置不允许写入'
READ_BUSY = '文件可能正在别的程序里打开，或者没有读取它的权限'
LOCKED = '加密笔记需先解锁'
RELOCKED = '后端已锁定'
FORMATS = ('html', 'txt', 'docx', 'xlsx')


def _note(api, title='会议', body='# 会议\n\n正文\n', notebook_id=None):
    note_id = api.notes_create(notebook_id)['id']
    api.notes_update(note_id, {'title': title, 'content': body})
    return note_id


def _exports(api):
    """每一种导出，都写到 path 去：[(名字, 调用)]"""
    note_id = _note(api)
    notebook = api.notebooks_create('项目')['id']
    _note(api, '项目里的', '正文\n', notebook)
    calls = [(fmt, lambda path, fmt=fmt: api.export_note('会议', '<p>正文</p>', fmt, path)) for fmt in FORMATS]
    return calls + [
        ('md', lambda path: api.export_note_markdown(note_id, path)),
        ('zip', lambda path: api.export_all_to_zip(path)),
        ('scope', lambda path: api.export_notes_zip(path, notebook_id=notebook)),
        ('csv', lambda path: api.export_table_csv([note_id], path)),
    ]


@contextlib.contextmanager
def _held_open(path):
    """像 Word、Excel 那样独占打开一个文件：打开时不和别人共享读写"""
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    generic_read, share_nothing, open_existing, normal = 0x80000000, 0, 3, 0x80
    handle = kernel32.CreateFileW(path, generic_read, share_nothing, None, open_existing, normal, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


# ---------------- 后端：没做成时的原因 ----------------

def test_a_folder_in_the_way(api, tmp_path):
    """保存的位置是个同名文件夹：Windows 上写文件报的是 PermissionError"""
    target = tmp_path / '是个文件夹'
    target.mkdir()
    for name, export in _exports(api):
        assert export(str(target)) == {'ok': False, 'error': BUSY}, name
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith('.part')], '替换不了时临时文件要删掉'


@pytest.mark.skipif(sys.platform != 'win32', reason='独占打开文件是 Windows 上的行为')
def test_a_file_another_program_holds_open(api, tmp_path):
    """要覆盖的文件正被别的程序独占着（Word 开着 .docx、Excel 开着 .csv）：原因照实说，原来的文件不动。
    zip 也一样：打开就失败时那可能是一份旧备份，不能当成写到一半的删掉"""
    for name, export in _exports(api):
        target = tmp_path / ('旧的.' + name)
        target.write_text('旧内容', encoding='utf-8')
        with _held_open(str(target)):
            result = export(str(target))
        assert result == {'ok': False, 'error': BUSY}, name
        assert target.read_text(encoding='utf-8') == '旧内容', name
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith('.part')], '替换不了时临时文件要删掉'


def test_a_folder_that_is_gone(api, tmp_path):
    """对话框开着的时候文件夹被删了（或者 U 盘拔了）"""
    target = tmp_path / '已经删掉的文件夹' / '会议'
    for name, export in _exports(api):
        result = export(str(target))
        assert result['ok'] is False and result['error'].startswith('找不到保存的位置'), (name, result)
    assert not target.parent.exists()


def test_reasons_in_plain_words(backend_mod):
    """磁盘满这类在测试里造不出来的，直接看原因怎么写。异常原文是英文，还带着整条路径，不给人看"""
    text = backend_mod._file_error_text
    path = 'D:\\x.md'
    assert text(OSError(errno.ENOSPC, 'No space left on device', path)) == '磁盘空间不够'
    assert text(OSError(errno.EACCES, 'Permission denied', path)) == BUSY
    assert text(OSError(errno.EACCES, 'Permission denied', path), reading=True) == READ_BUSY
    assert text(OSError(errno.ENOENT, 'No such file or directory', path)).startswith('找不到保存的位置')
    assert text(OSError(errno.ENOENT, 'No such file or directory', path), reading=True).startswith('找不到这个文件')
    for reading, start in ((False, '写入文件时出错'), (True, '读取文件时出错')):
        other = text(OSError(errno.EIO, 'Input/output error', path), reading=reading)
        assert other.startswith(start) and 'error.log' in other and 'Input/output' not in other and 'D:' not in other
    assert text(ValueError('bug')).startswith('导出时出错') and 'error.log' in text(ValueError('bug'))
    assert text(ValueError('bug'), reading=True).startswith('导入时出错')


def test_a_conversion_error_leaves_the_file_alone(api, backend_mod, tmp_path, monkeypatch):
    """不是写文件出的错（转换出了 bug）：说去哪儿看原因，原来的文件也不会被清空（先转换、再打开文件）"""
    def broken(*args, **kwargs):
        raise ValueError('转换出错')
    for name in ('html_to_text', 'html_to_docx', 'html_to_xlsx', '_delta_to_markdown'):
        monkeypatch.setattr(backend_mod, name, broken)
    note_id = _note(api)
    for fmt in ('txt', 'docx', 'xlsx', 'md'):
        target = tmp_path / ('旧的.' + fmt)
        target.write_text('旧内容', encoding='utf-8')
        result = (api.export_note_markdown(note_id, str(target)) if fmt == 'md'
                  else api.export_note('会议', '<p>正文</p>', fmt, str(target)))
        assert result['ok'] is False and result['error'].startswith('导出时出错'), (fmt, result)
        assert 'error.log' in result['error'], fmt
        assert target.read_text(encoding='utf-8') == '旧内容', fmt


def test_an_unknown_format(api, tmp_path):
    out = tmp_path / 'out'
    out.mkdir()
    for fmt in ('pdf', '', None, ['html']):
        assert api.export_note('会议', '<p>正文</p>', fmt, str(out / 'x')) == {
            'ok': False, 'error': '不支持导出成这种格式'}, fmt
    assert os.listdir(out) == []


def test_a_half_written_export_keeps_the_old_file(api, backend_mod, tmp_path, monkeypatch):
    """写到一半失败（比如 U 盘满了）：同名的旧文件原样留着，写了一半的临时文件也删掉。

    以前直接打开目标文件写，旧文件一打开就被清空了，失败后手里只剩一个打不开的半截文件（两种 zip 再把半截删掉，
    旧备份照样没了）。现在都先写临时文件、写完再替换（`_replacing`）。八种导出各在写进去一部分之后报磁盘满
    """
    exports = _exports(api)
    full = OSError(errno.ENOSPC, 'No space left on device')

    def half_zip(zf, base, arc):                       # 两种 zip：已经写进去一部分
        zf.writestr('一部分.txt', '写了一半')
        raise full

    def half_file(_title, _html, path):                # Word / Excel：库已经往文件里写了一部分
        with builtins.open(path, 'wb') as fh:
            fh.write(b'PK\x03\x04half')
        raise full

    class HalfWriter:                                  # HTML / TXT / Markdown / CSV：第一次写只落一半
        def __init__(self, fh):
            self.fh = fh

        def write(self, text):
            self.fh.write(text[:len(text) // 2])
            raise full

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

    def opener(file, mode='r', *args, **kwargs):
        fh = builtins.open(file, mode, *args, **kwargs)
        return HalfWriter(fh) if 'w' in mode else fh

    monkeypatch.setattr(backend_mod.Api, '_zip_dir', staticmethod(half_zip))
    monkeypatch.setattr(backend_mod, 'html_to_docx', half_file)
    monkeypatch.setattr(backend_mod, 'html_to_xlsx', half_file)
    monkeypatch.setattr(backend_mod, 'open', opener, raising=False)
    out = tmp_path / '导出'                            # tmp_path 本身是数据目录，导出放单独的文件夹好数
    out.mkdir()
    for name, export in exports:
        target = out / ('旧的.' + name)
        target.write_text('旧内容', encoding='utf-8')
        assert export(str(target)) == {'ok': False, 'error': '磁盘空间不够'}, name
        assert target.read_text(encoding='utf-8') == '旧内容', name
    assert sorted(p.name for p in out.iterdir()) == sorted('旧的.' + name for name, _ in exports), \
        '写了一半的临时文件要删掉'


def test_replacing_swaps_the_file_in_only_when_it_is_complete(backend_mod, tmp_path):
    out = tmp_path / '导出'
    out.mkdir()
    target = out / '备份.zip'
    target.write_text('旧内容', encoding='utf-8')
    with backend_mod._replacing(str(target)) as part:
        assert os.path.dirname(part) == str(out), '临时文件要在同一目录：os.replace 跨盘就不是一步完成的'
        with open(part, 'w', encoding='utf-8') as fh:
            fh.write('新内容')
        assert target.read_text(encoding='utf-8') == '旧内容', '写完之前旧文件不能动'
    assert target.read_text(encoding='utf-8') == '新内容'
    with pytest.raises(OSError):
        with backend_mod._replacing(str(target)) as part:
            with open(part, 'w', encoding='utf-8') as fh:
                fh.write('写了一半')
            raise OSError(errno.ENOSPC, 'No space left on device')
    assert target.read_text(encoding='utf-8') == '新内容'
    assert [p.name for p in out.iterdir()] == ['备份.zip'], '失败时临时文件要删掉'


def test_a_zip_that_cannot_be_opened_keeps_the_old_file(api, tmp_path, monkeypatch):
    """打开就失败时目标文件没被动过，它可能是一份旧备份：只有写到一半的才删。
    （真实情况里打不开的文件通常也删不掉，所以这里直接让打开失败）"""
    def refused(*args, **kwargs):
        raise OSError(errno.EACCES, 'Permission denied')
    monkeypatch.setattr('zipfile.ZipFile', refused)
    notebook = api.notebooks_create('项目')['id']
    _note(api, '项目里的', '正文\n', notebook)
    for name, export in (('zip', api.export_all_to_zip),
                         ('scope', lambda path: api.export_notes_zip(path, notebook_id=notebook))):
        target = tmp_path / ('旧备份-%s.zip' % name)
        target.write_text('旧内容', encoding='utf-8')
        assert export(str(target)) == {'ok': False, 'error': BUSY}, name
        assert target.read_text(encoding='utf-8') == '旧内容', name


def test_an_undecryptable_body_is_not_exported_as_empty(api, backend_mod, tmp_path):
    """以前正文解不开就当成空的：写出一个只有标题的文件，还提示导出成功"""
    note_id = _note(api, '机密', '秘密内容\n')
    assert api.note_set_password(note_id, 'pass123456')            # 设完密码仍是解锁状态
    broken = backend_mod.ENC_PREFIX + base64.b64encode(b'\0' * 40).decode('ascii')
    backend_mod.conn.execute("UPDATE notes SET content = ? WHERE id = ?", (broken, note_id))
    backend_mod.conn.commit()
    target = tmp_path / '机密.md'
    assert api.export_note_markdown(note_id, str(target)) == {'ok': False, 'error': '正文无法解密，没有导出'}
    assert not target.exists()


# ---------------- 桥接：只在取消时返回 None ----------------

def test_the_bridge_exports(app_ns, tmp_path, monkeypatch):
    """每个导出按钮背后的桥接方法：取消 → None；导出了 → ok + 路径；写不进去 → 原因"""
    import backend as _b
    out = tmp_path / 'out'
    blocked = out / '是个文件夹'
    blocked.mkdir(parents=True)
    note_id = _note(_b.api)
    secret = _note(_b.api, '机密', '秘密\n')
    assert _b.api.note_set_password(secret, 'pass123456')
    _b.api.note_lock(secret)
    notebook = _b.api.notebooks_create('项目')['id']
    empty = _b.api.notebooks_create('空的')['id']
    _note(_b.api, '项目里的', '正文\n', notebook)
    answers = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: answers.pop(0))
    bridge = app_ns['AppApi'](_b.api)

    exports = [(fmt, lambda fmt=fmt: bridge.export_note('会议', '<p>正文</p>', fmt), {}) for fmt in FORMATS] + [
        ('md', lambda: bridge.export_markdown(note_id, '会议'), {}),
        ('zip', bridge.export_all, {}),
        ('scope', lambda: bridge.export_scope(notebook_id=notebook, label='项目'), {'count': 1}),
        ('csv', lambda: bridge.export_table_csv([note_id]), {'count': 1}),
    ]
    for name, export, extra in exports:
        path = str(out / ('导出.' + name))
        answers[:] = ['', path, str(blocked)]
        assert export() is None, name                                   # 按了「取消」
        assert export() == dict({'ok': True, 'path': path}, **extra), name
        assert export() == {'ok': False, 'error': BUSY}, name
        assert answers == [], name

    answers[:] = [str(out / '机密.md'), str(out / '空的.csv')]
    assert bridge.export_markdown(secret, '机密') == {'ok': False, 'error': LOCKED}
    # 范围里一篇都没有：不弹保存对话框，直接说没有（什么都没写，也不带路径）
    unused_tag = _b.api.tags_create('没用过的')['id']
    assert bridge.export_scope(notebook_id=empty, label='空的') == {'ok': True, 'count': 0}
    assert bridge.export_scope(tag_id=unused_tag, label='没用过的') == {'ok': True, 'count': 0}
    assert answers == [str(out / '空的.csv')], '范围是空的就不该弹保存对话框'
    assert bridge.export_table_csv([]) == {'ok': True, 'count': 0}
    assert sorted(os.listdir(out)) == sorted(['导出.' + name for name, _, _ in exports] + ['是个文件夹'])


def test_the_bridge_imports_markdown(app_ns, tmp_path, monkeypatch):
    """导入 Markdown：取消 → None；导入了 → ok + 笔记回执；读不出文件、写不进库 → 原因"""
    import backend as _b
    utf8, gbk, held = tmp_path / 'utf8.md', tmp_path / 'gbk.md', tmp_path / 'held.md'
    utf8.write_bytes('# 从外面来的\r\n\r\n正文\r\n'.encode('utf-8'))
    gbk.write_bytes('# 老文件\n\n用记事本存的\n'.encode('gbk'))
    held.write_text('# 开着的\n', encoding='utf-8')
    answers = []
    monkeypatch.setattr('tkinter.filedialog.askopenfilename', lambda **kw: answers.pop(0))
    bridge = app_ns['AppApi'](_b.api)

    answers[:] = ['', str(utf8), str(gbk), str(tmp_path / '不在了.md')]
    assert bridge.import_markdown_dialog() is None
    r = bridge.import_markdown_dialog()
    assert r['ok'] is True and r['note']['title'] == '从外面来的' and 'content' not in r['note'], r
    assert _b.api.notes_get(r['note']['id'])['content'] == '# 从外面来的\n\n正文\n'     # 换行照旧统一成 \n
    r = bridge.import_markdown_dialog()
    assert r['ok'] is True and r['note']['title'] == '老文件', r
    r = bridge.import_markdown_dialog()
    assert r['ok'] is False and r['error'].startswith('找不到这个文件'), r
    if sys.platform == 'win32':
        answers[:] = [str(held)]
        with _held_open(str(held)):
            assert bridge.import_markdown_dialog() == {'ok': False, 'error': READ_BUSY}

    monkeypatch.setattr(_b.api, 'import_markdown', lambda *args, **kwargs: None)
    answers[:] = [str(utf8)]
    assert bridge.import_markdown_dialog() == {'ok': False, 'error': '导入时出错，详细原因记在 error.log 里'}


# ---------------- e2e：从界面上点 ----------------

# 提示条只有一个元素，后一次会盖掉前一次：记下每一次写进去的字
RECORD_TOASTS = """
window.__toasts = [];
new MutationObserver(records => records.forEach(r => {
  if (r.target.id === 'save-toast' && r.addedNodes.length)
    window.__toasts.push(Array.from(r.addedNodes, n => n.textContent).join(''));
})).observe(document.body, {childList: true, subtree: true});
"""


def _wait_toasts(window, count):
    """等到恰好有 count 条提示（"后端已锁定"那一句不算，见 Markdown 那个用例的说明）。

    每一步都要等到自己的提示出来再点下一个：桥接调用各走各的线程，慢的（备份全部数据要先给数据库拍快照）
    会比后面点的那个晚回来，提示的先后就乱了（第一次跑就是这样）。count 没变的那一步（按了「取消」）
    先等一秒，让不该出现的提示有机会出现"""
    if window.evaluate_js('window.__toasts.length') == count:
        time.sleep(1)
    expr = 'window.__toasts.filter(t => !t.startsWith(%s)).length' % json.dumps(RELOCKED)
    return wait_for_js(window, expr, count, timeout=20)


def _all_toasts(window):
    time.sleep(1)                                # 最后一条之后多出来的提示也要等得到
    return window.evaluate_js('window.__toasts')


@pytest.mark.e2e
def test_markdown_export_says_what_happened(tmp_path, monkeypatch):
    """按「取消」：什么都不提示（以前提示"导出失败：加密笔记需要先解锁"）。导出了：说导出了。
    要覆盖的位置是个文件夹、对话框开着的时候笔记的解锁过期了：都说出真的原因，也都没写出文件"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    out = tmp_path / 'out'
    (out / '是个文件夹.md').mkdir(parents=True)
    secret = _note(backend.api, '机密', '秘密正文\n')
    assert backend.api.note_set_password(secret, 'pass123456')
    backend.api.note_lock(secret)
    plain = _note(backend.api, '会议', '正文\n')          # 后建的排在最前：启动时打开的是它

    answers = {'会议.md': ['', str(out / '会议.md'), str(out / '是个文件夹.md')], '机密.md': [str(out / '机密.md')]}
    asked = []

    def ask(**kw):
        asked.append(kw['initialfile'])
        if kw['initialfile'] == '机密.md':
            backend.api.note_lock(secret)        # 对话框开着的时候，后端的解锁过期了
        return answers[kw['initialfile']].pop(0)
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', ask)

    def actions(window, result):
        window.evaluate_js(RECORD_TOASTS)
        for count, toasts in ((1, 0), (2, 1), (3, 2)):          # 取消、导出了、目标是个文件夹
            _click_export(window, plain, 'md', '#md-preview', 'md', formulas=0)
            assert wait_until(lambda count=count: len(asked) >= count)
            assert _wait_toasts(window, toasts) == toasts, window.evaluate_js('window.__toasts')

        verify_panel = "document.getElementById('password-verify-panel').style.display"
        window.evaluate_js("document.querySelector('#note-list .note-item[data-note-id=\"%s\"]').click()" % secret)
        assert wait_for_js(window, verify_panel, 'flex')
        window.evaluate_js("document.getElementById('password-verify-input').value = 'pass123456'")
        window.evaluate_js("document.getElementById('btn-verify-password').click()")
        assert wait_for_js(window, verify_panel, 'none')
        assert wait_for_js(window, 'window.__app.state.activeNoteId', secret)
        _click_export(window, secret, 'md', '#md-preview', 'md', formulas=0)
        assert wait_until(lambda: len(asked) >= 4)
        assert _wait_toasts(window, 3) == 3
        result['toasts'] = _all_toasts(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert asked == ['会议.md'] * 3 + ['机密.md']
    # 前端每 30 秒和后端对一次解锁状态，赶上了会接着提示"后端已锁定"：那是该有的提示，不算多出来的
    toasts = [t for t in r['toasts'] if not t.startswith(RELOCKED)]
    assert toasts == ['Markdown 已导出', '导出失败：' + BUSY, '导出失败：' + LOCKED], r['toasts']
    assert sorted(os.listdir(out)) == ['会议.md', '是个文件夹.md']


@pytest.mark.e2e
def test_the_other_exports_say_what_happened(tmp_path, monkeypatch):
    """导出面板上其它几个按钮和表格视图的「导出 CSV」：取消不说话，做成了、没做成都说一句"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    out = tmp_path / 'out'
    blocked = out / '是个文件夹'
    blocked.mkdir(parents=True)
    notebook = backend.api.notebooks_create('项目')['id']
    empty = backend.api.notebooks_create('空的')['id']
    plain = _note(backend.api, '会议', '正文\n', notebook)
    outside = out / '外面写的.md'
    outside.write_text('# 外面写的\n\n正文\n', encoding='utf-8')

    saves = ['', str(out / '会议.html'), str(blocked), str(blocked), str(out / '项目.zip'), str(blocked),
             str(blocked)]
    opens = [str(blocked), str(outside)]
    asked = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: asked.append(kw['title']) or saves.pop(0))
    monkeypatch.setattr('tkinter.filedialog.askopenfilename', lambda **kw: asked.append(kw['title']) or opens.pop(0))

    def actions(window, result):
        window.evaluate_js('window.__app.setNotebookScope(%s)' % json.dumps(notebook))
        assert wait_for_js(window, 'window.__app.getCurrentNotebookId()', notebook)
        window.evaluate_js(RECORD_TOASTS)
        # （格式, 这一步之后应有几条提示）：取消、导出了、Word 写不进去、备份写不进去、导出当前笔记本、
        # 它也写不进去、导入读不出来、导入了
        steps = [('html', 0), ('html', 1), ('docx', 2), ('zip', 3), ('zip-notebook', 4), ('zip-notebook', 5),
                 ('md-import', 6), ('md-import', 7)]
        for count, (fmt, toasts) in enumerate(steps, 1):
            _click_export(window, plain, 'md', '#md-preview', fmt, formulas=0)
            assert wait_until(lambda count=count: len(asked) >= count), fmt
            assert _wait_toasts(window, toasts) == toasts, (fmt, window.evaluate_js('window.__toasts'))
        assert wait_for_js(window, 'window.__app.state.notes.length', 2)    # 导入的那篇进了列表
        window.evaluate_js("document.getElementById('btn-table-view').click()")
        assert wait_for_js(window, "document.querySelectorAll('#table-view-body tr[data-note]').length", 2)
        window.evaluate_js("document.getElementById('btn-table-csv').click()")
        assert wait_until(lambda: len(asked) >= 9)
        assert _wait_toasts(window, 8) == 8

        # 换到一个空的笔记本：「导出当前笔记本」直接说没有笔记，不弹保存对话框
        window.evaluate_js('window.__app.setNotebookScope(%s)' % json.dumps(empty))
        assert wait_for_js(window, 'window.__app.getCurrentNotebookId()', empty)
        window.evaluate_js("document.getElementById('btn-export').click()")
        assert wait_for_js(window, "getComputedStyle(document.getElementById('export-panel')).display !== 'none'", True)
        window.evaluate_js("document.querySelector('.export-option[data-format=\"zip-notebook\"]').click()")
        assert _wait_toasts(window, 9) == 9
        result['toasts'] = _all_toasts(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert asked == ['导出为 HTML 网页'] * 2 + ['导出为 Word 文档', '导出全部数据为备份包'] + ['导出所选范围'] * 2 + [
        '导入 Markdown', '导入 Markdown', '导出表格为 CSV']
    assert r['toasts'] == ['HTML 网页已导出', '导出失败：' + BUSY, '导出失败：' + BUSY, '已导出 1 篇笔记',
                           '导出失败：' + BUSY, '导入失败：' + READ_BUSY, '已导入「外面写的」',
                           '导出失败：' + BUSY, '这个范围里还没有笔记'], r['toasts']
    assert sorted(os.listdir(out)) == sorted(['是个文件夹', '外面写的.md', '会议.html', '项目.zip'])
    imported = [n for n in backend.api.notes_list(notebook) if n['title'] == '外面写的']
    assert len(imported) == 1, '导入的笔记跟着当前笔记本走'


@pytest.mark.e2e
def test_the_table_view_lists_every_search_match(tmp_path, monkeypatch):
    """搜索时表格视图和它导出的 CSV 要包含每一篇命中的笔记。列表第 13 轮改成只画看得见的十几行，
    表格视图却还从列表的 DOM 里数行：命中 60 篇，表格里只有画出来的那些"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    for i in range(60):
        _note(backend.api, '笔记 %02d' % i, '共同的词 %d\n' % i)
    _note(backend.api, '不相干', '别的内容\n')
    target = tmp_path / '表格.csv'
    asked = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: asked.append(1) or str(target))
    toast = "(document.getElementById('save-toast') || {}).textContent"

    def search(window, words):
        window.evaluate_js("document.getElementById('search-input').value = %s;"
                           "document.getElementById('search-input').dispatchEvent(new Event('input', {bubbles: true}))"
                           % json.dumps(words))

    def actions(window, result):
        search(window, '共同的词')
        assert wait_for_js(window, 'window.__app.state.searchMatched && window.__app.state.searchMatched.size', 60)
        result['rendered'] = window.evaluate_js("document.querySelectorAll('#note-list .note-item').length")
        window.evaluate_js("document.getElementById('btn-table-view').click()")
        wait_for_js(window, "document.querySelectorAll('#table-view-body tr[data-note]').length > 0", True)
        time.sleep(0.5)
        result['rows'] = window.evaluate_js("document.querySelectorAll('#table-view-body tr[data-note]').length")
        window.evaluate_js("document.getElementById('btn-table-csv').click()")
        result['toast'] = wait_for_js(window, toast, '已导出 60 行 CSV', timeout=10)

        # 一篇都没搜到：表格是空的，导出 CSV 直接说没有笔记，不弹保存对话框
        search(window, '谁也没写过的词')
        assert wait_for_js(window, "window.__app.state.searchQuery === '谁也没写过的词'"
                                   " && (window.__app.state.searchMatched || {size: 0}).size === 0", True)
        window.evaluate_js("document.getElementById('table-view-close').click()")
        window.evaluate_js("document.getElementById('btn-table-view').click()")
        assert wait_for_js(window, "document.querySelectorAll('#table-view-body tr[data-note]').length", 0) == 0
        window.evaluate_js("document.getElementById('btn-table-csv').click()")
        result['empty_toast'] = wait_for_js(window, toast, '表格里没有笔记', timeout=10)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['rendered'] < 60, '列表应该只画了看得见的几行，不然测不到要测的东西：%r' % r['rendered']
    assert r['rows'] == 60, r
    assert r['toast'] == '已导出 60 行 CSV', r
    with open(target, encoding='utf-8-sig', newline='') as f:
        assert len(list(csv.reader(f))) == 61                     # 表头 + 60 行
    assert r['empty_toast'] == '表格里没有笔记', r
    assert len(asked) == 1, '表格是空的时候不该弹保存对话框'


# ---------------- 「导出当前标签」跟着列表（2026-11-20 用户定的） ----------------

def test_the_tag_export_counts_what_the_list_shows(app_ns, monkeypatch):
    """导出标签时页面带上当前笔记本：这一本里没有这个标签的笔记就不弹保存对话框，哪怕别的笔记本里有。
    两个都给、没给名字时，默认文件名写"标签"（以前看 notebook_id，会写成"笔记本"）"""
    import backend as _b
    work = _b.api.notebooks_create('工作')['id']
    life = _b.api.notebooks_create('生活')['id']
    tag = _b.api.tags_create('重点')['id']
    _b.api.note_tags_set(_note(_b.api, '工作里的重点', '正文\n', work), [tag])
    _note(_b.api, '生活里的', '正文\n', life)
    asked = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: asked.append(kw['initialfile']) or '')
    bridge = app_ns['AppApi'](_b.api)
    assert bridge.export_scope(notebook_id=life, tag_id=tag, label='重点（生活）') == {'ok': True, 'count': 0}
    assert asked == [], '这一本里没有这个标签的笔记，就不该弹保存对话框'
    assert bridge.export_scope(notebook_id=work, tag_id=tag) is None
    assert len(asked) == 1 and re.fullmatch(r'我的记事本-标签-\d{8}-\d{6}\.zip', asked[0]), asked


@pytest.mark.e2e
def test_the_tag_export_follows_the_list(tmp_path, monkeypatch):
    """选了笔记本再按标签筛，「导出当前标签」导出的就是列表里那几篇（这个标签 × 这一本），
    按钮下面也这么写；选「全部笔记」时是这个标签的全部笔记。以前不管选了哪一本都导出这个标签的全部笔记。
    走真实界面和真实桥接，只换掉保存对话框，最后打开 zip 看里面是哪几篇"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    work = backend.api.notebooks_create('工作')['id']
    life = backend.api.notebooks_create('生活')['id']
    tag = backend.api.tags_create('重点')['id']
    in_work = _note(backend.api, '工作里的重点', '正文\n', work)
    _note(backend.api, '工作里的别的', '正文\n', work)
    in_life = _note(backend.api, '生活里的重点', '正文\n', life)
    backend.api.note_tags_set(in_work, [tag])
    backend.api.note_tags_set(in_life, [tag])
    targets = [tmp_path / '工作里的.zip', tmp_path / '全部.zip']
    asked = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename',
                        lambda **kw: asked.append(kw['initialfile']) or str(targets[len(asked) - 1]))
    toast = "(document.getElementById('save-toast') || {}).textContent"
    chip = "document.querySelector('.tag-filter-chip[data-tag-id=\"%s\"]')" % tag
    listed = "JSON.stringify(window.__app.state.notes.map(n => n.id).sort())"

    def export_tag(window):
        window.evaluate_js("document.getElementById('btn-export').click()")
        assert wait_for_js(window, "document.getElementById('export-panel').style.display", 'flex') == 'flex'
        descs = window.evaluate_js("[document.getElementById('export-scope-tag-desc').textContent,"
                                   " document.getElementById('export-scope-notebook-desc').textContent]")
        window.evaluate_js("document.querySelector('.export-option[data-format=\"zip-tag\"]').click()")
        return descs

    def actions(window, result):
        window.evaluate_js("window.__app.setNotebookScope(%s)" % json.dumps(work))
        assert wait_for_js(window, "window.__app.getCurrentNotebookId()", work) == work
        assert wait_for_js(window, "!!" + chip)
        window.evaluate_js(chip + ".click()")
        expected = json.dumps([in_work], separators=(',', ':'))          # 和 JSON.stringify 一样不带空格
        assert wait_for_js(window, listed, expected) == expected
        result['work'] = export_tag(window)
        result['work_toast'] = wait_for_js(window, toast, '已导出 1 篇笔记', timeout=10)
        # 标签筛选还在，换成「全部笔记」：列表是这个标签的全部笔记，导出的也是
        window.evaluate_js("window.__app.setNotebookScope(null)")
        expected = json.dumps(sorted([in_work, in_life]), separators=(',', ':'))
        assert wait_for_js(window, listed, expected) == expected
        result['all'] = export_tag(window)
        result['all_toast'] = wait_for_js(window, toast, '已导出 2 篇笔记', timeout=10)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['work'] == ['当前：「工作」里标签为「重点」的笔记（含附件）', '当前：工作 整本（含附件）'], r['work']
    assert r['all'] == ['当前：所有笔记本里标签为「重点」的笔记（含附件）', '先在侧边栏选择一个笔记本'], r['all']
    assert r['work_toast'] == '已导出 1 篇笔记' and r['all_toast'] == '已导出 2 篇笔记', r
    assert len(asked) == 2, asked
    assert re.fullmatch(r'我的记事本-重点（工作）-\d{8}-\d{6}\.zip', asked[0]), asked
    assert re.fullmatch(r'我的记事本-重点-\d{8}-\d{6}\.zip', asked[1]), asked
    for target, titles in zip(targets, (['工作里的重点'], ['工作里的重点', '生活里的重点']), strict=True):
        unpacked = tmp_path / ('unz-' + target.stem)
        with zipfile.ZipFile(target) as zf:
            zf.extract('notes.db', str(unpacked))
        conn = sqlite3.connect('file:%s?mode=ro' % (unpacked / 'notes.db'), uri=True)
        try:
            assert sorted(row[0] for row in conn.execute('SELECT title FROM notes')) == titles, target
        finally:
            conn.close()
