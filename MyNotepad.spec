# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


def _say(msg):
    """往构建日志里写一行 —— **只输出 ASCII**。

    打包脚本可能在 stdout 编码是 cp1252 的环境里跑（英文版 Windows Server 的 runner 就是），
    中文/箭头这类字符会抛 `UnicodeEncodeError` 把构建直接打崩：2026-09-27 排查"Tcl/Tk 数据
    没进包"时，两次构建都死在一句中文 print 上（而不是死在真正的逻辑上）。构建日志宁可用
    英文，也不要因为"写日志"而失败。
    """
    try:
        print(msg.encode('ascii', 'replace').decode('ascii'))
    except Exception:                                            # noqa: BLE001
        pass


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
    _say('[spec] collect_license_datas() failed (build continues): %r' % (_exc,))
    _license_datas = []

# Tcl/Tk 数据目录：tkinter 起不来 = 打包版一启动就弹异常框（2026-09-27 真发过这样一个包）。
# PyInstaller 自己的发现逻辑（utils/hooks/tcl_tk.py）会在 Tcl 9 的 Python 上**静默失败**：
# 它在隔离子进程里 `tkinter.Tcl()`，一抛 TclError 就 return None（不报错、不警告），
# 数据目录一个都不收；而运行时的 pyi_rth__tkinter 仍会去 _tcl_data/_tk_data 找并直接抛异常。
# 这里按文件标记兜底；两条路都没找到就**中止构建** —— 宁可打包失败，也不发一个起不来的包。
# 详细背景见 build_resources/tcl_tk_data.py 顶部。
sys.path.insert(0, os.path.join(SPECPATH, 'build_resources'))
from tcl_tk_data import (TCL_ROOTNAME, TK_ROOTNAME, collect as _collect_tcl_tk,
                         diagnostics as _tcl_tk_diag, hook_dest_names)
try:
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info as _tcltk_info
    _hook_dests = hook_dest_names(_tcltk_info.data_files)
except Exception as _exc:                                        # noqa: BLE001
    _say('[spec] cannot read PyInstaller Tcl/Tk discovery (using fallback): %r' % (_exc,))
    _hook_dests = set()

_tcl_tk_extra = []
_missing_tcl_tk = [name for name in (TCL_ROOTNAME, TK_ROOTNAME) if name not in _hook_dests]
if _missing_tcl_tk:
    _found = {dest: src for src, dest in _collect_tcl_tk(sys.base_prefix, sys.prefix)}
    _tcl_tk_extra = [(_found[name], name) for name in _missing_tcl_tk if name in _found]
    _say('[spec] PyInstaller missed %s -> fallback collects: %s'
         % (_missing_tcl_tk, [src for src, _ in _tcl_tk_extra]))
    if len(_tcl_tk_extra) != len(_missing_tcl_tk):
        # 中止构建，并把诊断放进 **GitHub Actions 注解**（公开可读；job 日志要登录才能看）
        _msg = ('Tcl/Tk data dirs not found (%s): tkinter cannot start, so the artifact would be '
                'broken. diagnostics=%s' % (', '.join(_missing_tcl_tk), _tcl_tk_diag()))
        _say('::error title=Tcl/Tk data missing::%s' % _msg)
        raise SystemExit(_msg)

_all_datas = [('renderer', 'renderer'), ('resources', 'resources')] + _license_datas + _tcl_tk_extra

# 开发机 site-packages 里的无关包不进产物：PyInstaller 顺着可选 import 能把它们全带进来
# （winocr 的 serve() 带出 fastapi/uvicorn，一路带出 pydantic、rich、tornado……），
# 而它们不在 requirements.txt 的依赖树里，许可证也就收不到。CI 环境干净，这里让本机打包与它一致。
# 不能排除 wheel：PyInstaller 把它当 setuptools 内置模块的别名处理，排除后构建直接报错。
_UNRELATED_PACKAGES = [
    'annotated_doc', 'annotated_types', 'anyio', 'attr', 'attrs', 'bs4', 'charset_normalizer',
    'click', 'defusedxml', 'fastapi', 'h11', 'html5lib', 'idna', 'itsdangerous', 'jinja2',
    'markdown_it', 'markupsafe', 'mdurl', 'psutil', 'pydantic', 'pydantic_core', 'rich',
    'sniffio', 'soupsieve', 'starlette', 'threadpoolctl', 'tornado', 'typing_inspection',
    'tzdata', 'uvicorn', 'webencodings', 'websockets', 'werkzeug', 'yaml',
    # pywin32：本应用走 ctypes，这几个模块只是 logging、numpy.testing 的可选 import 带进来的
    'pythoncom', 'pywintypes', 'win32api', 'win32com', 'win32con', 'win32evtlog',
    'win32evtlogutil', 'win32pdh', 'winerror',
]


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
    excludes=_UNRELATED_PACKAGES,
    noarchive=False,
    optimize=0,
    # pystray 是 LGPL-3.0：按源码放在 _internal/pystray/，用户才能换成自己的兼容版本。
    # 默认会进 exe 内的 PYZ 归档，那样就替换不了。
    module_collection_mode={'pystray': 'py'},
)


def _is_ffmpeg(entry):
    name = entry[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
    return name.startswith('opencv_videoio_ffmpeg')


# FFmpeg 不进产物：opencv 的轮子自带 opencv_videoio_ffmpeg*.dll（LGPL-2.1，含 H.264/HEVC/AAC
# 解码器，商用还有专利问题），而本应用只用 OpenCV 做图标裁切，不处理视频。
# 显著性、人脸检测、积分图这几个调用不依赖它（已实测）；它只是 VideoCapture 才加载的插件。
a.binaries = [item for item in a.binaries if not _is_ffmpeg(item)]
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

# 本项目许可与第三方清单放在产物根目录。写在 spec 里，build.py 与 CI 两条路都覆盖
# （以前只有 release.yml 复制，本机打的包缺这两个文件）。
for _name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(os.path.join(SPECPATH, _name), os.path.join(DISTPATH, 'MyNotepad', _name))
