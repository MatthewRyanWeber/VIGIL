# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Vigil Desktop.
# Build with:  pyinstaller vigil.spec
#

import os

SPEC_DIR = os.path.abspath(SPECPATH)
PROJECT_ROOT = os.path.dirname(SPEC_DIR)

a = Analysis(
    [os.path.join(SPEC_DIR, 'vigil_desktop.py')],
    pathex=[PROJECT_ROOT],
    datas=[
        (os.path.join(PROJECT_ROOT, 'index.html'), '.'),
        (os.path.join(PROJECT_ROOT, 'core'), 'core'),
        (os.path.join(PROJECT_ROOT, 'static'), 'static'),
        (os.path.join(PROJECT_ROOT, 'templates'), 'templates'),
        (os.path.join(PROJECT_ROOT, 'config.example.json'), '.'),
    ],
    hiddenimports=[
        'core.config',
        'core.routes',
        'core.checks',
        'core.status',
        'core.auth',
        'core.certs',
        'webview',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Vigil',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='Vigil',
)
