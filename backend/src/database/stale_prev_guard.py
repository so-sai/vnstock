"""stale_prev_guard.py — Chống gán nhãn "1D" khi prev_close ≠ T-1 phiên.

Bug 24/09/2026: daily_ohlcv market-wide thiếu 18/21/22/23/09 → `pct_change`
/ `LAG(close)` trên 24/09 trả Δ lũy kế 17→24 (5 phiên) nhưng consumer vẫn
gọi là return_1D → VPB −21.6%, breadth "88 mã ≤−5%" sai provenance.

Zero-Hallucination rule:
  - Không có phiên T-1 thật → return_1d = NO_DATA (None), cờ STALE_PREV_GAP.
  - Delta nhiều phiên phải tách schema: return_gap / multi_session_return
    (vd "Δ_5D (17→24)"), cấm gán nhãn "1D".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.services.macro.time_series_aligner import VNSessionCalendar

FLAG_OK = "OK"
FLAG_STALE_PREV = "STALE_PREV_GAP"
NO_DATA = None

_CAL = VNSessionCalendar()


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"unsupported date type: {type(value)!r}")


def expected_prev_trading_day(current_date: date | datetime | str) -> date:
    """Phiên HOSE hợp lệ gần nhất TRƯỚC current_date (T-1 lịch)."""
    cur = _as_date(current_date)
    if cur is None:
        raise ValueError("current_date is required")
    return _CAL.prev_trading_day(cur)


def count_trading_sessions(prev_date: date | datetime | str, current_date: date | datetime | str) -> int:
    """Số phiên giao dịch mở rộng từ prev_date (loại) đến current_date (bao gồm).

    prev=T-1 thật → 1. Gap 17→24 → 5 (18,21,22,23,24).
    """
    prev = _as_date(prev_date)
    cur = _as_date(current_date)
    if prev is None or cur is None:
        return 0
    if cur <= prev:
        return 0
    n = 0
    d = prev + timedelta(days=1)
    while d <= cur:
        if _CAL.is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def check_prev_continuity(
    prev_date: date | datetime | str | None,
    current_date: date | datetime | str,
) -> dict[str, Any]:
    """Soi prev_date với T-1 lịch HOSE.

    Returns
    -------
    {
      "flag": FLAG_OK | FLAG_STALE_PREV,
      "expected_prev": "YYYY-MM-DD",
      "prev_date": "YYYY-MM-DD" | None,
      "current_date": "YYYY-MM-DD",
      "sessions": int,          # 1 = true 1D; >1 = multi-session gap
      "gap_label": None | "Δ_{N}D ({prev}->{cur})",
    }
    """
    cur = _as_date(current_date)
    if cur is None:
        raise ValueError("current_date is required")
    prev = _as_date(prev_date)
    expected = expected_prev_trading_day(cur)

    result: dict[str, Any] = {
        "flag": FLAG_OK,
        "expected_prev": expected.isoformat(),
        "prev_date": prev.isoformat() if prev else None,
        "current_date": cur.isoformat(),
        "sessions": 0,
        "gap_label": None,
    }

    if prev is None:
        result["flag"] = FLAG_STALE_PREV
        result["gap_label"] = f"MISSING_PREV (None->{cur.isoformat()})"
        return result

    if prev == expected:
        result["sessions"] = count_trading_sessions(prev, cur)
        if result["sessions"] <= 1:
            result["sessions"] = 1
            return result

    result["flag"] = FLAG_STALE_PREV
    result["sessions"] = count_trading_sessions(prev, cur)
    result["gap_label"] = f"Δ_{result['sessions']}D ({prev.isoformat()}->{cur.isoformat()})"
    return result


def safe_return_1d(
    prev_close: float | None,
    curr_close: float | None,
    prev_date: date | datetime | str | None,
    current_date: date | datetime | str,
) -> dict[str, Any]:
    """Trả return_1d chỉ khi prev là T-1 thật; ngược lại NO_DATA + return_gap.

    Schema tách bạch:
      return_1d: float | None   — % 1 phiên HOSE (None khi STALE_PREV)
      return_gap: float | None  — % delta thực tế prev→curr (có thể multi-session)
      multi_session_return: float | None — alias return_gap khi sessions>1
      flag / gap_label / sessions — provenance
    """
    continuity = check_prev_continuity(prev_date, current_date)
    out: dict[str, Any] = {
        "flag": continuity["flag"],
        "gap_label": continuity["gap_label"],
        "sessions": continuity["sessions"],
        "expected_prev": continuity["expected_prev"],
        "prev_date": continuity["prev_date"],
        "current_date": continuity["current_date"],
        "return_1d": NO_DATA,
        "return_gap": NO_DATA,
        "multi_session_return": NO_DATA,
    }

    if prev_close is None or curr_close is None:
        out["flag"] = FLAG_STALE_PREV
        if out["gap_label"] is None:
            out["gap_label"] = f"MISSING_CLOSE ({out['prev_date']}->{out['current_date']})"
        return out
    if prev_close <= 0 or curr_close is None:
        out["flag"] = FLAG_STALE_PREV
        if out["gap_label"] is None:
            out["gap_label"] = f"INVALID_CLOSE ({out['prev_date']}->{out['current_date']})"
        return out

    pct = (float(curr_close) - float(prev_close)) / float(prev_close) * 100.0
    out["return_gap"] = round(pct, 4)

    if continuity["flag"] == FLAG_OK and continuity["sessions"] == 1:
        out["return_1d"] = round(pct, 4)
    else:
        out["multi_session_return"] = round(pct, 4)
        out["return_1d"] = NO_DATA

    return out
