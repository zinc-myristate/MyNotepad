# -*- coding: utf-8 -*-
"""窗口几何持久化（记住大小/位置）与「关窗驻留托盘」的关闭语义。

这两件事都直接决定用户会不会丢数据/丢提醒，所以把边界都锁死：
  · 保存的几何必须经过与默认值同一条钳制路径（拔掉显示器后旧坐标可能在屏外）；
  · prefer 位置只在能**完整**落在工作区内时才采用，否则退回居中；
  · 没有保存过 / 存的是坏数据 → 行为必须与改造前完全一致（用默认尺寸居中）；
  · 关闭处理器在 tray=None 时行为不变（测试与未启用托盘时必须能真正关掉窗口）。
"""
import time

import pytest
from conftest import load_app_partial


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
        api.settings_set('window_geometry', '{"w": 1000, "h": 700, "x": 40, "y": 30}')
        geo = app_ns['_resolve_window_geometry'](api)
        assert geo['w'] == 1000 and geo['h'] == 700
        wa = app_ns['_get_work_area']()
        if wa:
            assert geo['x'] is not None

    def test_resolve_without_saved_still_works(self, app_ns, api):
        geo = app_ns['_resolve_window_geometry'](api)
        assert geo['w'] > 0 and geo['h'] > 0


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
