import argparse
import io
import logging
import socket
import sys
import webbrowser
from pathlib import Path

if sys.platform == "win32":
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _hydrate_path():
    """Zero-Friction Sentinel v2.2: Anchor on AGENTS.md + backend is_dir"""
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    # Remove script's own directory (backend/src) from sys.path to prevent shadowing the root 'core' package
    src_dir = str(root_path / "backend" / "src")
    while src_dir in sys.path:
        sys.path.remove(src_dir)
    return root_path


PROJECT_ROOT = _hydrate_path()


def _resolve_data_dir() -> Path:
    """Resolve data directory for database storage.

    Portable mode (frozen): store next to .exe
    Dev mode: use backend/data/
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return PROJECT_ROOT / "backend" / "data"


DATA_DIR = _resolve_data_dir()
logger = logging.getLogger(__name__)

import uvicorn

from src.api.main import app


def find_free_port(start: int = 8000, end: int = 8999) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port available between 8000 and 8999")


def check_cognitive_modules() -> None:
    print("  🧠 Cognitive modules health check...")
    try:
        from core.guard import DECISION_VIEW_SCHEMA_VERSION, lock_schema
        from core.presentation import get_label
        from core.signal_provenance import ProvenanceRegistry, SignalProvenanceNode

        lb = get_label("risk", "NEUTRAL")
        assert lb is not None
        lock_schema()
        print(f"  ✅ Epistemic + Presentation + Guard layers OK (schema v{DECISION_VIEW_SCHEMA_VERSION})")
    except ImportError as e:
        print(f"  ⚠️  Cognitive module missing: {e}")
    except Exception as e:
        print(f"  ⚠️  Cognitive module error: {e}")


def show_banner(port: int, data_dir: Path) -> None:
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     SENTINEL MARKET COGNITION TERMINAL    ║")
    print("  ║          Alpha — Local Execution          ║")
    print("  ╠══════════════════════════════════════════╣")
    print(f"  ║  Serving on http://127.0.0.1:{port:<5}          ║")
    if getattr(sys, "frozen", False):
        print(f"  ║  Data: {str(data_dir):35s} ║")
    print("  ║  DecisionView schema: v1.0.0             ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  Hệ thống hỗ trợ ra quyết định – không phải khuyến nghị đầu tư.")
    print()


def cmd_init() -> int:
    """Initialize ALL database schemas (no network)."""
    from src.init_db import run_init

    print("=" * 60)
    print("  PTCK — DATABASE INITIALIZATION")
    print("=" * 60)
    result = run_init(str(DATA_DIR))
    print("=" * 60)
    print("  Chạy '--seed' để tải dữ liệu thị trường." if result > 0 else "  [FAIL] Init failed.")
    return 0 if result > 0 else 1


def cmd_seed() -> int:
    """Download market data (requires network)."""
    from src.daily_updater import run_daily_update

    print("=" * 60)
    print("  PTCK — DATA SEEDING")
    print("=" * 60)
    print(f"  Data directory: {DATA_DIR}")
    print()
    run_daily_update(None)
    print("=" * 60)
    print("  Chạy sentinel.exe để khởi động hệ thống.")
    return 0


def cmd_run() -> None:
    """Start the server (default mode)."""
    from src.init_db import needs_init

    missing = needs_init(str(DATA_DIR))
    if missing:
        print(f"  ⚠️  {len(missing)} database(s) need initialization: {', '.join(missing)}")
        print("  Chạy '--init' trước, hoặc dùng 'python ptck.py db init'")
        print()
        # Auto-init if in dev mode (non-frozen)
        if not getattr(sys, "frozen", False):
            print("  → Tự động init schema...")
            cmd_init()
        else:
            print("  → Bỏ qua. Dùng --init để khởi tạo.")
            return

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print("🔧 Starting Sentinel Fortress...")
    check_cognitive_modules()
    print(f"📡 Binding to port {port}...")
    show_banner(port, DATA_DIR)

    if sys.platform == "win32":
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel Fortress — PTCK Market Cognition Terminal",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="run",
        choices=["init", "seed", "run"],
        help="init: create DB schemas | seed: download data | run: start server",
    )
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    args = parser.parse_args()

    if args.mode == "init":
        sys.exit(cmd_init())
    elif args.mode == "seed":
        sys.exit(cmd_seed())
    else:
        cmd_run()


if __name__ == "__main__":
    main()
