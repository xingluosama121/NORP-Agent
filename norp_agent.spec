# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for NORP Agent (Vibe Coding Agent)
Build: python -m PyInstaller norp_agent.spec
"""

import os

BASE_DIR = r'H:\vctest\20260811'

_block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[BASE_DIR],
    binaries=[],
    datas=[
        ('front.html', '.'),
        ('app_icon.ico', '.'),
        ('plugin_system', 'plugin_system'),
    ],
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'webview',
        'webview.platforms.winforms',
        'PyPDF2',
        'docx',
        'openpyxl',
        'pptx',
        'keyring',
        'keyring.backends.Windows',
        'win32crypt',
        'anthropic',
        'plugin_system',
        'plugin_system.manager',
        'plugin_system.context',
        'plugin_system.security',
        'workspace_index',
        'file_surgery',
        'web_fetcher_native',
        'context_index',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas',
        'jedi', 'IPython', 'ipykernel',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=_block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=_block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NORP Agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
