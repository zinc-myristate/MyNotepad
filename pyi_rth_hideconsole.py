# PyInstaller 运行时钩子：彻底抑制控制台窗口
import os
import sys

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
    except:
        pass
