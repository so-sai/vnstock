"""Persistence State Lock — reboot-safe system_state.json + TWAP context."""

import json
from datetime import UTC, datetime
from pathlib import Path

_STATE_PATH = Path(__file__).resolve().parents[3] / "backend" / "data" / "system_state.json"

_DEFAULT = {
    "version": 2,
    # Fail-closed: mất state / lỗi đọc file JSON → KHÓA chặt (không bao giờ fail-open).
    "portfolio_critical_lock": True,
    # [WHY] Vết lý do của lần KHOÁ gần nhất — để phân biệt "khoá do kẹt vốn" (stale manager) với
    # "khoá do chính sách" (lớp phủ 07:00 re-assert). Không phải cờ điều khiển, chỉ để kiểm toán.
    "last_lock_reason": None,
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
    return _read().get("portfolio_critical_lock", True)


def lock(total_stale_pct: float = 0.0, reason: str | None = None):
    """Đặt `portfolio_critical_lock = True` (fail-closed) và ghi vết `reason`.

    [WHY] `reason` cần cho LỚP PHỦ chính sách: `stale_manager` tự `unlock()` khi stale% dưới ngưỡng
    (dòng 154-156), nên chính sách 100% tiền mặt phải re-assert mỗi sáng trước ATO. Không có `reason`
    thì không phân biệt được khoá do kẹt vốn với khoá do chính sách. Tham số có default để mọi caller
    cũ (`lock(0.0)`) không vỡ.
    """
    s = _read()
    s["portfolio_critical_lock"] = True
    s["total_stale_pct"] = total_stale_pct
    if reason:
        s["last_lock_reason"] = reason
    _write(s)


def unlock():
    s = _read()
    s["portfolio_critical_lock"] = False
    s["total_stale_pct"] = 0.0
    _write(s)


def get_state() -> dict:
    s = _read()
    s["locked"] = s.get("portfolio_critical_lock", True)
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
