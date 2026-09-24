"""Paper watchlist bid state — explicit invalidation freeze (not live orders).

Zero-hallucination: status is evidence of close vs hard levels only.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def _hydrate_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    root = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root = current
            break
        current = current.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    backend_dir = root / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root


PROJECT_ROOT = _hydrate_path()
STATE_PATH = Path(str(PROJECT_ROOT)) / "backend" / "data" / "paper_watch_state.json"

DEFAULT_STATE = {
    "version": 1,
    "mode": "PAPER",
    "entry_lock": True,
    "cash_pct": 100.0,
    "as_of": None,
    "bids": [
        {
            "symbol": "PNJ",
            "status": "ACTIVE",
            "invalidation": 35275.0,
            "hard_drop_below": 35275.0,
            "last_close": None,
            "reason": None,
            "updated": None,
        },
        {
            "symbol": "HPG",
            "status": "ACTIVE",
            "invalidation": 20800.0,
            "hard_drop_below": 20800.0,
            "last_close": None,
            "reason": None,
            "updated": None,
        },
    ],
}


def load_paper_watch(path: Path | None = None) -> dict:
    p = path or STATE_PATH
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("bids"):
                return data
        except OSError, ValueError, TypeError:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_paper_watch(state: dict, path: Path | None = None) -> bool:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True
    except OSError, TypeError, ValueError:
        return False


def apply_eod_invalidation(state: dict, closes: dict[str, float], as_of: str | None = None) -> dict:
    """Mark DROPPED / RED_ALERT from same-day closes vs hard levels."""
    day = as_of or datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    for bid in state.get("bids", []):
        sym = bid.get("symbol")
        if sym not in closes or closes[sym] is None:
            continue
        close = float(closes[sym])
        hard = float(bid.get("hard_drop_below") or bid.get("invalidation") or 0.0)
        bid["last_close"] = close
        bid["updated"] = now
        if close < hard:
            bid["status"] = "DROPPED"
            bid["reason"] = f"close {close:.0f} < hard {hard:.0f} ({day})"
        elif close == hard:
            bid["status"] = "RED_ALERT"
            bid["reason"] = f"close {close:.0f} == hard line {hard:.0f} ({day}); drop next bar < {hard:.0f}"
        else:
            if bid.get("status") != "DROPPED":
                bid["status"] = "ACTIVE"
                bid["reason"] = f"close {close:.0f} above hard {hard:.0f} ({day})"
    state["as_of"] = day
    state["entry_lock"] = True
    state["cash_pct"] = 100.0
    return state


def freeze_from_eod(closes: dict[str, float], as_of: str | None = None) -> dict:
    state = load_paper_watch()
    state = apply_eod_invalidation(state, closes, as_of=as_of)
    save_paper_watch(state)
    return state
