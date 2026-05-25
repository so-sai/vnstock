# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

block_cipher = None

project_root = Path(os.getcwd())
frontend_dist = project_root / "frontend" / "dist"

# Include the built frontend bundle if present.
datas = []
if frontend_dist.exists():
    datas.append((str(frontend_dist), "frontend/dist"))

hiddenimports = [
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "starlette",
    "anyio",
    "h11",
    "httpcore",
]

a = Analysis(
    [str(project_root / "backend" / "src" / "run_sentinel.py")],
    pathex=[str(project_root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "notebook", "matplotlib", "numpy"],
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
    name="Sentinel_Fortress_v1.0",
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
