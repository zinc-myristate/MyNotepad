# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.pyw'],
    pathex=[],
    binaries=[],
    datas=[('renderer', 'renderer'), ('resources', 'resources')],
    # 注：这里曾列 'pycparser.yacctab', 'pycparser.lextab' —— pycparser 3.x 已删除这两个模块
    # （实测 find_spec 为 None），保留只会让**每次构建都刷两行** `ERROR: Hidden import ... not found`，
    # 淹没真正缺失的 hiddenimport。故移除。
    hiddenimports=['backend', 'docx', 'openpyxl', 'PIL', 'cryptography',
                   'cv2', 'numpy'],
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
