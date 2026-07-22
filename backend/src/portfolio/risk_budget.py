"""
Risk Budget Controller v1.0
Contextual exposure caps and Portfolio Heat gatekeeper.
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

EXPOSURE_CAPS = {
    "CRISIS":   10.0,
    "RECOVERY": 35.0,
    "RANGING":  50.0,
    "TRENDING": 100.0,
}

MAX_PORTFOLIO_HEAT = 10.0

def evaluate_gatekeeper(current_regime: str, current_net_exposure: float,
                         current_portfolio_heat: float,
                         next_position_exposure: float) -> tuple:
    max_allowed = EXPOSURE_CAPS.get(current_regime, 0.0)
    new_total = current_net_exposure + next_position_exposure

    if new_total > max_allowed:
        return (False, f"Exposure {new_total:.1f}% exceeds regime cap {max_allowed:.1f}%")

    if current_portfolio_heat >= MAX_PORTFOLIO_HEAT:
        return (False, f"Portfolio heat {current_portfolio_heat:.1f}% at max {MAX_PORTFOLIO_HEAT:.1f}%")

    return (True, "Gatekeeper approved")
