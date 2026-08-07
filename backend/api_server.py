"""PyInstaller entry point: boot FastAPI via uvicorn.
   Implements Dynamic Path Resolution — data lives outside the temp dir
   so that daily-close commits survive sidecar shutdown.
"""
import io
import os
import shutil
import sys

# Set UTF-8 encoding for stdout/stderr to prevent Windows charmap encoding crashes
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from pathlib import Path

# ── Dynamic Path Resolution ──────────────────────────────────────────
USER_HOME = Path(os.path.expanduser("~"))
APP_DATA_DIR = USER_HOME / ".ptck_vn" / "data"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE_NAME = "screener_cache.db"
TARGET_DB_PATH = APP_DATA_DIR / DB_FILE_NAME

if getattr(sys, 'frozen', False):
    meipass = Path(sys._MEIPASS)

    # ── Seed ALL bundled databases to permanent user data dir ──
    bundled_data_dir = meipass / "data"
    if bundled_data_dir.exists():
        for db_file in bundled_data_dir.glob("*.db"):
            target = APP_DATA_DIR / db_file.name
            if not target.exists() or target.stat().st_size == 0:
                shutil.copy2(db_file, target)
                print(f"[api_server] Copied/Replaced seed DB {db_file.name} -> {target}")
            else:
                print(f"[api_server] DB {db_file.name} already present and populated")

    # Override DATA_DIR via env so config.py picks up the permanent location
    os.environ["CUSTOM_DATA_PATH"] = str(APP_DATA_DIR)

    sys.path.insert(0, str(meipass))
    libs_dir = meipass / "libs"
    if libs_dir.exists():
        for lib_sub in libs_dir.iterdir():
            p = str(lib_sub)
            if p not in sys.path:
                sys.path.append(p)
    src_dir = meipass / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.append(str(src_dir))
else:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    libs_dir = root / "libs"
    if libs_dir.exists():
        for lib_sub in libs_dir.iterdir():
            p = str(lib_sub)
            if p not in sys.path:
                sys.path.append(p)

# ── Initialize schemas for any missing tables ─────────────────────────
# Must run after sys.path is set so init_db can be imported
try:
    from src.init_db import DATABASES, init_all
    init_all(APP_DATA_DIR)
    print(f"[api_server] Database schemas initialized at {APP_DATA_DIR}")

    # ── Pre-flight validation: đảm bảo 6 DB đều tồn tại và có schema ──
    missing = []
    for filename, _schema, _desc in DATABASES:
        db_path = APP_DATA_DIR / filename
        if not db_path.exists() or db_path.stat().st_size == 0:
            missing.append(filename)
    if missing:
        print(f"[api_server] WARNING: {len(missing)} database(s) missing or empty: {missing}")
    else:
        print(f"[api_server] All {len(DATABASES)} databases verified and ready")
except Exception as e:
    print(f"[api_server] WARNING: Schema init failed: {e}")

from src.api.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=17039, log_level="info")
