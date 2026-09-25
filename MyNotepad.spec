# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# 图片文字识别（第 11 轮）走 winrt 投影：真正的实现是 winrt 包下面的一堆
# `_winrt_*.pyd` 扩展 + msvcp140.dll，PyInstaller 既没有内置 hook、也不会自动
# 顺着 `from winrt.windows.media.ocr import ...` 把这些二进制收进去。
# 少一个 .pyd 的现象就是"打包版点识别没反应"（import 抛异常），所以这里显式收。
_winrt_binaries = collect_dynamic_libs('winrt')
_winrt_modules = collect_submodules('winrt')


a = Analysis(
    ['app.pyw'],
    pathex=[],
    binaries=_winrt_binaries,
    datas=[('renderer', 'renderer'), ('resources', 'resources')],
    # 注：这里曾列 'pycparser.yacctab', 'pycparser.lextab' —— pycparser 3.x 已删除这两个模块
    # （实测 find_spec 为 None），保留只会让**每次构建都刷两行** `ERROR: Hidden import ... not found`，
    # 淹没真正缺失的 hiddenimport。故移除。
    # PIL.ImageGrab 是**函数内导入**（截图那条路才需要）。PyInstaller 能扫到函数内 import，
    # 但截图是第 10 轮的主功能，缺了它打包版会静默失效，所以显式列一份兜底。
    # ocr / winocr / winrt.*：同上，识别文字是第 11 轮的主功能。
    hiddenimports=['backend', 'desktop', 'pystray', 'docx', 'openpyxl', 'PIL', 'PIL.ImageGrab',
                   'cryptography', 'cv2', 'numpy', 'ocr', 'winocr'] + _winrt_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_hideconsole.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MyNotepad',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['cryptography', 'libcrypto', 'libssl', 'opencv', 'numpy'],
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['resources\\icon.ico'],
    version='build_resources/version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MyNotepad',
)
