"""Persistence State Lock — reboot-safe system_state.json.

Các cờ khóa cứng được ghi vào `backend/data/system_state.json`.
Reboot laptop KHÔNG giải phóng lock — chỉ StalePositionManager mới được quyền.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_STATE_PATH = Path(__file__).resolve().parents[3] / "backend" / "data" / "system_state.json"


def _lock_path() -> Path:
    global _STATE_PATH
    return _STATE_PATH


def set_lock_path(p: Path):
    global _STATE_PATH
    _STATE_PATH = p


def _read() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "version": 1,
            "portfolio_critical_lock": False,
            "total_stale_pct": 0.0,
            "escrow_balance": 0.0,
            "last_updated": None,
        }


def _write(state: dict):
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_locked() -> bool:
    return _read().get("portfolio_critical_lock", False)


def lock(total_stale_pct: float):
    s = _read()
    s["portfolio_critical_lock"] = True
    s["total_stale_pct"] = total_stale_pct
    _write(s)


def unlock():
    s = _read()
    s["portfolio_critical_lock"] = False
    s["total_stale_pct"] = 0.0
    _write(s)


def get_state() -> dict:
    s = _read()
    s["locked"] = s.get("portfolio_critical_lock", False)
    return s


def update_escrow(balance: float):
    s = _read()
    s["escrow_balance"] = balance
    _write(s)
