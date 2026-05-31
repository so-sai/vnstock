import sys
import socket
import webbrowser
from pathlib import Path

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


def show_banner(port: int) -> None:
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     SENTINEL MARKET COGNITION TERMINAL    ║")
    print("  ║          Alpha — Local Execution          ║")
    print("  ╠══════════════════════════════════════════╣")
    print(f"  ║  Serving on http://127.0.0.1:{port:<5}          ║")
    print("  ║  DecisionView schema: v1.0.0             ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  Hệ thống hỗ trợ ra quyết định – không phải khuyến nghị đầu tư.")
    print()


def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print("🔧 Starting Sentinel Fortress...")
    check_cognitive_modules()
    print(f"📡 Binding to port {port}...")
    show_banner(port)

    if sys.platform == "win32":
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
