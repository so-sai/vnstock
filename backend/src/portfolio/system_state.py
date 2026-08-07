"""Persistence State Lock — reboot-safe system_state.json + TWAP context."""

import json
from datetime import UTC, datetime
from pathlib import Path

_STATE_PATH = Path(__file__).resolve().parents[3] / "backend" / "data" / "system_state.json"

_DEFAULT = {
    "version": 2,
    "portfolio_critical_lock": False,
    "total_stale_pct": 0.0,
    "escrow_balance": 0.0,
    "last_updated": None,
    "twap_execution_context": {
        "twap_resume_cursor": None,
        "last_known_good_network": None,
        "pending_slices_count": 0,
        "active_broker_order_id": None,
        "circuit_breaker_trips": 0,
        "slice_history": [],
    },
}


def set_lock_path(p: Path):
    global _STATE_PATH
    _STATE_PATH = p


def _read() -> dict:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        if "twap_execution_context" not in data:
            data["twap_execution_context"] = dict(_DEFAULT["twap_execution_context"])
        else:
            for k, v in _DEFAULT["twap_execution_context"].items():
                data["twap_execution_context"].setdefault(k, v)
        return data
    except FileNotFoundError, json.JSONDecodeError:
        return dict(_DEFAULT)


def _write(state: dict):
    state["last_updated"] = datetime.now(UTC).isoformat()
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


# ── TWAP Context ────────────────────────────────────────


def update_twap_context(ctx: dict):
    s = _read()
    s["twap_execution_context"].update(ctx)
    _write(s)


def get_twap_context() -> dict:
    return _read().get("twap_execution_context", dict(_DEFAULT["twap_execution_context"]))


def clear_twap_context():
    s = _read()
    s["twap_execution_context"] = dict(_DEFAULT["twap_execution_context"])
    _write(s)
