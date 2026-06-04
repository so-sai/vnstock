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

def find_modules_in_package(dir_path, package_name):
    modules = []
    p = Path(dir_path)
    if not p.exists():
        return modules
    for path in p.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel_parts = list(path.relative_to(p).parts)
        parts = [package_name]
        for part in rel_parts:
            parts.append(part)
        if parts[-1] == "__init__.py":
            mod_name = ".".join(parts[:-1])
        else:
            parts[-1] = parts[-1][:-3]
            mod_name = ".".join(parts)
        if mod_name:
            modules.append(mod_name)
    return list(set(modules))

hiddenimports = [
    "vnstock",
    "canonical",
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "starlette",
    "anyio",
    "h11",
    "httpcore",
    "pandas",
    "numpy",
    "sqlite3",
    "yfinance",
    "requests",
    "urllib3",
    "beautifulsoup4",
    "tenacity",
    "pytz",
    "python-dateutil",
    "vnai",
]

hiddenimports.extend(find_modules_in_package(project_root / "backend" / "src", "src"))
hiddenimports.extend(find_modules_in_package(project_root / "core", "core"))
hiddenimports.extend(find_modules_in_package(project_root / "backend" / "libs" / "vnstock" / "vnstock", "vnstock"))
hiddenimports.extend(find_modules_in_package(project_root / "backend" / "libs" / "canonical", "canonical"))

hiddenimports = sorted(list(set(hiddenimports)))

a = Analysis(
    [str(project_root / "backend" / "src" / "run_sentinel.py")],
    pathex=[
        str(project_root / "backend"),
        str(project_root),  # so core/ modules are discoverable
        str(project_root / "backend" / "libs" / "vnstock"),  # so vnstock is discoverable
        str(project_root / "backend" / "libs"),  # so canonical is discoverable
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "notebook", "matplotlib"],
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
