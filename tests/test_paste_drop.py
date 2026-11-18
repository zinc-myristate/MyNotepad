# -*- coding: utf-8 -*-
"""粘贴、拖进笔记的文件（2026-11-18）：存成这篇笔记的附件，能显示的图片插成图片，别的插成附件。

以前（实测）：
· Markdown 笔记（新笔记默认）里粘贴截图没反应，拖进图片没反应；拖进 .txt、.csv 这类文件，CodeMirror 自己把文件内容
  整个当文字贴进正文（PDF 开头那几行也贴进来过）
· 富文本笔记里粘贴或拖进一张图会插进两张：Quill 自带的 uploader 插一张 data URI，02-editor.js 另外监听的 paste / drop
  又插一张附件。拖进 PDF 这类文件什么都没有（靠 `file.path`，那是 Electron 的属性，WebView2 里没有）
现在页面把文件内容读出来（base64）交给桥接 `file_add_to_note`，代码在 renderer/js/app/32-paste-drop.js。
"""
import base64
import json
import os
import re
import tempfile
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note, wait_for_js
from test_export_formats import _run

PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
PNG = base64.b64decode(PNG_B64)
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def _drop_folders():
    """file_add_to_note 用的临时文件夹（用完就该删掉）"""
    return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith('mynotepad_drop_')}


def _attachments(backend, note_id):
    return [tuple(r) for r in backend.conn.execute(
        "SELECT original_name, type FROM attachments WHERE note_id = ? ORDER BY created_at, rowid", (note_id,))]


# ---------------- 桥接：file_add_to_note ----------------

def test_a_pasted_image_becomes_an_attachment(app_ns):
    import backend as _b
    bridge = app_ns['AppApi'](_b.api)
    note = _b.api.notes_create()['id']
    before = _drop_folders()
    info = bridge.file_add_to_note(note, '截图 1.png', _b64(PNG), 'image')
    assert re.fullmatch(r'img_[0-9a-f]{32}\.png', info['filename']), info
    assert info['original_name'] == '截图 1.png' and info['file_size'] == len(PNG)
    with open(info['storedPath'], 'rb') as f:
        assert f.read() == PNG
    assert _attachments(_b, note) == [('截图 1.png', 'image')]
    assert _drop_folders() == before, '临时文件夹用完要删'


def test_any_file_and_the_names_the_page_sends(app_ns):
    """名字来自页面：Windows 不认的字符换成全角，设备名加 `_`，太长截断，扩展名一直留着"""
    import backend as _b
    bridge = app_ns['AppApi'](_b.api)
    note = _b.api.notes_create()['id']
    for name, shown, ext in (('资料.pdf', '资料.pdf', '.pdf'), ('a/b:c?.pdf', 'a／b：c？.pdf', '.pdf'),
                             ('CON.txt', '_CON.txt', '.txt'), ('汉' * 300 + '.pdf', '汉' * 100 + '.pdf', '.pdf'),
                             ('没有扩展名', '没有扩展名', ''), ('', '附件', '')):
        info = bridge.file_add_to_note(note, name, _b64(PDF), 'file')
        assert info['original_name'] == shown, (name, info)
        assert re.fullmatch(r'file_[0-9a-f]{32}' + re.escape(ext), info['filename']), (name, info)
    assert bridge.file_add_to_note(note, '', _b64(PNG), 'image')['original_name'] == '粘贴的图片'


def test_refusals(app_ns):
    import backend as _b
    bridge = app_ns['AppApi'](_b.api)
    note = _b.api.notes_create()['id']
    before = _drop_folders()
    too_big = 'A' * ((app_ns['_DROP_MAX_BYTES'] + 2) // 3 * 4 + 4)
    assert '超过 50 MB' in bridge.file_add_to_note(note, '大.bin', too_big, 'file')['error']
    assert '没读全' in bridge.file_add_to_note(note, 'x.png', '这不是 base64', 'image')['error']
    assert '没读全' in bridge.file_add_to_note(note, 'x.png', PNG_B64[:-3], 'image')['error']
    assert bridge.file_add_to_note(note, 'x.exe', _b64(b'x'), 'program') == {'error': '参数不对'}
    assert bridge.file_add_to_note(note, 'x.png', None, 'image') == {'error': '参数不对'}
    assert bridge.file_add_to_note('不存在的笔记', 'x.png', _b64(PNG), 'image') == {'error': '笔记不存在'}
    assert _b.conn.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] == 0
    assert _drop_folders() == before


def test_the_size_limit_is_the_same_on_both_sides(app_ns):
    """页面在读文件之前就按这个数判（太大的文件不读进内存），桥接再判一次"""
    with open(os.path.join(PROJECT_ROOT, 'renderer', 'js', 'app', '32-paste-drop.js'), encoding='utf-8') as f:
        m = re.search(r'MAX_DROP_BYTES = (\d+) \* 1024 \* 1024;', f.read())
    assert m and int(m.group(1)) * 1024 * 1024 == app_ns['_DROP_MAX_BYTES']


# ---------------- e2e：真的发 paste / drop 事件 ----------------

HELPERS = """
window.__file = (name, type, b64) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], name, {type});
};
window.__transfer = (files, text) => {
  const dt = new DataTransfer();
  files.forEach(f => dt.items.add(f));
  if (text) dt.setData('text/plain', text);
  return dt;
};
window.__paste = (target, dt) => {
  const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
  target.dispatchEvent(ev);
  return ev.defaultPrevented;
};
window.__drop = (target, dt, x, y) => {
  const ev = new DragEvent('drop', {dataTransfer: dt, clientX: x, clientY: y, bubbles: true, cancelable: true});
  target.dispatchEvent(ev);
  return ev.defaultPrevented;
};
'ok'
"""
CM = "document.querySelector('#md-editor .CodeMirror').CodeMirror"


def _png(name='image.png'):
    return "__file(%s, 'image/png', %s)" % (json.dumps(name), json.dumps(PNG_B64))


def _pdf(name='资料.pdf'):
    return "__file(%s, 'application/pdf', %s)" % (json.dumps(name), json.dumps(_b64(PDF)))


@pytest.mark.e2e
def test_markdown_note_takes_pasted_and_dropped_files(tmp_path, monkeypatch):
    """粘贴截图 → 一行图片；剪贴板里有文字 → 照常贴文字；拖进图片和 PDF → 落点处一行图片、一行附件链接，
    PDF 的内容不会当文字贴进来；超过 50 MB 的不读、说一声；文件拖到编辑器以外的地方 → 光标显示不能放"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    note = backend.api.notes_create()['id']
    backend.api.notes_update(note, {'title': '粘贴', 'content': '第一行\n第二行\n'})
    value = CM + '.getValue()'

    def actions(window, result):
        assert wait_for_js(window, "window.__app.state.activeNoteId === %s && window.__app.state.noteFormat === 'md'"
                                   % json.dumps(note), True)
        window.evaluate_js(HELPERS)
        window.evaluate_js(CM + '.focus(); ' + CM + '.setCursor(0, 3)')
        result['image_paste_prevented'] = window.evaluate_js(
            '__paste(%s.getInputField(), __transfer([%s]))' % (CM, _png()))
        assert wait_for_js(window, value + ".includes('![image.png](attachments/')", True)

        # 剪贴板里有文字：这边不管，CodeMirror 自己把文字贴进去（它也会 preventDefault，所以只看结果）
        window.evaluate_js("__paste(%s.getInputField(), __transfer([%s], '一段文字'))" % (CM, _png('不该加进来.png')))
        assert wait_for_js(window, value + ".includes('一段文字')", True)

        line2 = json.loads(window.evaluate_js('JSON.stringify(%s.charCoords({line: 1, ch: 0}, "window"))' % CM))
        result['drop_prevented'] = window.evaluate_js('__drop(%s.getScrollerElement(), __transfer([%s, %s]), %f, %f)' % (
            CM, _png('拖进来的.png'), _pdf(), line2['left'] + 1, (line2['top'] + line2['bottom']) / 2))
        assert wait_for_js(window, value + ".includes('资料.pdf](attachments/')", True)

        window.evaluate_js("window.__big = new File([new Uint8Array(51 * 1024 * 1024)], '大文件.zip', {type: 'application/zip'})")
        window.evaluate_js('__paste(%s.getInputField(), __transfer([window.__big]))' % CM)
        result['big_toast'] = wait_for_js(window, "(document.getElementById('save-toast') || {}).textContent",
                                          '「大文件.zip」超过 50 MB，请用工具栏的「附件」按钮添加')

        guard = ("(() => { const dt = __transfer([%s]);"
                 " const ev = new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true});"
                 " document.getElementById('btn-notebook-select').dispatchEvent(ev);"
                 " return [ev.defaultPrevented, dt.dropEffect]; })()" % _pdf())
        result['outside'] = window.evaluate_js(guard)
        time.sleep(0.5)
        result['content'] = window.evaluate_js(value)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['image_paste_prevented'] is True and r['drop_prevented'] is True
    path = r'\(attachments/%s/(img|file)_[0-9a-f]{32}\.(png|pdf)\)' % note
    lines = r['content'].split('\n')
    assert re.fullmatch('第一行!\\[image\\.png\\]' + path + '一段文字', lines[0]), lines
    assert re.fullmatch('!\\[拖进来的\\.png\\]' + path, lines[1]), lines
    assert re.fullmatch('\\[📎 资料\\.pdf\\]' + path + '第二行', lines[2]), lines
    assert '%PDF' not in r['content'], 'PDF 的内容不能当文字贴进来'
    assert _attachments(backend, note) == [('image.png', 'image'), ('拖进来的.png', 'image'), ('资料.pdf', 'file')]
    assert r['big_toast'] == '「大文件.zip」超过 50 MB，请用工具栏的「附件」按钮添加'
    assert r['outside'] == [True, 'none'], '文件拖到编辑器以外：不接'


@pytest.mark.e2e
def test_rich_text_note_inserts_each_file_once(tmp_path, monkeypatch):
    """粘贴一张截图只插一张（以前一张 data URI + 一张附件）；拖进图片和 PDF：一张图、一张附件卡片（以前 PDF 什么都没有）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    note = make_delta_note(backend, '富文本', '{"ops":[{"insert":"正文第一段\\n"}]}')
    picked = tmp_path / '工具栏选的.pdf'
    picked.write_bytes(PDF)
    ns['pick_attachment_file'] = lambda: str(picked)
    embeds = ("JSON.stringify(window.__app.state.quill.getContents().ops.filter(op => op.insert && typeof op.insert === 'object')"
              ".map(op => op.insert.image ? (typeof op.insert.image === 'string' ? 'data-uri' : 'image')"
              " : op.insert.attachment ? 'card:' + op.insert.attachment.originalName : Object.keys(op.insert)[0]))")

    def actions(window, result):
        assert wait_for_js(window, "window.__app.state.activeNoteId === %s && window.__app.state.noteFormat === 'delta'"
                                   % json.dumps(note), True)
        window.evaluate_js(HELPERS)
        quill = 'window.__app.state.quill'
        window.evaluate_js('%s.setSelection(%s.getLength() - 1, 0)' % (quill, quill))
        window.evaluate_js('__paste(%s.root, __transfer([%s]))' % (quill, _png()))
        assert wait_for_js(window, embeds, '["image"]')
        time.sleep(1)                                        # 以前的第二张会晚一点到
        result['after_paste'] = window.evaluate_js(embeds)

        para = json.loads(window.evaluate_js("JSON.stringify(%s.root.querySelector('p').getBoundingClientRect())" % quill))
        window.evaluate_js('__drop(%s.root, __transfer([%s, %s]), %f, %f)' % (
            quill, _png('拖进来的.png'), _pdf(), para['x'] + 4, para['y'] + para['height'] / 2))
        assert wait_for_js(window, embeds + ".includes('card:')", True)
        time.sleep(1)
        result['after_drop'] = window.evaluate_js(embeds)

        # 工具栏「附件」按钮那条路：后端拒绝时以前照样插进一张空的附件卡片，现在说原因
        monkeypatch.setattr(backend, 'MAX_ATTACH_SIZE', 10)
        window.evaluate_js("document.getElementById('save-toast') && (document.getElementById('save-toast').textContent = '')")
        window.evaluate_js("document.getElementById('btn-insert-attachment').click()")
        result['toolbar_toast'] = wait_for_js(window, "(document.getElementById('save-toast') || {}).textContent",
                                              '插入附件失败：文件超过 0MB 限制')
        result['after_toolbar'] = window.evaluate_js(embeds)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['after_paste'] == '["image"]', r
    assert sorted(json.loads(r['after_drop'])) == ['card:资料.pdf', 'image', 'image'], r
    assert r['toolbar_toast'] == '插入附件失败：文件超过 0MB 限制', r
    assert r['after_toolbar'] == r['after_drop'], '后端拒绝时不能插进空的附件卡片'
    assert _attachments(backend, note) == [('image.png', 'image'), ('拖进来的.png', 'image'), ('资料.pdf', 'file')]
