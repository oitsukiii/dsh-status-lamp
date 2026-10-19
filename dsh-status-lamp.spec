# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

单文件 exe，内嵌素材与图标。在 Windows 上构建（PyInstaller 不跨平台）。
    pyinstaller --clean dsh-status-lamp.spec
"""

block_cipher = None

a = Analysis(
    ['client/lamp.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets', 'assets'),      # 素材
        ('dsh_lamp', 'dsh_lamp'),  # 包本身（有 __init__，按数据带一份更稳）
    ],
    hiddenimports=[
        'PIL._tkinter_finder',     # Pillow 的 tk 桥，不写会缺
        'dsh_lamp.i18n',
        'dsh_lamp.state',
        'dsh_lamp.event_tail',
        'dsh_lamp.backends',
        'dsh_lamp.paths',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'PyQt5', 'PySide2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DSH-Lamp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    icon='client/lamp.ico',
)
