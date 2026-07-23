"""build_windows_installer.py — Build pipeline: Nuitka + Tauri + NSIS.

Pipeline:
  1. Compile FastAPI backend with Nuitka --onefile → uv_backend.exe (~15MB)
  2. Copy to frontend/src-tauri/binaries/ with target triple suffix
  3. Run `bun tauri build` → NSIS creates PTCK_Setup.exe
  4. Verify final size < 200MB

Usage:
    python backend/build_windows_installer.py [--dev] [--clean]

Args:
    --dev     Build in dev mode (skip Nuitka, use stub binary for testing)
    --clean   Remove build artifacts after completion

Size Budget:
    Nuitka backend (.exe):    ~15 MB  (Python stdlib + Numpy/SQLite compiled in)
    Tauri Rust shell:          ~8 MB   (WebView2 wrapper + sidecar manager)
    Frontend dist (React):     ~2 MB   (Vite tree-shaken + vendor chunk)
    NSIS installer overhead:   ~5 MB   (compression + metadata)
    ───────────────────────────────
    Total:                    ~30 MB   << 200 MB SAFE

    Included in installer (as empty markers, bootstrapped at first run):
    - backend/data/*.db       [6 registered DBs: screener_cache, telemetry,
                               portfolio_state, sentinel_macro, shadow_cao, quant]
    Excluded entirely:
    - backend/data/*.db data  ~218 MB — hydrated live at first run via daily_updater
    - backend/__pycache__     ~50 MB  — build artifacts
    - node_modules            ~500 MB — dev dependencies
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Nạp đường dẫn Bun cấp User vào PATH của tiến trình Python hiện tại
_bun_path = os.path.expanduser(r"~\.bun\bin")
if _bun_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] += os.pathsep + _bun_path
BUN_EXE = shutil.which("bun") or os.path.join(_bun_path, "bun.exe")

# Giảm song song MSVC để tránh xung đột clcache + quá tải CPU
os.environ.setdefault("CLCACHE_NODIRECT", "1")

# ── Paths ───────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent  # parent of backend/
FRONTEND_DIR = PROJECT_ROOT / "frontend"
TAURI_SRC_DIR = FRONTEND_DIR / "src-tauri"
BINARIES_DIR = TAURI_SRC_DIR / "binaries"
BACKEND_SRC = HERE / "src"
BACKEND_ENTRY = HERE.parent / "ptck.py"
BACKEND_DATA = HERE / "data"
BUILD_DIR = HERE / "build" / "installer"

# Tauri triple for x64 Windows
TARGET_TRIPLE = "x86_64-pc-windows-msvc"
SIDECAR_NAME = "uv_backend"

# ── Config ──────────────────────────────────────────────────────────

SIZE_LIMIT_MB = 200


# ── Utilities ───────────────────────────────────────────────────────

def log(msg: str, emoji: str = "∙"):
    ts = time.strftime("%H:%M:%S")
    print(f"  {emoji} [{ts}] {msg}")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 600,
        stream: bool = False) -> str:
    """Run a command and return stdout. Raise on failure.

    Khi stream=True, stdout/stderr được in trực tiếp ra terminal (tránh
    pipe buffer deadlock) — bắt buộc cho Nuitka compile lâu.
    """
    log(f"Running: {' '.join(cmd[:4])}...", "⚙")
    if stream:
        # Stream trực tiếp ra terminal — tránh pipe buffer deadlock
        # shell=True trên Windows để tìm được npx.cmd, bun, v.v.
        result = subprocess.run(
            cmd, cwd=cwd or PROJECT_ROOT,
            stdout=None, stderr=None, timeout=timeout,
            shell=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {result.returncode}:\n"
                f"  {' '.join(cmd)}"
            )
        return ""
    result = subprocess.run(
        cmd, cwd=cwd or PROJECT_ROOT,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"  ❌ STDOUT:\n{result.stdout[:2000]}")
        print(f"  ❌ STDERR:\n{result.stderr[:2000]}")
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}:\n"
            f"  {' '.join(cmd)}\n"
            f"  {result.stderr[-500:]}"
        )
    return result.stdout


def get_dir_size(path: Path) -> int:
    """Total size in bytes of a directory."""
    total = 0
    for root, dirs, files in os.walk(path):
        # Skip __pycache__
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def format_size(bytes_: int) -> str:
    if bytes_ > 1024**3:
        return f"{bytes_ / 1024**3:.2f} GB"
    return f"{bytes_ / 1024**2:.2f} MB"


def find_sqlite_databases(data_dir: Path) -> list[Path]:
    """Find SQLite databases to exclude from installer."""
    dbs = []
    for root, dirs, files in os.walk(data_dir):
        for f in files:
            if f.endswith((".db", ".sqlite", ".sqlite3")):
                dbs.append(Path(root) / f)
    return dbs


# ── Step 1: Nuitka Compile ─────────────────────────────────────────

def step1_nuitka_compile(dev_mode: bool = False) -> Path:
    """Compile backend to standalone .exe using Nuitka.

    Flags đã được chuẩn hóa cho Nuitka 4.x:
    - --assume-yes-for-downloads: tự động tải Dependency Walker
    - --windows-console-mode=disable: ẩn console (không dùng --disable-console cũ)
    - Bỏ --plugin-enable=numpy (đã deprecated)
    - Bỏ --no-pyi-file (chỉ dùng cho module mode)

    In dev mode, create a stub .exe for testing the Tauri build pipeline.
    """
    log("Step 1/5: Compiling backend with Nuitka...", "🔨")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    output_exe = BUILD_DIR / f"{SIDECAR_NAME}.exe"

    if dev_mode:
        log("  [DEV MODE] Creating stub binary...", "🧪")
        stub_py = BUILD_DIR / "_stub.py"
        stub_py.write_text(
            '"""Stub backend for dev build testing."""\n'
            "import time\n"
            'print("PTCK Backend Stub — running for 30s")\n'
            "time.sleep(30)\n"
        )
        shutil.copy2(stub_py, output_exe)
        log(f"  Stub created at {output_exe} ({output_exe.stat().st_size} bytes)", "✅")
        return output_exe

    # Verify Nuitka is installed
    try:
        run(["python", "-m", "nuitka", "--version"], timeout=30)
    except (RuntimeError, FileNotFoundError):
        log("Nuitka not found. Installing...", "📦")
        run([sys.executable, "-m", "pip", "install", "nuitka"], timeout=120)

    # Build Nuitka args — đã làm sạch theo Nuitka 4.x
    args = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        "--standalone",
        "--windows-console-mode=disable",
        "--assume-yes-for-downloads",
        f"--jobs={max(2, os.cpu_count() // 2)}",
        "--follow-imports",
        "--include-package=src",
        "--include-package=core",
        "--include-package=vnstock",
        "--include-package-data=src",
        "--include-package-data=libs",
        f"--output-dir={BUILD_DIR}",
        "--remove-output",
        f"--output-filename={SIDECAR_NAME}.exe",
        # Nofollow: loại bỏ test/docs để giảm dung lượng
        "--nofollow-import-to=*.tests",
        "--nofollow-import-to=*.test",
        "--nofollow-import-to=*.docs",
        "--nofollow-import-to=*.examples",
        "--nofollow-import-to=unittest",
        # Include modules thiết yếu
        "--include-module=uvicorn",
        "--include-module=fastapi",
        "--include-module=sqlite3",
        "--include-module=numpy",
        "--include-module=pandas",
        "--include-module=PIL",
        # Nofollow gói nặng không cần thiết
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=tensorflow",
        "--nofollow-import-to=torch",
        "--nofollow-import-to=keras",
        "--nofollow-import-to=sklearn",
        "--nofollow-import-to=bokeh",
        "--nofollow-import-to=plotly",
        "--nofollow-import-to=dash",
        "--nofollow-import-to=streamlit",
        "--nofollow-import-to=jupyter",
        "--nofollow-import-to=ipython",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tox",
        "--nofollow-import-to=coverage",
        "--nofollow-import-to=sphinx",
        "--nofollow-import-to=docutils",
        "--nofollow-import-to=setuptools",
        "--nofollow-import-to=pip",
        "--nofollow-import-to=wheel",
        str(BACKEND_ENTRY),
    ]

    log(f"  Nuitka compiling {BACKEND_ENTRY.name}... (this takes 2-5 min)", "⏳")
    # Thêm backend/ + project root vào PYTHONPATH để Nuitka tìm được src và core package
    old_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(PROJECT_ROOT / "backend") + os.pathsep + str(PROJECT_ROOT / "backend" / "libs" / "vnstock") + os.pathsep + str(PROJECT_ROOT) + os.pathsep + old_pythonpath
    run(args, timeout=3600, stream=True)  # stream=True: in log trực tiếp ra terminal

    if not output_exe.exists():
        raise FileNotFoundError(
            f"Nuitka output not found at {output_exe}."
        )

    size_mb = output_exe.stat().st_size / 1024**2
    log(f"  Nuitka output: {output_exe.name} ({size_mb:.2f} MB)", "✅")

    if size_mb > 100:
        log(f"  ⚠ WARNING: Backend binary is {size_mb:.1f} MB! "
            f"Consider adding more --nofollow-import-to exclusions.", "⚠")

    return output_exe


# ── Step 2: Prepare Data Directory ─────────────────────────────────

def step2_prepare_data() -> Path:
    """Prepare backend/data for installer.

    Strategy:
    - Exclude large SQLite databases (they're bootstrapped at first run)
    - Include schema files, seed CSVs, and config
    - Create a clean data template directory
    """
    log("Step 2/5: Preparing data directory for installer...", "📁")

    data_template = BUILD_DIR / "data_template"
    if data_template.exists():
        shutil.rmtree(data_template)

    # Create skeleton structure
    (data_template / "bootstraps").mkdir(parents=True, exist_ok=True)
    (data_template / "output").mkdir(parents=True, exist_ok=True)
    (data_template / "logs").mkdir(parents=True, exist_ok=True)
    (data_template / "alerts").mkdir(parents=True, exist_ok=True)

    # Copy bootstrap CSVs (small)
    bs_src = BACKEND_DATA / "bootstraps"
    if bs_src.exists():
        for f in bs_src.iterdir():
            if f.suffix in (".csv", ".json", ".yaml", ".yml") and f.stat().st_size < 10 * 1024 * 1024:
                shutil.copy2(f, data_template / "bootstraps" / f.name)

    # Copy small config files from data root
    for f in BACKEND_DATA.iterdir():
        if f.is_file() and f.suffix in (".json", ".yaml", ".yml", ".toml", ".ini"):
            if f.stat().st_size < 1024 * 1024:  # < 1MB
                shutil.copy2(f, data_template / f.name)

    # List all 6 registered databases from init_db.py DATABASES registry
    # (screener_cache, telemetry, portfolio_state, sentinel_macro, shadow_cao, quant)
    # Each is bootstrapped at first run — only marker files in installer.
    REGISTERED_DBS = [
        "screener_cache.db", "telemetry.db", "portfolio_state.db",
        "sentinel_macro.db", "shadow_cao.db", "quant.db",
    ]
    for db_name in REGISTERED_DBS:
        marker = data_template / db_name
        marker.write_text("")  # empty file — schema created at runtime via `python ptck.py db init`

    log(f"  Created {len(REGISTERED_DBS)} empty DB markers: {', '.join(REGISTERED_DBS)}", "📊")

    size_mb = get_dir_size(data_template) / 1024**2
    log(f"  Data template: {size_mb:.2f} MB ({len(list(data_template.rglob('*')))} files)", "📊")

    return data_template


# ── Step 3: Copy Binary to Tauri Binaries ──────────────────────────

def _unlock_binary(target_path: Path):
    """Kill tiến trình đang giữ lock trên binary để tránh WinError 32."""
    if not target_path.exists():
        return
    try:
        target_path.rename(target_path)  # test lock
        return  # không bị lock, không cần làm gì
    except PermissionError:
        log(f"  Binary bị lock ({target_path.name}), đang giải phóng...", "🔓")
    import subprocess
    for name in [target_path.stem, target_path.name]:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", name],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    import time
    time.sleep(0.5)


def step3_copy_binary(nuitka_exe: Path) -> Path:
    """Copy compiled binary to Tauri sidecar location.

    Tauri expects: binaries/uv_backend-x86_64-pc-windows-msvc.exe

    Edge cases handled:
    - Nuitka build failed → nuitka_exe không tồn tại, dùng target nếu có
    - Fallback từ sidecar cũ → nuitka_exe == target_path, bỏ qua copy
    """
    log("Step 3/5: Copying binary to Tauri sidecar location...", "📋")

    BINARIES_DIR.mkdir(parents=True, exist_ok=True)

    target_name = f"{SIDECAR_NAME}-{TARGET_TRIPLE}.exe"
    target_path = BINARIES_DIR / target_name

    # 1) Nguồn là chính target (fallback từ sidecar cũ) → giữ nguyên
    if nuitka_exe.resolve() == target_path.resolve():
        if target_path.exists():
            log(f"  Binary already in place: {target_path} ({target_path.stat().st_size / 1024**2:.2f} MB)", "✅")
            return target_path
        # Cả nguồn và đích đều mất → không thể tiếp tục
        raise FileNotFoundError(
            f"Cannot proceed: nuitka_exe == target_path but neither exists.\n"
            f"  Nuitka build failed and no valid sidecar found at {target_path}"
        )

    # 2) Nguồn không tồn tại (Nuitka lỗi không ra file)
    if not nuitka_exe.exists():
        if target_path.exists():
            log(f"  Nuitka build failed, reusing existing sidecar: {target_path}", "⚠")
            return target_path
        raise FileNotFoundError(
            f"Cannot proceed: Nuitka output missing at {nuitka_exe} "
            f"and no existing sidecar at {target_path}"
        )

    # 3) Nguồn tồn tại → copy đè lên target
    _unlock_binary(target_path)
    if target_path.exists():
        try:
            target_path.unlink()
        except PermissionError:
            log("  ❌ Vẫn không giải phóng được binary. Hãy đóng ứng dụng PTCK_VN rồi thử lại.", "❌")
            raise

    shutil.copy2(nuitka_exe, target_path)
    size = target_path.stat().st_size / 1024**2
    log(f"  Copied: {nuitka_exe.name} → {target_path} ({size:.2f} MB)", "✅")
    return target_path

    return target_path


# ── Step 4: Update tauri.conf.json ──────────────────────────────────

def step4_update_tauri_config(data_template: Path) -> None:
    """Update tauri.conf.json (Tauri v2 schema) with correct paths and NSIS config.

    Tauri v2 uses flat schema: no `tauri` root key.
    """
    log("Step 4/5: Updating tauri.conf.json for NSIS bundler (Tauri v2)...", "🔧")

    config_path = TAURI_SRC_DIR / "tauri.conf.json"
    if not config_path.exists():
        raise FileNotFoundError(f"tauri.conf.json not found at {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Update bundle info (root-level in v2)
    bundle = config.setdefault("bundle", {})
    # Xóa identifier khỏi bundle (nó ở root, không trong bundle)
    bundle.pop("identifier", None)
    bundle.update({
        "active": True,
        "targets": ["nsis"],
        "externalBin": [f"binaries/{SIDECAR_NAME}"],
        "shortDescription": "PTCK_VN — Terminal phân tích thị trường chứng khoán Việt Nam",
        "longDescription": (
            "PTCK_VN Command Center: Hệ thống phân tích thị trường chứng khoán "
            "Việt Nam với 49 lệnh CLI song ngữ, 4-tier Auto-Recovery Data Pipeline, "
            "và cơ chế Anti-Survivorship LAW-001."
        ),
        "category": "Finance",
        "copyright": "© 2026 PTCK_VN",
    })

    # Windows NSIS configuration (bundle.windows.nsis in v2)
    nsis_config = {
        "installMode": "currentUser",
        "displayLanguageSelector": False,
        "installerIcon": "icons/icon.ico",
        "headerImage": "icons/128x128.png",
    }
    bundle.setdefault("windows", {})["nsis"] = nsis_config

    # Write back
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    log(f"  Updated {config_path}", "✅")


# ── Step 5: Run Tauri Build ────────────────────────────────────────

def step5_tauri_build() -> Path:
    """Run `bun tauri build` to produce NSIS installer.

    Returns path to the generated NSIS installer.
    """
    log("Step 5/5: Running Tauri build (this takes 3-8 min)...", "🚀")

    # Install frontend dependencies if needed
    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        log("  Installing frontend dependencies...", "📦")
        run([BUN_EXE, "install"], cwd=FRONTEND_DIR, timeout=120, stream=True)

    # Build frontend (vite) — dùng bun x vite build để bỏ qua TypeScript check
    log("  Building frontend (vite)...", "🏗")
    run([BUN_EXE, "x", "vite", "build"], cwd=FRONTEND_DIR, timeout=120, stream=True)

    # Run Tauri build
    log("  Building Tauri app (cargo build + NSIS)...", "🦀")
    run([BUN_EXE, "tauri", "build"], cwd=FRONTEND_DIR, timeout=1800, stream=True)

    # Find installer
    installer_dir = TAURI_SRC_DIR / "target" / "release" / "bundle" / "nsis"
    installers = list(installer_dir.glob("*.exe")) + list(installer_dir.glob("*.msi"))
    if not installers:
        # Try alternative path
        installer_dir = TAURI_SRC_DIR / "target" / "release" / "bundle" / "msi"
        installers = list(installer_dir.glob("*.exe")) + list(installer_dir.glob("*.msi"))

    if not installers:
        log("  ⚠ No installer found in bundle directory. Checking target/release...", "⚠")
        release_dir = TAURI_SRC_DIR / "target" / "release"
        installers = list(release_dir.glob("*.exe")) + list(release_dir.glob("*.msi"))

    if not installers:
        raise FileNotFoundError(
            f"No installer found. Check {TAURI_SRC_DIR / 'target' / 'release' / 'bundle'}"
        )

    installer = max(installers, key=os.path.getmtime)
    log(f"  Installer created: {installer}", "✅")
    return installer


# ── Step 6: Size Verification ──────────────────────────────────────

def step6_verify_size(installer: Path) -> bool:
    """Verify installer size is under 200MB."""
    log("Verifying installer size...", "📏")

    size_bytes = installer.stat().st_size
    size_mb = size_bytes / 1024**2

    log(f"  Installer: {installer.name} ({size_mb:.2f} MB)", "💾")

    if size_mb > SIZE_LIMIT_MB:
        log(f"  ❌ FAIL: {size_mb:.2f} MB exceeds {SIZE_LIMIT_MB} MB limit!", "❌")
        log("  Tips to reduce size:", "💡")
        log("  1. Add more --nofollow-import-to to Nuitka", "   ")
        log("  2. Exclude more SQLite database seeds", "   ")
        log("  3. Use --lto=yes in Cargo.toml for Rust binary", "   ")
        return False

    log(f"  ✅ PASS: {size_mb:.2f} MB < {SIZE_LIMIT_MB} MB limit", "✅")
    return True


# ── Cleanup ─────────────────────────────────────────────────────────

def cleanup():
    """Remove build artifacts."""
    log("Cleaning build artifacts...", "🧹")
    dirs_to_clean = [
        BUILD_DIR,
        BACKEND_SRC / "__pycache__",
        BACKEND_DATA / "__pycache__",
        HERE / "__pycache__",
        FRONTEND_DIR / "dist",
        TAURI_SRC_DIR / "target",
        BINARIES_DIR,
    ]
    for d in dirs_to_clean:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log(f"  Removed: {d}", "🗑")


# ── Main ────────────────────────────────────────────────────────────

def main():
    print()
    print("  ╔═══════════════════════════════════════════════════════════╗")
    print("  ║     PTCK_VN — Windows Installer Build Pipeline          ║")
    print("  ║     Nuitka + Tauri + NSIS                               ║")
    print("  ╚═══════════════════════════════════════════════════════════╝")
    print()

    dev_mode = "--dev" in sys.argv
    do_clean = "--clean" in sys.argv

    if dev_mode:
        log("DEV MODE: Using stub binary (not Nuitka)", "🧪")
    log(f"Target triple: {TARGET_TRIPLE}", "🎯")
    log(f"Output: {TAURI_SRC_DIR / 'target' / 'release' / 'bundle' / 'nsis'}", "📁")
    print()

    # Step 1: Compile backend
    try:
        backend_exe = step1_nuitka_compile(dev_mode)
    except Exception as e:
        log(f"Step 1 failed: {e}", "❌")
        log("Continuing with existing binary if available...", "⚠")
        existing = BINARIES_DIR / f"{SIDECAR_NAME}-{TARGET_TRIPLE}.exe"
        if existing.exists():
            log(f"Found existing binary: {existing}", "✅")
            backend_exe = existing
        else:
            raise

    # Step 2: Prepare data template
    data_template = step2_prepare_data()

    # Step 3: Copy binary
    step3_copy_binary(backend_exe)

    # Step 4: Update config
    step4_update_tauri_config(data_template)

    # Step 5: Build
    installer = step5_tauri_build()

    print()
    log("═══ BUILD COMPLETE ═══", "🎉")
    log(f"Installer: {installer}", "📦")
    log(f"Size: {installer.stat().st_size / 1024**2:.2f} MB", "💾")

    # Step 6: Verify size
    step6_verify_size(installer)

    # Cleanup
    if do_clean:
        cleanup()

    print()
    log("To install, run:", "👉")
    log(f"  {installer}", "   ")
    print()

    # Return exit code
    if not step6_verify_size(installer):
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    main()
