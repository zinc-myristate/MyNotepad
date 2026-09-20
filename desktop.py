"""Windows 桌面集成：系统托盘常驻 + 开机自启 + 气泡通知。

为什么需要这一层：提醒功能原先只由前端的 `setInterval(checkReminders, 30s)` 触发，
**窗口一关就再也不提醒**（后端也没有任何常驻检查）。窗口关闭时改为隐藏到托盘后，
进程还活着、提醒线程还在跑，提醒才谈得上"可信"。

三个独立能力，按需组合：
  · Tray        —— 托盘图标 + 右键菜单（打开/退出）+ 气泡通知
  · autostart_* —— 开机自启（写 HKCU\\...\\Run，只有打包版才有意义）
  · ReminderWatcher —— 窗口隐藏时在后端轮询到期提醒并弹托盘气泡

设计约束：
  · pystray 的 win32 后端自己起消息循环（run_detached 在自己的线程里），与 pywebview
    的主窗口消息循环互不干扰。
  · 所有能力都必须"失败即降级"：托盘起不来不能让应用起不来。
  · 测试环境（MYNOTEPAD_DATA_DIR）不碰注册表，行为与单实例互斥量一致。
"""
import os
import sys
import threading

TRAY_TITLE = '我的记事本'
_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_RUN_VALUE = 'MyNotepad'


def _is_test_env():
    """每次调用时读环境变量，而不是 import 时定死——测试里 monkeypatch 设置得很晚"""
    return bool(os.environ.get('MYNOTEPAD_DATA_DIR'))


# ====== 开机自启 ======

def autostart_supported():
    """开发态（python app.pyw）注册表里写 python 路径没有意义，只支持打包版。
    测试环境一律不支持：绝不因为跑测试而改动用户真实注册表。"""
    return bool(getattr(sys, 'frozen', False)) and not _is_test_env()


def autostart_enabled():
    """当前是否已开机自启（以注册表为准，不另存一份状态）"""
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _RUN_VALUE)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(enabled):
    """写/删开机自启项，返回操作后的实际状态（失败返回当前状态，不抛）"""
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if enabled:
                # 路径加引号：exe 路径可能含空格，不加引号 Windows 会解析错
                winreg.SetValueEx(k, _RUN_VALUE, 0, winreg.REG_SZ, '"%s"' % sys.executable)
            else:
                try:
                    winreg.DeleteValue(k, _RUN_VALUE)
                except FileNotFoundError:
                    pass
    except Exception:
        pass
    return autostart_enabled()


# ====== 托盘图标 ======

class Tray:
    """系统托盘图标。on_open/on_quit 由调用方提供（app.pyw 负责窗口操作）。

    线程模型：pystray 的 run_detached() 自建消息循环线程；回调在**界面线程之外**执行，
    因此 on_open/on_quit 里调用的 pywebview 接口必须是线程安全的（pywebview 的
    window.show/hide/destroy 都会 marshal 到 UI 线程，安全）。
    """

    def __init__(self, icon_path, on_open, on_quit, on_toggle_autostart=None):
        self._icon = None
        self._icon_path = icon_path
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_toggle_autostart = on_toggle_autostart
        self._hinted = False

    def start(self):
        """启动托盘；失败返回 False（调用方继续以"无托盘"模式运行）"""
        try:
            import pystray
            from pystray import Menu, MenuItem
            image = self._load_image()
            if image is None:
                return False
            items = [MenuItem('打开我的记事本', self._open, default=True)]
            if self._on_toggle_autostart is not None and autostart_supported():
                items.append(MenuItem(
                    '开机自启', self._toggle_autostart,
                    checked=lambda item: autostart_enabled()))
            items.append(Menu.SEPARATOR)
            items.append(MenuItem('退出', self._quit))
            self._icon = pystray.Icon('MyNotepad', image, TRAY_TITLE, Menu(*items))
            self._icon.run_detached()
            return True
        except Exception:
            self._log('启动托盘失败')
            self._icon = None
            return False

    def _load_image(self):
        """优先用当前生效的图标（用户换过图标就用他换的那个）"""
        try:
            from PIL import Image
            if self._icon_path and os.path.exists(self._icon_path):
                img = Image.open(self._icon_path)
                return img.convert('RGBA')
        except Exception:
            pass
        # 兜底：画一个纯色方块，保证托盘至少有个图标而不是直接失败
        try:
            from PIL import Image, ImageDraw
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(125, 138, 110, 255))
            return img
        except Exception:
            return None

    def _open(self, icon=None, item=None):
        try:
            self._on_open()
        except Exception:
            self._log('托盘：打开窗口失败')

    def _quit(self, icon=None, item=None):
        try:
            self._on_quit()
        except Exception:
            self._log('托盘：退出失败')

    def _toggle_autostart(self, icon=None, item=None):
        try:
            self._on_toggle_autostart(not autostart_enabled())
        except Exception:
            self._log('托盘：切换开机自启失败')

    def notify(self, title, message):
        """弹气泡通知（窗口隐藏时给提醒用）。失败静默。"""
        try:
            if self._icon is not None:
                self._icon.notify(message, title)
        except Exception:
            pass

    def notify_hidden_once(self):
        """第一次「关窗变托盘」时提示一次：否则用户会以为软件没关掉（或没在运行）"""
        if self._hinted:
            return
        self._hinted = True
        self.notify(TRAY_TITLE, '已最小化到托盘，提醒继续生效；右键托盘图标可退出。')

    def refresh_menu(self):
        """开机自启状态变了之后刷新菜单勾选（pystray 读 checked 回调，一般无需手动）"""
        try:
            if self._icon is not None:
                self._icon.update_menu()
        except Exception:
            pass

    def stop(self):
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        self._icon = None

    @staticmethod
    def _log(msg):
        try:
            import applog
            applog.get_logger().exception(msg)
        except Exception:
            pass


# ====== 提醒守护：窗口隐藏时在后端触发 ======

class ReminderWatcher(threading.Thread):
    """窗口隐藏时的提醒兜底。

    为什么不在后端"总是"提醒：窗口可见时前端已经在弹 Toast（带完成/稍后按钮），
    后端再弹一次就是重复提醒。所以这里只在窗口隐藏时工作。

    去重：reminder_check() 对同一个到期项会反复返回（它只负责"查"，标记完成是用户动作），
    所以本线程记住已弹过的 id，并随着该提醒不再到期而自动遗忘（稍后提醒会重新进入到期集，
    于是能再次弹出）。
    """

    def __init__(self, backend, tray, is_window_visible, interval=30):
        super().__init__(daemon=True)
        self._backend = backend
        self._tray = tray
        self._is_visible = is_window_visible
        self._interval = interval
        self._stop = threading.Event()
        self._notified = set()

    def run(self):
        while not self._stop.wait(self._interval):
            try:
                self._tick()
            except Exception:
                continue

    def _tick(self):
        """跑一轮检查，返回本轮弹出的提醒 id 列表。

        单独抽出来是为了可单测：不必启线程、不必等 30 秒。
        窗口可见时交给前端 Toast（返回空）；窗口隐藏/最小化时才弹托盘气泡。
        """
        if self._is_visible():
            # 窗口可见：前端已经弹过 Toast，清掉去重记录即可
            self._notified = set()
            return []
        due = self._backend.reminder_check()
        if not due:
            self._notified = set()
            return []
        current, fired = set(), []
        for r in due:
            rid = r.get('id')
            current.add(rid)
            if rid in self._notified:
                continue
            self._notified.add(rid)
            fired.append(rid)
            title = r.get('content') or '提醒'
            when = (r.get('remind_at') or '')[11:16]
            self._tray.notify(TRAY_TITLE, '%s  %s' % (when, title))
        self._notified &= current      # 不再到期的（已完成/已延后）遗忘掉
        return fired

    def stop(self):
        self._stop.set()
