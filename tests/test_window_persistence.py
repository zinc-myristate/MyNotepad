# -*- coding: utf-8 -*-
"""窗口几何持久化（记住大小/位置）与「关窗驻留托盘」的关闭语义。

这两件事都直接决定用户会不会丢数据/丢提醒，所以把边界都锁死：
  · 保存的几何必须经过与默认值同一条钳制路径（拔掉显示器后旧坐标可能在屏外）；
  · prefer 位置只在能**完整**落在工作区内时才采用，否则退回居中；
  · 没有保存过 / 存的是坏数据 → 行为必须与改造前完全一致（用默认尺寸居中）；
  · 最小化 / 最大化 / 隐藏时读到的几何绝不能落库，已经写坏的用户库读取时必须自愈；
  · 关闭处理器在 tray=None 时行为不变（测试与未启用托盘时必须能真正关掉窗口）。
"""
import json
import os
import threading as _threading
import time
from types import SimpleNamespace

import pytest
from conftest import load_app_partial

# 在**任何 fixture 运行之前**把真类与真方法记下来：conftest 加载 app.pyw 时会临时拦一层
# Thread.start，这个测试用来确认它拦完必须还原、且从没动过类本身。
_REAL_THREAD_CLASS = _threading.Thread
_REAL_THREAD_START = _threading.Thread.start


def _clamp(ns, w, h, work_area, prefer=None):
    return ns['clamp_window_geometry'](w, h, work_area, prefer=prefer)


WORK = (0, 0, 1536, 912)   # 本仓库故障场景用过的 200% 缩放逻辑工作区


class TestPreferPosition:
    """prefer=(x, y)：恢复上次位置，但必须完整可见"""

    def test_uses_prefer_when_fully_inside(self, app_ns):
        w, h, x, y = _clamp(app_ns, 900, 700, WORK, prefer=(100, 80))
        assert (w, h, x, y) == (900, 700, 100, 80)

    def test_falls_back_to_center_when_partly_offscreen(self, app_ns):
        # 右/下越界：位置被放弃，回到居中
        w, h, x, y = _clamp(app_ns, 900, 700, WORK, prefer=(1000, 80))
        assert x == (1536 - 900) // 2, '越界的位置不能采用'
        assert y == 80 or y == (912 - 700) // 2   # x 越界即整体退回居中，y 也应按居中算
        assert (x, y) == ((1536 - 900) // 2, (912 - 700) // 2)

    def test_falls_back_when_negative(self, app_ns):
        # 显示器被拔掉后常见的负坐标（窗口原本在左侧副屏）
        _, _, x, _ = _clamp(app_ns, 900, 700, WORK, prefer=(-400, 100))
        assert x == (1536 - 900) // 2

    def test_prefer_none_behaves_like_before(self, app_ns):
        assert _clamp(app_ns, 900, 700, WORK, prefer=None) == _clamp(app_ns, 900, 700, WORK)
        assert _clamp(app_ns, 900, 700, WORK, prefer=(None, None)) == _clamp(app_ns, 900, 700, WORK)

    def test_oversized_saved_size_is_still_shrunk(self, app_ns):
        """存的是大尺寸 + 小工作区：尺寸照旧被钳制（不能因为"恢复上次"就把窗口撑出屏）"""
        w, h, x, y = _clamp(app_ns, 3000, 2000, WORK, prefer=(0, 0))
        assert w <= 1536 and h <= 912 - 48
        assert x >= 0 and y >= 0 and x + w <= 1536 and y + h <= 912

    def test_prefer_used_even_in_tiny_work_area_when_fits(self, app_ns):
        # 极小屏：窗口撑满（允许溢出）时位置只能是 0,0 附近，不应崩
        w, h, x, y = _clamp(app_ns, 900, 600, (0, 0, 800, 420), prefer=(0, 0))
        assert isinstance(x, int) and isinstance(y, int)


class TestSavedGeometryRoundtrip:
    """_load_saved_geometry / _save_window_geometry 与后端设置的往返"""

    def test_none_backend_is_safe(self, app_ns):
        assert app_ns['_load_saved_geometry'](None) is None

    def test_missing_key_returns_none(self, app_ns, api):
        assert app_ns['_load_saved_geometry'](api) is None, '从没存过就必须当没有'

    def test_roundtrip(self, app_ns, api):
        api.settings_set('window_geometry', '{"w": 1000, "h": 700, "x": 120, "y": 60}')
        assert app_ns['_load_saved_geometry'](api) == (1000, 700, 120, 60)

    @pytest.mark.parametrize('raw', [
        'not json', '{}', '{"w": 0, "h": 700}', '{"w": -5, "h": 700}',
        '{"w": "abc", "h": 700}', '[]',
    ])
    def test_bad_data_returns_none(self, app_ns, api, raw):
        api.settings_set('window_geometry', raw)
        assert app_ns['_load_saved_geometry'](api) is None

    def test_legacy_keys_are_ignored(self, app_ns, api):
        """早期版本留下的 window_width/height/x/y 无任何代码读取，不能拿来恢复窗口"""
        for k, v in (('window_width', '900'), ('window_height', '1073'),
                     ('window_x', '-6'), ('window_y', '0')):
            api.settings_set(k, v)
        assert app_ns['_load_saved_geometry'](api) is None

    def test_resolve_uses_saved_then_clamps(self, app_ns, api):
        """保存的尺寸优先于默认尺寸，但两者都要过工作区钳制。

        ⚠️ 不能硬编码 1000×700：CI 运行器的虚拟屏幕比开发机小得多，高度会被钳成
        `工作区高 - reserve`（实测 672 而不是 700）。这里改成断言**钳制规则本身**——
        结果必须等于「拿保存值去调同一个纯函数」的结果；只有工作区真装得下时才要求原样。
        """
        api.settings_set('window_geometry', '{"w": 1000, "h": 700, "x": 40, "y": 30}')
        geo = app_ns['_resolve_window_geometry'](api)
        wa = app_ns['_get_work_area']()
        exp_w, exp_h, _, _ = app_ns['clamp_window_geometry'](1000, 700, wa)
        assert (geo['w'], geo['h']) == (exp_w, exp_h), '保存的尺寸必须走钳制，而不是被忽略'
        if wa and wa[2] >= 1000 and wa[3] - app_ns['_WORKAREA_RESERVE'] >= 700:
            assert (geo['w'], geo['h']) == (1000, 700), '工作区装得下时必须原样用保存值'
        if wa:
            assert geo['x'] is not None

    def test_resolve_without_saved_still_works(self, app_ns, api):
        geo = app_ns['_resolve_window_geometry'](api)
        assert geo['w'] > 0 and geo['h'] > 0


MINIMIZED = {'w': 157, 'h': 25, 'x': -16000, 'y': -16000}   # 用户库实测（3072×1920 @ 200%）


class TestImplausibleGeometryIsIgnored:
    """读取端：写坏的值当没存过，已经中招的用户库启动时自愈"""

    @pytest.mark.parametrize('bad', [
        MINIMIZED,
        {'w': 160, 'h': 28, 'x': -32000, 'y': -32000},   # 100% 缩放下的同一个状态
        {'w': 500, 'h': 700, 'x': 10, 'y': 10},          # 只有宽度低于最小窗口
        {'w': 1000, 'h': 300, 'x': 10, 'y': 10},         # 只有高度低于最小窗口
        {'w': 1000, 'h': 700, 'x': -16000, 'y': 10},     # 尺寸正常，x 远在屏外
        {'w': 1000, 'h': 700, 'x': 10, 'y': 20000},      # 尺寸正常，y 远在屏外
    ])
    def test_treated_as_unsaved(self, app_ns, api, bad):
        api.settings_set('window_geometry', json.dumps(bad))
        assert app_ns['_load_saved_geometry'](api) is None

    def test_poisoned_library_opens_at_default_size(self, app_ns, api, monkeypatch):
        """修复前这里开成 900×600：尺寸被钳到最小值，用户看到的就是"窗口大小记不住" """
        monkeypatch.setitem(app_ns, '_get_work_area', lambda: WORK)
        api.settings_set('window_geometry', json.dumps(MINIMIZED))
        assert app_ns['_resolve_window_geometry'](api) == {'w': 1200, 'h': 800, 'x': 168, 'y': 56}

    @pytest.mark.parametrize('x,y', [(-1920, 100), (3000, 200), (11000, 0)])
    def test_other_monitor_coordinates_keep_the_size(self, app_ns, api, monkeypatch, x, y):
        """副屏或拔掉显示器留下的坐标不是坏数据：尺寸要留下，只有位置退回居中"""
        monkeypatch.setitem(app_ns, '_get_work_area', lambda: WORK)
        api.settings_set('window_geometry', json.dumps({'w': 1000, 'h': 700, 'x': x, 'y': y}))
        assert app_ns['_load_saved_geometry'](api) == (1000, 700, x, y)
        assert app_ns['_resolve_window_geometry'](api) == {
            'w': 1000, 'h': 700, 'x': (1536 - 1000) // 2, 'y': (912 - 700) // 2}

    def test_position_is_optional(self, app_ns, api):
        api.settings_set('window_geometry', '{"w": 1000, "h": 700}')
        assert app_ns['_load_saved_geometry'](api) == (1000, 700, None, None)

    @pytest.mark.parametrize('dpi', [96, 120, 125, 144, 168, 192, 216, 240, 288])
    def test_smallest_real_window_is_valid_at_every_scale(self, app_ns, api, dpi):
        """贴着最小尺寸的窗口不能被当成坏数据。

        这里按 pywebview 的取整方式算：125 dpi（130%）下物理下限是 1171×781，读回来是 899×599。
        """
        scale = dpi / 96
        w = int(int(app_ns['MIN_WIN_W'] * scale) / scale)
        h = int(int(app_ns['MIN_WIN_H'] * scale) / scale)
        api.settings_set('window_geometry', json.dumps({'w': w, 'h': h, 'x': 10, 'y': 10}))
        assert app_ns['_load_saved_geometry'](api) == (w, h, 10, 10)


class _FakeForm:
    """pywebview 的 `window.native`：只用到 Handle.ToInt64()"""
    Handle = SimpleNamespace(ToInt64=lambda: 4242)


@pytest.fixture
def win32(monkeypatch):
    """假的 IsIconic / IsZoomed / IsWindowVisible；真窗口那条路由本文件的 e2e 覆盖"""
    import ctypes
    state = SimpleNamespace(iconic=False, zoomed=False, visible=True, asked=[])

    def is_iconic(hwnd):
        state.asked.append(hwnd)
        return state.iconic

    monkeypatch.setattr(ctypes.windll.user32, 'IsIconic', is_iconic)
    monkeypatch.setattr(ctypes.windll.user32, 'IsZoomed', lambda hwnd: state.zoomed)
    monkeypatch.setattr(ctypes.windll.user32, 'IsWindowVisible', lambda hwnd: state.visible)
    return state


MAXIMIZED = (1549, 925, -6, -6)      # 用户机器上实测（工作区 1536×912 @ 200%）


class TestSaveOnlyFromNormalWindow:
    """保存端：最小化 / 最大化 / 隐藏时读到的不是用户调出来的窗口，不能顶掉上一次的有效值"""

    LAST_GOOD = '{"w": 1000, "h": 700, "x": 120, "y": 60}'

    class Window:
        native = _FakeForm()

        def __init__(self, w=1100, h=750, x=50, y=40):
            self.width, self.height, self.x, self.y = w, h, x, y

    def test_normal_window_still_saves(self, app_ns, api, win32):
        api.settings_set('window_geometry', self.LAST_GOOD)
        app_ns['_save_window_geometry'](api, self.Window())
        assert json.loads(api.settings_get('window_geometry')) == {
            'w': 1100, 'h': 750, 'x': 50, 'y': 40}

    def test_minimized_keeps_last_good_value(self, app_ns, api, win32):
        """读数故意写成合法值：这条只验状态判断，不让 _geometry_is_plausible 替它挡"""
        api.settings_set('window_geometry', self.LAST_GOOD)
        win32.iconic = True
        app_ns['_save_window_geometry'](api, self.Window())
        assert api.settings_get('window_geometry') == self.LAST_GOOD
        assert win32.asked == [4242], '必须问这个窗口自己的句柄，不能按标题找'

    def test_maximized_keeps_last_good_value(self, app_ns, api, win32):
        """最大化的读数完全合法，能挡住它的只有状态判断"""
        api.settings_set('window_geometry', self.LAST_GOOD)
        win32.zoomed = True
        app_ns['_save_window_geometry'](api, self.Window(*MAXIMIZED))
        assert api.settings_get('window_geometry') == self.LAST_GOOD

    def test_hidden_keeps_last_good_value(self, app_ns, api, win32):
        api.settings_set('window_geometry', self.LAST_GOOD)
        win32.visible = False
        app_ns['_save_window_geometry'](api, self.Window())
        assert api.settings_get('window_geometry') == self.LAST_GOOD

    def test_minimized_numbers_are_refused_without_a_win32_answer(self, app_ns, api):
        """取不到句柄、判断不了窗口状态时，也不能把最小化的读数写进去"""
        api.settings_set('window_geometry', self.LAST_GOOD)
        win = SimpleNamespace(width=157, height=25, x=-16000, y=-16000)
        app_ns['_save_window_geometry'](api, win)
        assert api.settings_get('window_geometry') == self.LAST_GOOD

    def test_minimizing_between_check_and_read_is_refused(self, app_ns, api, win32):
        """状态判断说"显示着"、读数却已经是最小化的：用户恰好在这一刻点了最小化"""
        api.settings_set('window_geometry', self.LAST_GOOD)
        app_ns['_save_window_geometry'](api, self.Window(**MINIMIZED))
        assert api.settings_get('window_geometry') == self.LAST_GOOD


class TestClosingHandlerWithTray:
    """「关窗驻留托盘」的关闭语义——最不能出错的一处：绝不能变成"点了关闭关不掉" """

    class FakeWindow:
        def __init__(self, hide_raises=False):
            self.calls = []
            self._hide_raises = hide_raises

        # 关窗兜底会先取未存快照（无快照则返回 None）
        def evaluate_js(self, js):
            self.calls.append('evaluate_js')
            return None

        def hide(self):
            if self._hide_raises:
                raise RuntimeError('hide 失败')
            self.calls.append('hide')

        def destroy(self):
            self.calls.append('destroy')

        # 保存几何用
        width, height, x, y = 900, 700, 10, 10

    class FakeTray:
        def __init__(self):
            self.hinted = 0

        def notify_hidden_once(self):
            self.hinted += 1

    @staticmethod
    def _close(ns, api, win, tray=None, quitting=False, timeout=3.0):
        handler = ns['make_closing_handler'](win, api, tray=tray,
                                             is_quitting=lambda: quitting)
        assert handler() is False, '第一次 closing 必须取消关闭，等 flush 完成'
        deadline = time.time() + timeout
        while time.time() < deadline:
            if 'hide' in win.calls or 'destroy' in win.calls:
                break
            time.sleep(0.05)
        return win.calls

    def test_no_tray_destroys_window_as_before(self, app_ns, api):
        win = self.FakeWindow()
        calls = self._close(app_ns, api, win)
        assert 'destroy' in calls and 'hide' not in calls, \
            '未启用托盘时行为必须与改造前一致（测试全靠这条能真正关掉窗口）'

    def test_tray_hides_instead_of_destroy(self, app_ns, api):
        win, tray = self.FakeWindow(), self.FakeTray()
        calls = self._close(app_ns, api, win, tray=tray)
        assert 'hide' in calls, '启用托盘时关窗应隐藏'
        assert 'destroy' not in calls, '隐藏后不能销毁窗口（否则提醒又没了）'
        assert tray.hinted == 1, '第一次隐藏应提示一次"已最小化到托盘"'

    def test_tray_quitting_真关(self, app_ns, api):
        win, tray = self.FakeWindow(), self.FakeTray()
        calls = self._close(app_ns, api, win, tray=tray, quitting=True)
        assert 'destroy' in calls and 'hide' not in calls, '用户从托盘退出时必须真的关闭'
        assert tray.hinted == 0

    def test_hide_failure_falls_back_to_destroy(self, app_ns, api):
        """隐藏失败必须退化为直接关闭：绝不能出现"点关闭却关不掉" """
        win, tray = self.FakeWindow(hide_raises=True), self.FakeTray()
        calls = self._close(app_ns, api, win, tray=tray)
        assert 'destroy' in calls, 'hide 抛异常时必须仍然把窗口关掉'

    def test_can_close_twice_when_tray_enabled(self, app_ns, api):
        """隐藏后状态要回到 idle：否则第二次点关闭就再也没反应"""
        win, tray = self.FakeWindow(), self.FakeTray()
        handler = app_ns['make_closing_handler'](win, api, tray=tray, is_quitting=lambda: False)
        assert handler() is False
        deadline = time.time() + 3
        while time.time() < deadline and 'hide' not in win.calls:
            time.sleep(0.05)
        assert win.calls.count('hide') == 1
        # 第二次关闭：应当还能再隐藏一次，而不是被 phase='done' 卡成不再响应
        assert handler() is False, '第二次关闭必须仍被拦截并重新走 flush'
        deadline = time.time() + 3
        while time.time() < deadline and win.calls.count('hide') < 2:
            time.sleep(0.05)
        assert win.calls.count('hide') == 2, '第二次关闭应同样隐藏（状态已回到 idle）'

    @pytest.mark.parametrize('state,reading', [('iconic', tuple(MINIMIZED.values())),
                                               ('zoomed', MAXIMIZED)])
    @pytest.mark.parametrize('quitting,last_call', [(False, 'hide'), (True, 'destroy')])
    def test_closing_keeps_saved_geometry(self, app_ns, api, win32, state, reading,
                                          quitting, last_call):
        """用户库就是这么写坏的：最小化后关窗，或最小化后从托盘退出。最大化同理"""
        saved = '{"w": 1000, "h": 700, "x": 120, "y": 60}'
        api.settings_set('window_geometry', saved)
        setattr(win32, state, True)
        win, tray = self.FakeWindow(), self.FakeTray()
        win.native = _FakeForm()
        win.width, win.height, win.x, win.y = reading
        calls = self._close(app_ns, api, win, tray=tray, quitting=quitting)
        assert last_call in calls
        assert api.settings_get('window_geometry') == saved


def _until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.05)
    return predicate()


@pytest.mark.e2e
@pytest.mark.parametrize('closing_state', ['minimize', 'maximize'])
def test_real_window_saves_geometry_only_when_normal(tmp_path, monkeypatch, closing_state):
    """真窗口走一遍：最小化、隐藏、最大化，再带着 closing_state 真的关掉，设置里始终是用户的窗口。

    单测里的窗口状态是假的。这里钉住只有真窗口才能证明的三件事：Windows 确实报那些几何
    （否则上面都在防一个不存在的问题）、`win.native.Handle` 在非 UI 线程取得到、
    WM_CLOSE 这条真实路径不会把坏值写回去。
    """
    import ctypes
    import sqlite3

    import webview
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    user32 = ctypes.windll.user32
    last_good = '{"w": 1000, "h": 700, "x": 120, "y": 60}'
    result = {}
    window = webview.create_window(
        '窗口几何回归', html='<p>geometry</p>', width=980, height=680, x=20, y=20,
        min_size=(ns['MIN_WIN_W'], ns['MIN_WIN_H']))
    window.events.closing += ns['make_closing_handler'](window, backend.api)

    def save_and_read():
        ns['_save_window_geometry'](backend.api, window)
        return backend.api.settings_get('window_geometry')

    def runner():
        try:
            assert window.events.shown.wait(15), '窗口没显示出来'
            hwnd = window.native.Handle.ToInt64()
            result['normal'] = json.loads(save_and_read())
            result['reported'] = (window.width, window.height, window.x, window.y)

            backend.api.settings_set('window_geometry', last_good)
            window.minimize()
            assert _until(lambda: user32.IsIconic(hwnd)), '窗口没进入最小化'
            result['minimized_reading'] = (window.width, window.height, window.x, window.y)
            result['after_minimize'] = save_and_read()

            window.restore()
            assert _until(lambda: not user32.IsIconic(hwnd)), '窗口没还原'
            window.hide()
            assert _until(lambda: not user32.IsWindowVisible(hwnd)), '窗口没隐藏'
            result['after_hide'] = save_and_read()

            window.show()
            window.maximize()
            assert _until(lambda: user32.IsZoomed(hwnd)), '窗口没进入最大化'
            result['maximized_reading'] = (window.width, window.height, window.x, window.y)
            result['after_maximize'] = save_and_read()

            window.restore()
            assert _until(lambda: not user32.IsZoomed(hwnd)), '窗口没从最大化还原'
            getattr(window, closing_state)()
            assert _until(lambda: user32.IsIconic(hwnd) or user32.IsZoomed(hwnd)), \
                '窗口没进入 %s 状态' % closing_state
            user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE：走 make_closing_handler 真实路径
        except Exception as exc:                      # noqa: BLE001
            result['error'] = repr(exc)
            try:
                window.destroy()
            except Exception:                         # noqa: BLE001
                pass

    _threading.Thread(target=runner, daemon=True).start()
    webview.start()

    assert 'error' not in result, result['error']
    assert (result['normal']['w'], result['normal']['h']) == result['reported'][:2]
    assert (result['normal']['x'], result['normal']['y']) == result['reported'][2:]
    assert result['minimized_reading'][0] < ns['MIN_WIN_W'], \
        'Windows 不再报最小化的假几何了，这条测试的前提要重看：%r' % (result['minimized_reading'],)
    assert result['maximized_reading'][0] > ns['MIN_WIN_W'], \
        '最大化的读数应当是合法尺寸，否则这条测试证明不了状态判断：%r' % (result['maximized_reading'],)
    assert result['after_minimize'] == last_good
    assert result['after_hide'] == last_good
    assert result['after_maximize'] == last_good
    # 关窗时 checkpoint_and_close 已经关了后端连接，只能直接读库文件
    con = sqlite3.connect(os.path.join(str(tmp_path), 'notes.db'))
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'window_geometry'").fetchone()
    finally:
        con.close()
    assert row[0] == last_good


def test_loading_app_partial_leaves_threading_intact(app_ns):
    """加载 app.pyw 前半段**不能**动 `threading.Thread` 这个类本身。

    ⚠️ 踩过的坑：conftest 为了别启动后台维护线程，最早是把一个"start() 是空操作"的假类
    挂到 `threading.Thread` 上。可 `desktop.GlobalHotkey` / `desktop.ReminderWatcher` 都是
    `class X(threading.Thread)` —— 基类在 **import 时**求值，而这个 import 正落在加载窗口里，
    于是它们永久继承假基类：`GlobalHotkey.start()` 成了空操作，`join()` 抛
    "cannot join thread before it is started"。全量跑时 desktop 早被导入过、掩盖了它，
    单跑本文件才暴露。拦 `Thread.start` 方法（而不是换类）即可，这条测试把它钉住。
    """
    import desktop
    assert _threading.Thread is _REAL_THREAD_CLASS, 'threading.Thread 被换掉了'
    assert _threading.Thread.start is _REAL_THREAD_START, 'Thread.start 没还原'
    assert desktop.GlobalHotkey.__mro__[1] is _REAL_THREAD_CLASS, 'GlobalHotkey 的基类被污染'
    assert desktop.ReminderWatcher.__mro__[1] is _REAL_THREAD_CLASS, 'ReminderWatcher 的基类被污染'


class TestGlobalHotkey:
    """全局快速记录热键（desktop.GlobalHotkey）。

    真机注册在测试里不可靠（可能被占用、也没有桌面会话），所以这里只锁**接口契约**：
    未启动时 started=False 且不抛、stop() 幂等；真实注册留给打包后的手工验证。
    """

    def test_starts_unstarted_and_stop_is_safe(self):
        import desktop
        calls = []
        hk = desktop.GlobalHotkey(lambda: calls.append(1))
        assert hk.started is False
        hk.stop()                       # 还没启动就 stop 不能抛
        assert calls == []

    def test_callback_is_not_called_before_start(self):
        import desktop
        hk = desktop.GlobalHotkey(lambda: None)
        assert hk._callback is not None and hk.started is False

    def test_registration_conflict_reports_error(self, monkeypatch):
        """注册失败（被占用）时必须 started=False 并把原因写进 error，而不是静默成功"""
        import ctypes

        import desktop
        monkeypatch.setattr(ctypes.windll.user32, 'RegisterHotKey',
                            lambda *a: 0, raising=False)
        hk = desktop.GlobalHotkey(lambda: None)
        hk.start()
        hk.join(timeout=3)
        assert hk.started is False
        assert hk.error, '失败必须留下原因（界面据此提示用户）'

    def test_hotkey_default_is_off(self):
        """默认不抢系统热键：设置项缺省必须是关（'0'）"""
        import os

        from conftest import PROJECT_ROOT
        src = open(os.path.join(PROJECT_ROOT, 'app.pyw'), encoding='utf-8').read()
        assert "HOTKEY_KEY = 'quick_hotkey'" in src
        assert "settings_get(HOTKEY_KEY) or '0'" in src, '默认值必须是关'


class TestTrayKey:
    def test_tray_key_and_default(self):
        """托盘开关的键名与默认值（默认必须为开，否则提醒默认失效）"""
        import os

        from conftest import PROJECT_ROOT
        src = open(os.path.join(PROJECT_ROOT, 'app.pyw'), encoding='utf-8').read()
        assert "TRAY_KEY = 'tray_enabled'" in src
        assert "settings_get(TRAY_KEY) or '1'" in src, '默认值必须是开启（未设置过视为开）'


class TestDesktopIntegration:
    """desktop.py：开机自启 + 托盘 + 提醒守护（都不真碰系统）"""

    def test_autostart_not_supported_in_test_env(self, api):
        import desktop
        assert desktop.autostart_supported() is False, '测试环境绝不能支持写注册表'
        assert desktop.autostart_enabled() is False
        # 关键：请求开启也必须返回 False 且真的不动注册表
        assert desktop.set_autostart(True) is False

    def test_registry_untouched_by_test_run(self, api):
        """跑完上面的 set_autostart(True)，注册表里不应出现我们的项"""
        import winreg

        import desktop
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, desktop._RUN_KEY) as k:
                try:
                    winreg.QueryValueEx(k, desktop._RUN_VALUE)
                    has_value = True
                except FileNotFoundError:
                    has_value = False
        except FileNotFoundError:
            has_value = False
        # 用户可能本来就设置过开机自启（打包版），所以只断言"不是测试刚写进去的"
        if has_value:
            import sys
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, desktop._RUN_KEY) as k:
                val, _ = winreg.QueryValueEx(k, desktop._RUN_VALUE)
            assert 'python' not in str(val).lower() or sys.executable not in str(val), \
                '测试不应把开发态 python 路径写进开机自启'

    def test_tray_image_fallback(self):
        """图标文件不存在时必须给出兜底图标，而不是直接失败"""
        import desktop
        t = desktop.Tray(r'D:\definitely\missing\icon.ico', lambda: None, lambda: None)
        img = t._load_image()
        assert img is not None and img.size[0] > 0

    def test_notify_without_icon_is_safe(self):
        import desktop
        t = desktop.Tray(None, lambda: None, lambda: None)
        t.notify('标题', '内容')       # 不抛
        t.stop()                       # 未启动也能安全停止

    def test_reminder_watcher_visible_delegates_to_frontend(self):
        """窗口可见时不弹气泡（前端 Toast 负责），并清掉去重记录"""
        import desktop

        class FakeTray:
            def __init__(self): self.calls = []
            def notify(self, title, msg): self.calls.append((title, msg))

        class FakeBackend:
            def __init__(self): self.due = [{'id': 'r1', 'content': '开会', 'remind_at': '2026-09-21 09:00:00'}]
            def reminder_check(self): return list(self.due)

        tray, be = FakeTray(), FakeBackend()
        w = desktop.ReminderWatcher(be, tray, lambda: True)
        assert w._tick() == []
        assert tray.calls == []
        w2 = desktop.ReminderWatcher(be, tray, lambda: False)
        assert w2._tick() == ['r1']
        assert len(tray.calls) == 1 and '开会' in tray.calls[0][1]

    def test_reminder_watcher_dedupes_and_forgets(self):
        """同一条提醒只弹一次；不再到期后遗忘，重新到期可以再弹（稍后提醒场景）"""
        import desktop

        class FakeTray:
            def __init__(self): self.calls = []
            def notify(self, title, msg): self.calls.append(msg)

        class FakeBackend:
            def __init__(self): self.due = []
            def reminder_check(self): return list(self.due)

        r = {'id': 'r1', 'content': '喝水', 'remind_at': '2026-09-21 10:00:00'}
        tray, be = FakeTray(), FakeBackend()
        w = desktop.ReminderWatcher(be, tray, lambda: False)
        be.due = [r]
        assert w._tick() == ['r1']
        assert w._tick() == [], '同一条提醒不能每 30 秒弹一次'
        assert len(tray.calls) == 1
        be.due = []                      # 用户点了「稍后提醒」→ 不再到期
        assert w._tick() == []
        be.due = [r]                     # 到点后又出现
        assert w._tick() == ['r1'], '遗忘后应能再次提醒'
        assert len(tray.calls) == 2

    def test_reminder_watcher_survives_backend_error(self):
        import desktop

        class Boom:
            def reminder_check(self): raise RuntimeError('db locked')

        class FakeTray:
            def notify(self, *a): raise AssertionError('不该被调用')

        w = desktop.ReminderWatcher(Boom(), FakeTray(), lambda: False)
        try:
            w._tick()
        except RuntimeError:
            pass  # 线程里由 run() 兜住；这里只要求不静默崩进程
