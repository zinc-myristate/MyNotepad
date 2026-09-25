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


# ====== 全局快速记录热键 ======

class GlobalHotkey(threading.Thread):
    """注册一个**系统级**热键（任意程序前台时都能触发），默认 Ctrl+Alt+N。

    为什么要自己开线程跑消息循环：`RegisterHotKey(hwnd=None, ...)` 把 WM_HOTKEY 投递到
    **调用它的那个线程**的消息队列，没有消息循环就永远收不到；而 pywebview 的主消息循环
    在别的线程里，借用不了。所以在线程内注册 + GetMessage 循环。

    失败（热键被别的程序占用、非 Windows）返回 started=False，调用方据此提示用户。
    """

    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
    VK_N = 0x4E
    VK_S = 0x53

    def __init__(self, callback, vk=VK_N, mods=MOD_CONTROL | MOD_ALT, hotkey_id=1):
        super().__init__(daemon=True)
        self._callback = callback
        self._vk = vk
        self._mods = mods | self.MOD_NOREPEAT
        # 每个热键一个 id：多个热键线程各自注册在同一进程里，id 撞了会互相顶掉
        self._id = int(hotkey_id)
        self._tid = None
        self.started = False
        self.error = ''

    def run(self):
        if not sys.platform.startswith('win'):
            self.error = '仅支持 Windows'
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(None, self._id, self._mods, self._vk):
                self.error = '热键已被其他程序占用'
                self._log('注册全局热键失败（可能被占用）')
                return
            self.started = True
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    try:
                        self._callback()
                    except Exception:
                        self._log('全局热键回调失败')
            user32.UnregisterHotKey(None, self._id)
        except Exception:
            self.error = '注册异常'
            self._log('全局热键线程异常')

    def stop(self):
        try:
            if self._tid:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
        except Exception:
            pass

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


# ====== 截图 / 剪贴板图片（第 10 轮：快速捕获）======
# 为什么整屏截图要这么费劲：WebView2 **不支持真正的透明窗口**（`transparent=True` 实测无效，
# 窗口仍是不透明的），所以"半透明蒙层 + 实时看到桌面"这条路走不通。改成
# 「先冻屏 → 覆盖窗显示这张冻屏图 → 在图上框选 → 按坐标从原图裁剪」：
# 用户看到的与最终裁到的像素完全一致，而且覆盖窗本身可以是不透明窗口。

# GetSystemMetrics 索引：虚拟屏幕（含所有显示器）的左上角与尺寸，**物理像素**
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN = 76, 77
_SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 78, 79


def virtual_screen_rect():
    """虚拟屏幕（所有显示器拼起来）的物理像素矩形 `(x, y, w, h)`；取不到返回 None。

    多显示器时左上角可能是负数（副屏在主屏左边），所以原点必须带着走——
    裁剪坐标是相对这张整屏图的，不能拿 (0,0) 当左上。
    """
    if not sys.platform.startswith('win'):
        return None
    try:
        import ctypes
        gsm = ctypes.windll.user32.GetSystemMetrics
        x, y = int(gsm(_SM_XVIRTUALSCREEN)), int(gsm(_SM_YVIRTUALSCREEN))
        w, h = int(gsm(_SM_CXVIRTUALSCREEN)), int(gsm(_SM_CYVIRTUALSCREEN))
        if w <= 0 or h <= 0:
            return None
        return (x, y, w, h)
    except Exception:
        return None


def grab_screen(path):
    """把整个虚拟屏幕抓成 PNG 存到 `path`，返回 `(w, h)`；失败返回 None。

    必须先让本应用窗口消失再抓（否则会把自己拍进去）——调用方负责先 hide + 等一小会儿。
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    try:
        img = ImageGrab.grab(all_screens=True)
        img.save(path, 'PNG')
        return img.size
    except Exception:
        try:
            import applog
            applog.get_logger().exception("抓屏失败")
        except Exception:
            pass
        return None


def map_selection_to_image(rect, viewport, client, origin, image_size):
    """纯函数：覆盖窗里的选区（CSS px）→ 图片里的像素矩形。

    为什么不能直接拿选区当像素：窗口坐标是**逻辑**像素（本机 200% 缩放，物理 = 逻辑 × 2），
    而冻屏图是物理像素。窗口客户区在屏幕上的物理矩形由 Win32 给出（GetClientRect +
    ClientToScreen），于是「1 CSS px = 客户区物理宽 / 视口宽」，这一个比例同时吃掉了
    DPI 缩放与边框补偿——比自己猜缩放系数可靠（spike 里窗口客户区比请求尺寸小了 13px，
    正是靠这个比例兜住的）。

    参数：
      rect       —— 选区 (x, y, w, h)，CSS px
      viewport   —— 覆盖窗视口 (w, h)，CSS px（JS 的 innerWidth/innerHeight）
      client     —— 覆盖窗客户区的物理矩形 (x, y, w, h)
      origin     —— 图片物理原点 (x, y)（虚拟屏幕左上角）
      image_size —— 图片物理尺寸 (w, h)
    返回 `(x, y, w, h)`（已裁进图内），参数不合法或选区太小则返回 None。
    """
    try:
        rx, ry, rw, rh = (float(v) for v in rect)
        vw, vh = (float(v) for v in viewport)
        cx, cy, cw, ch = (float(v) for v in client)
        ox, oy = (float(v) for v in origin)
        iw, ih = (float(v) for v in image_size)
    except (TypeError, ValueError):
        return None
    if min(vw, vh, cw, ch, iw, ih, rw, rh) <= 0:
        return None
    sx, sy = cw / vw, ch / vh
    x0 = cx + rx * sx - ox
    y0 = cy + ry * sy - oy
    x1 = x0 + rw * sx
    y1 = y0 + rh * sy
    x0, y0 = max(0.0, x0), max(0.0, y0)      # 选区可能拖出窗口/屏幕一点点
    x1, y1 = min(iw, x1), min(ih, y1)
    left, top = int(round(x0)), int(round(y0))
    right, bottom = int(round(x1)), int(round(y1))
    if right - left < 2 or bottom - top < 2:  # 小于 2×2 物理像素等于没选（误点）
        return None
    return (left, top, right - left, bottom - top)


def crop_png(src_path, box, dest_path):
    """按像素矩形裁剪 PNG（box 来自 map_selection_to_image），成功返回 True。"""
    try:
        from PIL import Image
        with Image.open(src_path) as im:
            im.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])).save(dest_path, 'PNG')
        return True
    except Exception:
        try:
            import applog
            applog.get_logger().exception("裁剪截图失败")
        except Exception:
            pass
        return False


def clipboard_image_to_file(png_path):
    """把剪贴板里的图片存成 PNG（给 OCR 用）。没有图片时返回 None。

    剪贴板里可能是三种东西（`ImageGrab.grabclipboard` 的原样返回值）：
      · PIL 图像（QQ/微信截图、画图里复制）
      · 文件路径列表（在资源管理器里"复制"了一个图片文件）
      · None（剪贴板里是文字/空的）
    三种都要处理——用户说的"剪贴板里的图"这三种都算。
    """
    try:
        from PIL import Image, ImageGrab
    except Exception:
        return None
    try:
        data = ImageGrab.grabclipboard()
    except Exception:
        return None
    if data is None:
        return None
    if isinstance(data, list):
        for name in data:
            try:
                with Image.open(name) as im:
                    im.convert('RGB').save(png_path, 'PNG')
                return png_path
            except Exception:
                continue
        return None
    try:
        data.convert('RGB').save(png_path, 'PNG')
        return png_path
    except Exception:
        return None


def set_clipboard_image(png_path):
    """把一张图片放进系统剪贴板（`CF_DIB`）。成功 True，失败 False。

    为什么要自己写：tkinter 只能处理文本剪贴板，图片得走 Win32。用 Pillow 存一份 BMP
    **去掉 14 字节文件头**——剩下的正好就是 `CF_DIB` 要的 DIB（BITMAPINFOHEADER + 像素），
    比手写位图结构靠谱得多。24 位无压缩，粘到 Word / 微信 / 画图里都是图。
    """
    if not sys.platform.startswith('win'):
        return False
    try:
        import ctypes
        import io

        from PIL import Image

        img = Image.open(png_path).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'BMP')
        data = buf.getvalue()[14:]           # 去文件头 = CF_DIB
        if not data:
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # 64 位下必须声明返回/参数类型：HANDLE 不声明会被当成 32 位 int 截断（实测踩到）
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(0x0002, len(data))   # GMEM_MOVEABLE
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(8, handle):          # 8 = CF_DIB
                kernel32.GlobalFree(handle)
                return False
            return True          # 成功后内存归剪贴板所有，**不能**再 GlobalFree
        finally:
            user32.CloseClipboard()
    except Exception:
        try:
            import applog
            applog.get_logger().exception("写入剪贴板图片失败")
        except Exception:
            pass
        return False
