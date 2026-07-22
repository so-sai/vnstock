"""
Static IPO seed / migration for the Sentinel IPO HUD.

This one-time migration seeds the existing screener_cache.db with a small
realistic IPO sample set so the IPO HUD can render genuine structure signals.
"""

import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / 'AGENTS.md').exists() and (current / 'backend').is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))

    backend_dir = root_path / 'backend'
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))

    return root_path


_PROJECT_ROOT = _hydrate_path()

from src.database.timeline_manager import seed_static_ipo_calendar


def main() -> None:
    inserted = seed_static_ipo_calendar()
    print(f"[IPO SEED] inserted={inserted}")


if __name__ == '__main__':
    main()
