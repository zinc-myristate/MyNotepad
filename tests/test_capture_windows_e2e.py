# -*- coding: utf-8 -*-
"""第十轮：两个捕获窗口的实机回归（真浏览器，假的桥接）。

为什么这么测：截图那条路的风险集中在**覆盖窗页面自己的坐标与事件**上——选区跟不跟手、
工具条出不出来、放大镜取到的是不是光标底下那个像素、Enter/Esc 走到哪个动作。这些只有真
浏览器能验。而"选区 → 图片像素"的后半段是纯函数，已在 test_capture_templates_unit.py 里
逐条钉死；这里用假桥接提供一张**已知内容**的冻屏图，于是两半能接上：
放大镜读到的颜色必须等于合成图对应位置的颜色，也就证明了 JS 的 CSS↔图片 约定与 Python 一致。

真窗口创建/藏主窗/抓屏那一段（要真屏幕）不进自动化，走打包后的手工探针。
"""
import base64
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT
from PIL import Image

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')

QUADRANTS = {
    'red': (255, 0, 0),
    'green': (0, 200, 0),
    'blue': (0, 0, 255),
    'white': (255, 255, 255),
}


def _make_image(path, w, h):
    """四象限测试图：左上红 / 右上绿 / 左下蓝 / 右下白（放大镜取色靠它断言）。"""
    img = Image.new('RGB', (w, h), QUADRANTS['white'])
    for y in range(h):
        for x in range(w):
            if x < w // 2 and y < h // 2:
                img.putpixel((x, y), QUADRANTS['red'])
            elif x >= w // 2 and y < h // 2:
                img.putpixel((x, y), QUADRANTS['green'])
            elif x < w // 2:
                img.putpixel((x, y), QUADRANTS['blue'])
    img.save(path)
    return img.size


def _run(factory, actions, wait_before=6):
    import webview
    result = {}
    window = factory()

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


class OverlayBridge:
    """覆盖窗的假桥接：给一张已知的冻屏图，并记录 capture_commit 的入参。

    客户区物理尺寸取「视口 × scale」，与真机 200% 缩放同形；这样 CSS(30,40) 就是图片里的
    (60,80)，断言能直接写成人能看懂的坐标。

    ⚠️ **不要**把 pywebview 的 Window 对象挂成本对象的属性：pywebview 生成 JS API 时会
    递归内省 js_api 的**所有**属性，碰到 Window.native.* 就会在非 UI 线程上访问 .NET/COM，
    实测直接死锁（主线程等 GUI 线程、destroy 等 Invoke）。这里的 viewport 由页面自己上报，
    所以根本不需要窗口引用。
    """

    def __init__(self, tmp_path, scale=2, note_id='note-1'):
        self.tmp = str(tmp_path)
        self.scale = scale
        self.note_id = note_id
        self.commits = []
        self.ready = 0
        self.info_calls = 0
        self.img_path = None
        self.client = None

    def capture_overlay_info(self, viewport_w=None, viewport_h=None):
        self.info_calls += 1
        w = float(viewport_w or 420)
        h = float(viewport_h or 320)
        cw, ch = int(round(w * self.scale)), int(round(h * self.scale))
        self.img_path = os.path.join(self.tmp, 'frozen.png')
        _make_image(self.img_path, cw, ch)
        self.client = [0, 0, cw, ch]
        with open(self.img_path, 'rb') as fh:
            data = base64.b64encode(fh.read()).decode('ascii')
        return {'image': 'data:image/png;base64,' + data, 'client': list(self.client),
                'origin': [0, 0], 'size': [cw, ch], 'note_id': self.note_id}

    def capture_overlay_ready(self):
        self.ready += 1
        return True

    def capture_commit(self, action, rect=None, viewport=None):
        self.commits.append({'action': action, 'rect': rect, 'viewport': viewport})
        return {'ok': True, 'action': action}


class MiniBridge:
    def __init__(self):
        self.saves = []
        self.closes = 0

    def capture_mini_submit(self, text, keep_open=False):
        self.saves.append({'text': text, 'keep_open': bool(keep_open)})
        return {'ok': True, 'id': 'n-%d' % len(self.saves)}

    def capture_mini_close(self):
        self.closes += 1
        return True


def _drag(window, x0, y0, x1, y1, steps=3):
    """在覆盖窗里模拟一次拖拽（页面把监听挂在 document 上）。"""
    window.evaluate_js(
        "document.dispatchEvent(new PointerEvent('pointerdown',"
        "{clientX:%d, clientY:%d, button:0, bubbles:true}));" % (x0, y0))
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        y = y0 + (y1 - y0) * i / steps
        window.evaluate_js(
            "document.dispatchEvent(new PointerEvent('pointermove',"
            "{clientX:%d, clientY:%d, bubbles:true}));" % (x, y))
    window.evaluate_js(
        "document.dispatchEvent(new PointerEvent('pointerup',"
        "{clientX:%d, clientY:%d, button:0, bubbles:true}));" % (x1, y1))


def _overlay_factory(bridge, title='覆盖窗测试'):
    """建一个覆盖窗（**不**把窗口对象挂到桥接上，见 OverlayBridge 的说明）"""
    import webview

    def factory():
        return webview.create_window(title, RENDERER + '/capture-overlay.html',
                                     js_api=bridge, width=420, height=320, frameless=True)
    return factory


@pytest.mark.e2e
def test_overlay_selection_shows_toolbar_and_reports_css_pixels(tmp_path):
    """框选 → 尺寸标签是**物理**像素、工具条出现、"存为新笔记"回报 CSS 选区与视口。"""
    bridge = OverlayBridge(tmp_path)

    def actions(window, result):
        time.sleep(2.0)
        result['ready'] = bridge.ready
        result['info_calls'] = bridge.info_calls
        with Image.open(bridge.img_path) as im:
            result['img_size'] = im.size
        _drag(window, 30, 40, 150, 140)
        time.sleep(0.4)
        result['bar_visible'] = window.evaluate_js(
            "!document.getElementById('bar').classList.contains('hidden')")
        result['buttons'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.ov-btn')].map(b => b.getAttribute('data-act')))")
        result['labels'] = window.evaluate_js(
            "document.querySelector('[data-act=\"insert\"]').textContent.trim()")
        result['size_label'] = window.evaluate_js(
            "document.getElementById('sel-size').textContent")
        result['has_svg'] = window.evaluate_js(
            "document.querySelectorAll('.ov-btn svg').length")
        window.evaluate_js("document.querySelector('[data-act=\"note\"]').click();")
        time.sleep(0.6)
        result['commit'] = bridge.commits[-1] if bridge.commits else None

    r = _run(_overlay_factory(bridge), actions)
    assert 'error' not in r, r.get('error')
    assert r['ready'] == 1, '页面画完冻屏图后必须让 Python 显示窗口（否则白闪）'
    assert r['img_size'][0] == bridge.client[2] and r['img_size'][1] == bridge.client[3]
    assert r['bar_visible'] is True
    assert json.loads(r['buttons']) == ['insert', 'note', 'ocr', 'clipboard', 'cancel']
    assert r['has_svg'] == 5, '工具条图标必须来自统一图标库（不是 emoji/文字）'
    assert '插入当前笔记' in r['labels']
    # 120 × 100 CSS × 2 倍缩放 = 240 × 200 物理像素
    assert r['size_label'].replace(' ', '') == '240×200'
    assert r['commit'] and r['commit']['action'] == 'note'
    assert r['commit']['rect'] == [30, 40, 120, 100], '上报的必须是 CSS 像素原样'
    vw, vh = r['commit']['viewport']
    assert vw > 0 and vh > 0


@pytest.mark.e2e
def test_overlay_magnifier_reads_the_pixel_under_cursor(tmp_path):
    """放大镜取到的颜色必须等于合成图对应位置的颜色（证明 CSS↔图片 的坐标约定一致）。"""
    bridge = OverlayBridge(tmp_path)

    def probe(x, y):
        # 取放大镜画布**正中**那一个像素：它对应光标底下那个源像素。
        # 不能取 64,64 —— 那是十字准星描边的位置，会被白色混掉（实测踩到）。
        return ("(function(){document.dispatchEvent(new PointerEvent('pointermove',"
                "{clientX:%d, clientY:%d, bubbles:true}));"
                "var m=document.getElementById('mag');"
                "var c=document.getElementById('mag-canvas');"
                "var g=c.getContext('2d');"
                "var d=g.getImageData(68,68,1,1).data.slice(0,3);"
                "return JSON.stringify({hex:document.getElementById('mag-hex').textContent,"
                "shown:!m.classList.contains('hidden'),px:[d[0],d[1],d[2]]});})()" % (x, y))

    def actions(window, result):
        time.sleep(2.0)
        w = window.evaluate_js('window.innerWidth')
        h = window.evaluate_js('window.innerHeight')
        # 左上红 / 右上绿 / 左下蓝（各取远离中线的点，避免踩到象限边界）
        result['topleft'] = json.loads(window.evaluate_js(probe(int(w * 0.15), int(h * 0.15))))
        result['topright'] = json.loads(window.evaluate_js(probe(int(w * 0.85), int(h * 0.2))))
        result['bottomleft'] = json.loads(window.evaluate_js(probe(int(w * 0.15), int(h * 0.85))))

    r = _run(_overlay_factory(bridge), actions)
    assert 'error' not in r, r.get('error')
    assert r['topleft']['shown'] is True
    assert r['topleft']['hex'] == '#FF0000' and tuple(r['topleft']['px']) == QUADRANTS['red']
    assert r['topright']['hex'] == '#00C800' and tuple(r['topright']['px']) == QUADRANTS['green']
    assert r['bottomleft']['hex'] == '#0000FF' and tuple(r['bottomleft']['px']) == QUADRANTS['blue']


@pytest.mark.e2e
def test_overlay_keyboard_shortcuts(tmp_path):
    """Esc 取消；有正在编辑的笔记时 Enter = 插入，没有时 Enter = 存为新笔记。"""
    bridge = OverlayBridge(tmp_path)

    def key(window, k):
        window.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',{key:%s,bubbles:true}));"
            % json.dumps(k))

    def actions(window, result):
        time.sleep(2.0)
        _drag(window, 20, 20, 120, 120)
        time.sleep(0.3)
        key(window, 'Escape')
        time.sleep(0.5)
        result['after_esc'] = list(bridge.commits)
        bridge.commits.clear()
        _drag(window, 20, 20, 120, 120)
        time.sleep(0.3)
        key(window, 'Enter')
        time.sleep(0.5)
        result['after_enter'] = list(bridge.commits)

    r = _run(_overlay_factory(bridge), actions)
    assert 'error' not in r, r.get('error')
    assert [c['action'] for c in r['after_esc']] == ['cancel']
    assert [c['action'] for c in r['after_enter']] == ['insert'], '有笔记时 Enter 应该是插入'

    # 没有正在编辑的笔记时：插入按钮禁用、Enter 退回"存为新笔记"
    bridge2 = OverlayBridge(tmp_path, note_id='')

    def actions2(window, result):
        time.sleep(2.0)
        result['insert_disabled'] = window.evaluate_js(
            "document.querySelector('[data-act=\"insert\"]').disabled")
        _drag(window, 20, 20, 120, 120)
        time.sleep(0.3)
        key(window, 'Enter')
        time.sleep(0.5)
        result['commit'] = bridge2.commits[-1] if bridge2.commits else None

    r2 = _run(_overlay_factory(bridge2, '覆盖窗测试2'), actions2)
    assert 'error' not in r2, r2.get('error')
    assert r2['insert_disabled'] is True
    assert r2['commit']['action'] == 'note'


# ---------------- 迷你捕获窗 ----------------

@pytest.mark.e2e
def test_mini_window_submit_shortcuts():
    """Enter 保存并关窗；Ctrl+Enter 保存并留着（输入框清空、状态提示）；Esc 不保存只关窗。"""
    bridge = MiniBridge()

    def factory():
        import webview
        return webview.create_window('迷你窗测试', RENDERER + '/capture-mini.html',
                                     js_api=bridge, width=520, height=170, frameless=True)

    def type_text(window, text):
        window.evaluate_js(
            "(function(){var i=document.getElementById('mini-input');i.value=%s;"
            "i.dispatchEvent(new Event('input',{bubbles:true}));})()" % json.dumps(text))

    def key(window, k, ctrl=False):
        payload = {'key': k, 'bubbles': True, 'ctrlKey': bool(ctrl)}
        window.evaluate_js(
            "(function(){var i=document.getElementById('mini-input');"
            "i.dispatchEvent(new KeyboardEvent('keydown',%s));})()" % json.dumps(payload))

    def actions(window, result):
        time.sleep(1.6)
        result['focused'] = window.evaluate_js(
            "document.activeElement === document.getElementById('mini-input')")
        result['has_close_svg'] = window.evaluate_js(
            "document.querySelectorAll('#mini-close svg').length")
        # Ctrl+Enter：保存但留着
        type_text(window, '第一条想法\n还有一行')
        key(window, 'Enter', ctrl=True)
        time.sleep(0.6)
        result['after_ctrl_enter'] = list(bridge.saves)
        result['input_cleared'] = window.evaluate_js(
            "document.getElementById('mini-input').value")
        result['status'] = window.evaluate_js(
            "document.getElementById('mini-status').textContent")
        # Esc：只关窗
        key(window, 'Escape')
        time.sleep(0.4)
        result['closes'] = bridge.closes
        # 空内容 + Enter：不该产生第二条
        type_text(window, '   ')
        key(window, 'Enter')
        time.sleep(0.4)
        result['saves'] = list(bridge.saves)

    r = _run(factory, actions)
    assert 'error' not in r, r.get('error')
    assert r['focused'] is True, '窗口一出来光标就该在输入框里'
    assert r['has_close_svg'] == 1
    assert r['after_ctrl_enter'] == [{'text': '第一条想法\n还有一行', 'keep_open': True}]
    assert r['input_cleared'] == ''
    assert '收件箱' in r['status']
    assert r['closes'] >= 1
    assert len(r['saves']) == 1, '空白内容按 Enter 不该建笔记'


@pytest.mark.e2e
def test_mini_window_plain_enter_saves_and_closes():
    bridge = MiniBridge()

    def factory():
        import webview
        return webview.create_window('迷你窗测试', RENDERER + '/capture-mini.html',
                                     js_api=bridge, width=520, height=170, frameless=True)

    def actions(window, result):
        time.sleep(1.6)
        window.evaluate_js(
            "(function(){var i=document.getElementById('mini-input');i.value='买牛奶';"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()")
        time.sleep(0.6)
        result['saves'] = list(bridge.saves)
        result['closes'] = bridge.closes

    r = _run(factory, actions)
    assert 'error' not in r, r.get('error')
    assert r['saves'] == [{'text': '买牛奶', 'keep_open': False}]
    # 关窗是 **Python** 的事（`_mini_submit` 末尾 destroy），页面自己不关：保存失败时
    # 页面必须留在屏幕上让用户重试。假桥接不模拟销毁，所以这里就是 0 次。
    assert r['closes'] == 0
