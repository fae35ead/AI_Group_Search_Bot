# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path.cwd()
frontend_dist_dir = project_root.parent / 'frontend' / 'dist'
playwright_browsers_dir = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', Path.home() / 'AppData' / 'Local' / 'ms-playwright'))

if not frontend_dist_dir.exists():
    raise SystemExit(f'Frontend build output is missing: {frontend_dist_dir}')

datas = [(str(frontend_dist_dir), 'frontend_dist')]
if playwright_browsers_dir.exists():
    datas.append((str(playwright_browsers_dir), 'ms-playwright'))

datas += collect_data_files('playwright')

hiddenimports = []
hiddenimports += collect_submodules('playwright')
hiddenimports += collect_submodules('uvicorn')


a = Analysis(
    ['launcher.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='ai-group-discovery',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
