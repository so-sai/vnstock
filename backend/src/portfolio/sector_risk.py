"""
Sector Risk Shield v1.0
Prevents correlated sector concentration in the portfolio.
"""

import sys
from pathlib import Path


def _hydrate_path():
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
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

MAX_SECTOR_CAP = 30.0


def validate_sector(active_positions: list, target_sector: str, next_exposure_pct: float) -> tuple:
    current = sum(p.get("nav_exposure_pct", 0.0) for p in active_positions if p.get("sector") == target_sector)
    new_total = current + next_exposure_pct
    if new_total > MAX_SECTOR_CAP:
        return (False, f"Sector {target_sector} would reach {new_total:.1f}% (cap {MAX_SECTOR_CAP:.1f}%)")
    return (True, "Sector exposure within limit")
