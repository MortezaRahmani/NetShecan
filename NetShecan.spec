# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['netshecan.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icons/netshecan-icon.ico', 'assets/icons'),
           ('assets/icons/netshecan-icon.png', 'assets/icons'),
           ('assets/providers', 'assets/providers'),
           ('assets/audio', 'assets/audio')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NetShecan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['assets/icons/netshecan-icon.ico'],
)