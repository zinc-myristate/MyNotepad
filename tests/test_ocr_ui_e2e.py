# -*- coding: utf-8 -*-
"""第十一轮「识别文字」的实机回归（真 WebView2 + **真 OCR 引擎**）。

为什么用真引擎而不是假桥接：OCR 这条路的坑大多在"引擎到底能不能用"上
（语言包、打包后的 winrt 运行时），假桥接会把这些问题全掩盖掉。识别一张自己画的图
（`PIL` + 雅黑），只断言"关键词在、段落没散、没有多余空格"——逐字精度是引擎的事，
不该由测试来赌。没有引擎的机器自动跳过。

覆盖：面板从截图路径打开 → 真识别 → 插入当前笔记（图 + 文字）/ 复制 / 存为新笔记 /
换语言重识别并记住选择 / 图片右键 → 文字插在那张图下面。
"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note

import ocr

RENDERER = PROJECT_ROOT + '/renderer'


def _need_engine(lang=None):
    """没有引擎就跳过；给了 lang 还要求**那个语言包真的装了**。

    ⚠️ 只检查"有没有引擎"不够：GitHub Actions 的 windows-latest 装了英文引擎、
    却没装中文语言包，守卫会放行，后面的中文识别用例必然认不出字（实测 e2e 4 条全挂，
    识别结果是空串或乱码）。这与 tests/test_ocr.py 里是同一个坑。
    """
    if not ocr.ocr_ready():
        pytest.skip('这台机器没有可用的 Windows OCR 引擎/语言包')
    if lang and lang not in ocr.available_languages():
        pytest.skip('这台机器没有安装 %s 的 OCR 语言包' % lang)


def _make_image(path, lines, size=(760, 200)):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 26)
    except Exception:
        pytest.skip('没有中文字体，无法生成测试图')
    img = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(img)
    for i, text in enumerate(lines):
        draw.text((18, 16 + i * 40), text, font=font, fill='black')
    img.save(path)
    return path


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('识别文字回归', RENDERER + '/index.html',
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


def _source(window):
    return window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.getValue()")


def _panel_open(window):
    return window.evaluate_js(
        "document.getElementById('ocr-panel').style.display !== 'none'")


def _open_from_capture(window, path, note_id):
    window.evaluate_js("window.__ocr && window.__ocr.openFromCapture(%s, %s)"
                       % (json.dumps(path), json.dumps(note_id)))


@pytest.mark.e2e
def test_ocr_panel_recognizes_and_inserts_image_with_text(tmp_path, monkeypatch):
    """截图路径：面板里真识别出字 → 插入当前笔记 = 图片 + 文字（图在上）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _make_image(os.path.join(str(tmp_path), 'shot.png'),
                      ['第一段文字跨行排版测试，内容比较长，', '所以被排版折成了两行显示。'])
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '要插图的笔记', 'content': '原有正文\n'})

    def actions(window, result):
        time.sleep(2.0)
        _open_from_capture(window, img, nid)
        time.sleep(3.0)                             # 真识别 + 缩略图
        result['panel_open'] = _panel_open(window)
        result['text'] = window.evaluate_js("document.getElementById('ocr-text').value")
        result['status'] = window.evaluate_js("document.getElementById('ocr-status').textContent")
        result['lang_options'] = window.evaluate_js(
            "JSON.stringify([...document.getElementById('ocr-lang').options].map(o => o.value))")
        result['thumb_ok'] = window.evaluate_js(
            "(document.getElementById('ocr-thumb').getAttribute('src') || '').indexOf('data:image') === 0")
        result['hint'] = window.evaluate_js("document.getElementById('ocr-hint').textContent")
        _click(window, '#ocr-insert')
        time.sleep(2.2)
        result['source'] = _source(window)
        result['closed'] = not _panel_open(window)
        result['attachments'] = len(backend.api.attachments_list(nid))
        result['body'] = backend.api.notes_get(nid)['content']
        # 前端 closeOcr() 对"非附件来源"的图一定会请求删除；这里给的是**用户自己的图**
        # （没有 mynotepad_ 前缀、也不在系统临时目录），所以后端必须拒绝 —— 顺手删掉
        # 用户的原图是不可逆的。
        result['src_intact'] = os.path.isfile(img)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['panel_open'] is True
    assert '排版' in r['text'] and '两行' in r['text'], '没识别出预期文字：%r' % r['text']
    assert '第 一' not in r['text'], '中文之间不该有空格：%r' % r['text']
    assert '\n\n' not in r['text'], '折行的同一段不该被拆开：%r' % r['text']
    assert '识别完成' in r['status']
    assert 'zh-Hans-CN' in json.loads(r['lang_options'])
    assert r['thumb_ok'] is True, '面板里要能看到待识别的图'
    assert '文字会插在当前笔记的光标处' in r['hint']
    assert r['closed'] is True, '插入后面板应当收起'
    assert r['attachments'] == 1, '图片只该进一次附件'
    assert '![识别图片](attachments/%s/' % nid in r['source'], r['source']
    assert r['source'].index('排版') > r['source'].index('![识别图片]'), '文字要在图片下面'
    assert r['body'] and '排版' in r['body']
    assert r['src_intact'] is True, '用户给的原图被删了——ocr_release 只该删自己造的临时图'


@pytest.mark.e2e
def test_ocr_panel_copy_and_save_as_note(tmp_path, monkeypatch):
    """复制（成功后收起面板）与存为新笔记（进「收件箱」、标题取识别文字第一行）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _make_image(os.path.join(str(tmp_path), 'a.png'), ['会议纪要：周一上午十点'])
    backend.api.notes_create()

    def actions(window, result):
        time.sleep(2.0)
        # 先测"存为新笔记"
        _open_from_capture(window, img, '')
        time.sleep(3.0)
        result['text'] = window.evaluate_js("document.getElementById('ocr-text').value")
        _click(window, '#ocr-note')
        time.sleep(2.5)
        notes = backend.api.notes_list()
        result['titles'] = [n['title'] for n in notes]
        result['notebooks'] = [nb['name'] for nb in backend.api.notebooks_list()]
        newest = [n for n in notes if '会议' in n['title'] or '纪要' in n['title']]
        result['body'] = backend.api.notes_get(newest[0]['id'])['content'] if newest else ''
        result['closed'] = not _panel_open(window)
        # 再测"复制"
        _open_from_capture(window, img, '')
        time.sleep(3.0)
        _click(window, '#ocr-copy')
        time.sleep(0.8)
        result['toast'] = window.evaluate_js(
            "(document.getElementById('save-toast') || {}).textContent || ''")
        result['copy_closed'] = not _panel_open(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert '会议' in r['text'] or '纪要' in r['text'], r['text']
    assert '收件箱' in r['notebooks']
    assert any(('会议' in t or '纪要' in t) for t in r['titles']), r['titles']
    assert '![' in r['body'] and '十点' in r['body'], r['body']
    assert r['closed'] is True
    assert '复制' in r['toast']
    assert r['copy_closed'] is False, '复制不该把面板关掉（用户可能还要插入）'


@pytest.mark.e2e
def test_ocr_language_switch_is_remembered(tmp_path, monkeypatch):
    """换语言会重新识别，并把选择存进设置（下次直接用）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    langs = ocr.available_languages()
    if len(langs) < 2:
        pytest.skip('这台机器只装了一种 OCR 语言，测不了切换')
    other = [x for x in langs if x != ocr.OCR_LANG_DEFAULT][0]
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _make_image(os.path.join(str(tmp_path), 'b.png'), ['Chinese 中文 mixed 123'])
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': '笔记', 'content': ''})

    def actions(window, result):
        time.sleep(2.0)
        _open_from_capture(window, img, nid)
        time.sleep(3.0)
        result['before'] = window.evaluate_js("document.getElementById('ocr-lang').value")
        window.evaluate_js(
            "(function(){var s=document.getElementById('ocr-lang');"
            "s.value=%s; s.dispatchEvent(new Event('change',{bubbles:true}));})()"
            % json.dumps(other))
        time.sleep(3.0)
        result['status'] = window.evaluate_js("document.getElementById('ocr-status').textContent")
        result['stored'] = backend.api.settings_get('ocr_language')

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['before'] == ocr.OCR_LANG_DEFAULT
    assert '识别完成' in r['status'] or '没有识别到' in r['status']
    assert r['stored'] == other, '语言选择要记进设置'


@pytest.mark.e2e
def test_ocr_attachment_right_click_inserts_below_that_image(tmp_path, monkeypatch):
    """图片右键识别：文字插在**那张图**下面（Markdown 与富文本各一次）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _make_image(os.path.join(str(tmp_path), 'c.png'), ['图里的文字在这里'])
    md_id = backend.api.notes_create()['id']
    backend.api.notes_update(md_id, {'title': '带图的笔记', 'content': '开头\n\n之后\n'})
    saved = backend.api.file_copy_to_note(img, md_id, 'image')
    rel = 'attachments/%s/%s' % (md_id, saved['filename'])
    backend.api.notes_update(md_id, {
        'content': '开头\n\n![图](%s)\n\n之后\n' % rel})

    def right_click_preview(window):
        window.evaluate_js(
            "(function(){var img=document.querySelector('#md-preview img[data-md-src]');"
            "if(!img) return 'no-img';"
            "img.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:200,clientY:200}));"
            "return 'ok';})()")

    def actions(window, result):
        time.sleep(2.0)
        window.evaluate_js(
            "(function(){var el=[...document.querySelectorAll('.note-item')]"
            ".find(n => n.textContent.indexOf('带图的笔记') >= 0); if(el) el.click();})()")
        time.sleep(2.5)
        result['preview_imgs'] = window.evaluate_js(
            "document.querySelectorAll('#md-preview img[data-md-src]').length")
        result['right_click'] = right_click_preview(window)
        time.sleep(1.0)
        result['menu_shown'] = window.evaluate_js(
            "document.querySelectorAll('.checklist-context-menu [data-action=\"ocr\"]').length")
        _click(window, '.checklist-context-menu [data-action="ocr"]')
        time.sleep(3.0)
        result['panel_open'] = _panel_open(window)
        result['text'] = window.evaluate_js("document.getElementById('ocr-text').value")
        result['hint'] = window.evaluate_js("document.getElementById('ocr-hint').textContent")
        _click(window, '#ocr-insert')
        time.sleep(2.2)
        result['source'] = _source(window)
        result['attachments'] = len(backend.api.attachments_list(md_id))

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['preview_imgs'] >= 1, '预览里应当有那张图'
    assert r['menu_shown'] == 1, '右键菜单没出来'
    assert r['panel_open'] is True
    assert '文字' in r['text'], r['text']
    assert '这张图片的下面' in r['hint'], '这条路的提示要说明插到图下面'
    src = r['source']
    assert '图里的文字' in src
    img_at = src.index('![图](')
    assert src.index('图里的文字') > img_at, '文字必须在图片那一行之后：%r' % src
    assert r['attachments'] == 1, '已经在笔记里的图不该被再复制一份'


@pytest.mark.e2e
def test_ocr_editor_image_right_click_inserts_below_embed(tmp_path, monkeypatch):
    """同一件事在**富文本**笔记里：右键编辑器里的图片 → 文字插在那个 embed 之后"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    img = _make_image(os.path.join(str(tmp_path), 'd.png'), ['图片里的字'])
    nid = make_delta_note(backend, '富文本带图', '')
    saved = backend.api.file_copy_to_note(img, nid, 'image')
    delta = {'ops': [
        {'insert': '开头文字\n'},
        {'insert': {'image': {'id': saved['id'], 'filename': saved['filename'],
                              'storedPath': saved['storedPath']}}},
        {'insert': '\n结尾\n'},
    ]}
    backend.api.notes_update(nid, {'content': json.dumps(delta)})

    def actions(window, result):
        time.sleep(2.0)
        result['is_delta'] = window.evaluate_js("window.__app.state.noteFormat")
        result['editor_imgs'] = window.evaluate_js(
            "document.querySelectorAll('.ql-editor img[data-filename]').length")
        result['img_attrs'] = window.evaluate_js(
            "(function(){var im=document.querySelector('.ql-editor img[data-filename]');"
            "if(!im) return 'none'; var h=im.closest('[data-stored-path]');"
            "return JSON.stringify({fn: im.getAttribute('data-filename'),"
            "sp: im.getAttribute('data-stored-path'),"
            "holder: h ? h.getAttribute('data-stored-path') : null});})()")
        result['direct_ocr'] = ocr.recognize(saved['storedPath'])
        window.evaluate_js(
            "(function(){var im=document.querySelector('.ql-editor img[data-filename]');"
            "if(!im) return;"
            "im.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:300,clientY:300}));})()")
        time.sleep(1.0)
        result['menu_shown'] = window.evaluate_js(
            "document.querySelectorAll('.checklist-context-menu [data-action=\"ocr\"]').length")
        _click(window, '.checklist-context-menu [data-action="ocr"]')
        time.sleep(3.0)
        result['panel_open'] = _panel_open(window)
        result['status'] = window.evaluate_js("document.getElementById('ocr-status').textContent")
        result['text'] = window.evaluate_js("document.getElementById('ocr-text').value")
        _click(window, '#ocr-insert')
        time.sleep(2.0)
        result['content'] = window.evaluate_js(
            "JSON.stringify(window.__app.state.quill.getContents())")
        result['attachments'] = len(backend.api.attachments_list(nid))
        result['saved_body'] = backend.api.notes_get(nid)['content']

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['is_delta'] == 'delta'
    assert r['editor_imgs'] >= 1, '富文本笔记里应当渲染出那张图'
    assert r['menu_shown'] == 1
    assert r['panel_open'] is True
    assert '图片里的字' in r['text'], ('status=%r text=%r attrs=%r direct=%r'
                                      % (r['status'], r['text'], r.get('img_attrs'),
                                         (r.get('direct_ocr') or {}).get('text')))
    ops = json.loads(r['content'])['ops']
    img_index = text_after = None
    pos = 0
    for op in ops:
        ins = op.get('insert')
        if isinstance(ins, dict) and ins.get('image') and img_index is None:
            img_index = pos
        elif isinstance(ins, str) and '图片里的字' in ins:
            text_after = pos
        pos += len(ins) if isinstance(ins, str) else 1
    assert img_index is not None and text_after is not None, ops
    assert text_after > img_index, '文字要插在图片 embed 之后：%r' % ops
    assert r['attachments'] == 1, '已经在笔记里的图不该被再复制一份'
    assert '图片里的字' in r['saved_body']
