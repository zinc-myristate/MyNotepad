# PyInstaller 运行时钩子：彻底抑制控制台窗口
import os
import sys
import tempfile

# ⚠️ 必须在**应用代码之前**干净地导入 asyncio（本钩子最先执行）。
#
# 为什么：冻结之后，应用启动过程中某个环节会触发一次**半途而废**的 asyncio 导入——
# 子模块（asyncio.base_events / tasks / …）进了 sys.modules，`asyncio` 本身却不在，
# 而 windows_events / proactor_events 也没走完。等第 11 轮的 winrt（图片文字识别）
# 再 `import asyncio` 时，`from .base_events import *` 因为子模块已在 sys.modules 里
# 而**不再把 base_events 挂到包上**，于是 `asyncio/__init__.py` 里
# `__all__ = (base_events.__all__ + …)` 直接抛 `NameError: name 'base_events' is not defined`。
# 现象是"打包版点识别没反应"，而同样的代码在开发态完全正常（实测踩到，查了很久）。
#
# 在这里先干净地导入一次，后面所有 import asyncio 都是缓存命中，绕开这个坑。
try:
    import asyncio  # noqa: F401
except Exception:                                   # pragma: no cover
    try:                                            # 真失败也要留下痕迹，别静默
        import traceback
        with open(os.path.join(tempfile.gettempdir(), 'mynotepad_asyncio_import_error.txt'),
                  'w', encoding='utf-8') as _fh:
            _fh.write(traceback.format_exc())
    except Exception:
        pass

if sys.platform == 'win32':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # 反复释放控制台（CEF 可能多次创建）
        for _ in range(5):
            kernel32.FreeConsole()
        # 隐藏自身窗口
        kernel32.GetConsoleWindow()
        # 设置控制台窗口为隐藏
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 0)
        # 重定向流
        null = open(os.devnull, 'w')
        sys.stdout = null
        sys.stderr = null
    except Exception:
        pass
