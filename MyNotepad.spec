# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# 图片文字识别（第 11 轮）走 winrt 投影：真正的实现是 winrt 包下面的一堆
# `_winrt_*.pyd` 扩展 + msvcp140.dll，PyInstaller 既没有内置 hook、也不会自动
# 顺着 `from winrt.windows.media.ocr import ...` 把这些二进制收进去。
# 少一个 .pyd 的现象就是"打包版点识别没反应"（import 抛异常），所以这里显式收。
_winrt_binaries = collect_dynamic_libs('winrt')
_winrt_modules = collect_submodules('winrt')

# 第三方许可证：产物自带每个依赖的许可证副本（合规要求"再分发时保留许可条款"）。
# 逻辑在 build_resources/collect_licenses.py（可单独跑：python build_resources/collect_licenses.py）。
# 这里包一层 try：收集失败只是少几个说明文件，绝不能让打包挂掉。
try:
    sys.path.insert(0, os.path.join(SPECPATH, 'build_resources'))
    from collect_licenses import collect_license_datas
    _license_datas = collect_license_datas()
except Exception as _exc:                                        # noqa: BLE001
    print('[spec] 收集第三方许可证失败（不影响打包）：%r' % (_exc,))
    _license_datas = []

# Tcl/Tk 数据目录：tkinter 起不来 = 打包版一启动就弹异常框（2026-09-27 真发过这样一个包）。
# PyInstaller 自己的发现逻辑（utils/hooks/tcl_tk.py）会在 Tcl 9 的 Python 上**静默失败**：
# 它在隔离子进程里 `tkinter.Tcl()`，一抛 TclError 就 return None（不报错、不警告），
# 数据目录一个都不收；而运行时的 pyi_rth__tkinter 仍会去 _tcl_data/_tk_data 找并直接抛异常。
# 这里按文件标记兜底；两条路都没找到就**中止构建** —— 宁可打包失败，也不发一个起不来的包。
# 详细背景见 build_resources/tcl_tk_data.py 顶部。
sys.path.insert(0, os.path.join(SPECPATH, 'build_resources'))
from tcl_tk_data import TCL_ROOTNAME, TK_ROOTNAME, collect as _collect_tcl_tk, hook_dest_names
try:
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info as _tcltk_info
    _hook_dests = hook_dest_names(_tcltk_info.data_files)
except Exception as _exc:                                        # noqa: BLE001
    print('[spec] 读不到 PyInstaller 的 Tcl/Tk 发现结果（改用兜底）：%r' % (_exc,))
    _hook_dests = set()

_tcl_tk_extra = []
_missing_tcl_tk = [name for name in (TCL_ROOTNAME, TK_ROOTNAME) if name not in _hook_dests]
if _missing_tcl_tk:
    _found = {dest: src for src, dest in _collect_tcl_tk(sys.base_prefix, sys.prefix)}
    _tcl_tk_extra = [(_found[name], name) for name in _missing_tcl_tk if name in _found]
    print('[spec] PyInstaller 没收到的 Tcl/Tk 数据 %s → 兜底补上：%s'
          % (_missing_tcl_tk, [src for src, _ in _tcl_tk_extra]))
    if len(_tcl_tk_extra) != len(_missing_tcl_tk):
        raise SystemExit(
            '[spec] 打包中止：找不到 Tcl/Tk 数据目录（%s）。tkinter 会起不来，产物必然是坏的；'
            '请检查构建用的 Python 是否带完整 Tcl/Tk：%s'
            % ('、'.join(_missing_tcl_tk), sys.base_prefix))

_all_datas = [('renderer', 'renderer'), ('resources', 'resources')] + _license_datas + _tcl_tk_extra


a = Analysis(
    ['app.pyw'],
    pathex=[],
    binaries=_winrt_binaries,
    datas=_all_datas,
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
