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


def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print("🔧 Starting Sentinel Fortress... ")
    print(f"📡 Serving on {url}")

    if sys.platform == "win32":
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
