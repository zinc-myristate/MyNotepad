# -*- coding: utf-8 -*-
"""无头 pywebview 端到端：驱动真实前端验证保存链路（需要显示环境，默认跳过）"""
import json
import sqlite3
import threading
import time

import pytest

from conftest import PROJECT_ROOT, load_app_partial

pytestmark = pytest.mark.e2e


def _run_window(ns, js_actions, wait_before=7, wait_after=2):
    """启动无头窗口，执行 js_actions(window, result) 后销毁。挂载与生产一致的 closing 兜底。"""
    import webview
    result = {}
    window = webview.create_window(
        "E2E测试", PROJECT_ROOT + r'\renderer\index.html',
        js_api=ns['api'], width=900, height=600)
    import backend
    window.events.closing += ns['make_closing_handler'](window, backend.api)

    def runner():
        try:
            time.sleep(wait_before)  # 等 app.js 初始化并自动选中第一篇笔记
            js_actions(window, result)
            time.sleep(wait_after)
        except Exception as e:
            result['error'] = repr(e)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


def test_title_only_edit_persists(tmp_path, monkeypatch):
    """纯标题修改（不动正文）经 500ms 防抖后必须落库——回归保护（曾因去重基线 bug 丢失）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    test_title = '标题落库验证_' + str(int(time.time()))

    def actions(window, result):
        active = window.evaluate_js("state.activeNoteId")
        result['active'] = active
        assert active == nid, f'前端未自动选中预置笔记: {active}'
        window.evaluate_js(
            "dom.titleInput.value = %r;"
            "dom.titleInput.dispatchEvent(new Event('input', {bubbles:true}));" % test_title)
        result['dot_dirty'] = window.evaluate_js(
            "document.getElementById('save-dot').classList.contains('dirty')")
        time.sleep(2)  # > 500ms 防抖
        result['dot_saved'] = window.evaluate_js(
            "!document.getElementById('save-dot').classList.contains('dirty')"
            " && !document.getElementById('save-dot').classList.contains('error')")
        conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
        result['db_title'] = conn.execute(
            "SELECT title FROM notes WHERE id=?", (nid,)).fetchone()[0]
        conn.close()

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert result['db_title'] == test_title
    assert result['dot_dirty'] is True, '输入后圆点应为未保存态'
    assert result['dot_saved'] is True, '防抖保存后圆点应回到已保存态'


def test_closing_flush_saves_last_edits(tmp_path, monkeypatch):
    """关窗兜底：输入后不等 500ms 防抖立即关窗，closing 事件同步落库不丢输入"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = backend.api.notes_create()['id']
    test_title = '关窗兜底验证_' + str(int(time.time()))

    def actions(window, result):
        active = window.evaluate_js("state.activeNoteId")
        assert active == nid, f'前端未自动选中预置笔记: {active}'
        window.evaluate_js(
            "dom.titleInput.value = %r;"
            "dom.titleInput.dispatchEvent(new Event('input', {bubbles:true}));" % test_title)
        # 不 sleep：立即销毁（防抖 500ms 定时器不会触发，全靠 closing 兜底）

    result = _run_window(ns, actions, wait_after=0)
    assert 'error' not in result, result
    conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
    saved_title = conn.execute("SELECT title FROM notes WHERE id=?", (nid,)).fetchone()[0]
    conn.close()
    assert saved_title == test_title, '关窗前最后的输入应由 closing 兜底落库'


def test_search_filters_note_list(tmp_path, monkeypatch):
    """搜索框驱动后端 notes_search：正文命中显示、未命中隐藏"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    a = backend.api.notes_create()['id']
    backend.api.notes_update(a, {'title': '苹果笔记', 'content': '{"ops":[{"insert":"这里讲水果种植技术\\n"}]}'})
    b = backend.api.notes_create()['id']
    backend.api.notes_update(b, {'title': '汽车笔记', 'content': '{"ops":[{"insert":"这里讲发动机保养\\n"}]}'})

    def actions(window, result):
        window.evaluate_js(
            "dom.searchInput.value = '水果种植';"
            "dom.searchInput.dispatchEvent(new Event('input', {bubbles:true}));")
        time.sleep(1.5)  # > 200ms 搜索防抖 + 桥接往返
        result['hidden'] = window.evaluate_js(
            "JSON.stringify([...dom.noteList.querySelectorAll('.note-item')]"
            ".map(el => [el.dataset.noteId, el.classList.contains('hidden-by-search')]))")

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    import json
    hidden = dict(json.loads(result['hidden']))
    assert hidden[a] is False, '正文命中的笔记不应被隐藏'
    assert hidden[b] is True, '未命中的笔记应被隐藏'


def test_image_dict_embed_renders_and_persists(tmp_path, monkeypatch):
    """图片外置：dict 引用进 Delta → 异步渲染出 data URI → 位置 dataset 落库（图片外置核心链路）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    import base64
    nid = backend.api.notes_create()['id']
    # Python 侧落盘一张 1x1 PNG（与生产插入路径一致）
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
    src = tmp_path / 'img.png'
    src.write_bytes(png)
    info = backend.api.file_copy_to_note(str(src), nid, 'image')
    assert info and info['storedPath']

    def actions(window, result):
        # 以 dict 形式插入（NoteImageBlot 覆盖内置 image blot 后的新格式）。
        # 注意：必须构造 JS 对象字面量，不能塞 JSON 字符串（否则走 legacy string 路径）
        js_obj = "{id: %r, filename: %r, storedPath: %r}" % (
            info['id'], info['filename'], info['storedPath'])
        window.evaluate_js("state.quill.insertEmbed(0, 'image', %s);" % js_obj)
        result['ops_value'] = window.evaluate_js(
            "JSON.stringify(state.quill.getContents().ops[0].insert.image)")
        time.sleep(1.5)  # 异步 read_file_base64 渲染
        result['img_src'] = window.evaluate_js(
            "state.quill.root.querySelector('img').src")
        # 模拟 .img-resizable 写位置 → value() 收进 Delta → 保存落库
        window.evaluate_js(
            "var im = state.quill.root.querySelector('img'); im.dataset.x = '10'; im.dataset.w = '320';")
        window.evaluate_js("debouncedSave()")
        time.sleep(2)  # > 500ms 防抖
        conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
        result['db_content'] = conn.execute("SELECT content FROM notes WHERE id=?", (nid,)).fetchone()[0]
        conn.close()

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert '"storedPath"' in result['ops_value'], 'Delta 应为 dict 引用'
    assert result['img_src'].startswith('data:image/'), '异步渲染应产出 data URI'
    assert '"x":10' in result['db_content'] and '"storedPath"' in result['db_content'], \
        '位置与引用应落库（位置持久化修复点）'


def test_pin_button_delegation(tmp_path, monkeypatch):
    """列表事件委托：容器级监听路由 data-pin-id 点击（原每行 5 个监听器已移除）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    a = backend.api.notes_create()['id']
    b = backend.api.notes_create()['id']

    def actions(window, result):
        result['items'] = window.evaluate_js("dom.noteList.querySelectorAll('.note-item').length")
        window.evaluate_js("document.querySelector('[data-pin-id=\"%s\"]').click()" % b)
        time.sleep(1.5)
        conn = sqlite3.connect('file:' + str(tmp_path / 'notes.db') + '?mode=ro', uri=True)
        result['pinned'] = conn.execute("SELECT is_pinned FROM notes WHERE id=?", (b,)).fetchone()[0]
        conn.close()

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert result['items'] == 2
    assert result['pinned'] == 1, '委托后的置顶按钮点击应生效'


def test_confirm_async_dialog(tmp_path, monkeypatch):
    """showConfirmAsync：确认按钮 resolve(true)、取消按钮 resolve(false)"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()

    def actions(window, result):
        window.evaluate_js(
            "window.__cv = 'pending'; showConfirmAsync({message:'t1'}).then(v => { window.__cv = v; });"
            "setTimeout(() => document.getElementById('btn-confirm-ok').click(), 300);")
        time.sleep(1.5)
        result['ok_path'] = window.evaluate_js("window.__cv")
        window.evaluate_js(
            "window.__cv = 'pending'; showConfirmAsync({message:'t2'}).then(v => { window.__cv = v; });"
            "setTimeout(() => document.getElementById('btn-confirm-cancel').click(), 300);")
        time.sleep(1.5)
        result['cancel_path'] = window.evaluate_js("window.__cv")

    result = _run_window(ns, actions)
    assert 'error' not in result, result
    assert result['ok_path'] is True, '确认按钮应 resolve(true)'
    assert result['cancel_path'] is False, '取消按钮应 resolve(false)'
