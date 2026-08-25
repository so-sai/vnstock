"""session_resolver.py — Canonical Market Session Date Resolution (Patch C-1).

Root cause (audit 23/08, Chủ nhật): CLI mặc định query date=hôm nay -> DB
không có phiên -> Data Quality Guard phát alert giả "0 mã đủ thanh khoản"
dù DB hoàn toàn khỏe. `No trading session ≠ Missing market data`.

Spec-frozen: backend/src/research/session_resolver_spec_note.md
Invariants:
  I1. DATA_STALE ≠ PREVIOUS_TRADING_SESSION — calendar fallback KHÔNG được
      che giấu ingestion failure.
  I2. requested_date luôn nguyên vẹn trong result (explicit --date không bị
      silent override; caller in provenance Requested/Data-as-of/Resolution).
  I3. Resolver chỉ quyết định NGÀY — Data Quality Guard giữ nguyên nhiệm vụ.

Nguồn sự thật: VNSessionCalendar (time_series_aligner) + MAX(date) daily_ohlcv.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

GATE_SAME_SESSION = "SAME_SESSION"
GATE_PREVIOUS_TRADING_SESSION = "PREVIOUS_TRADING_SESSION"
GATE_DATA_STALE = "DATA_STALE"
GATE_NO_DATA = "NO_DATA"

# Sentinel phân biệt "caller không truyền" (-> query DB thật) với
# "caller truyền None tường minh" (-> DB trống, NO_DATA).
_UNSET = object()


@dataclass(frozen=True)
class SessionResolution:
    """Semantic result bất biến — caller in provenance từ các trường này."""

    requested_date: str
    effective_date: str
    latest_available_date: str | None
    resolution: str
    reason: str


def _default_latest_available() -> str | None:
    """MAX(date) từ daily_ohlcv — lazy import để test không cần DB."""
    _candidate = Path(__file__).resolve().parent.parent.parent
    while _candidate != _candidate.parent:
        if (_candidate / "AGENTS.md").exists() and (_candidate / "backend").is_dir():
            break
        _candidate = _candidate.parent
    db = _candidate / "backend" / "data" / "screener_cache.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def resolve_session(
    requested_date: str | None = None,
    *,
    latest_available_date: object = _UNSET,
    allow_fallback: bool = True,
    calendar: object | None = None,
) -> SessionResolution:
    """Resolve requested_date -> effective market session.

    requested_date None -> hôm nay (lệnh CLI không truyền --date).
    latest_available_date None -> query DB thật (MAX date daily_ohlcv).
    allow_fallback=False: DATA_STALE raise thay vì trả effective khác requested
    (dùng khi caller muốn xử lý ingestion failure riêng).
    """
    from src.services.macro.time_series_aligner import VNSessionCalendar

    cal = calendar or VNSessionCalendar()

    req: date
    if requested_date is None:
        req = date.today()
    elif isinstance(requested_date, str):
        req = date.fromisoformat(requested_date)
    elif isinstance(requested_date, datetime):
        req = requested_date.date()
    else:
        req = requested_date
    req_str = req.isoformat()

    if latest_available_date is _UNSET:
        latest = _default_latest_available()
    else:
        latest = latest_available_date  # type: ignore[assignment]  # may be None -> NO_DATA

    # ── Non-trading day: fallback calendar ĐƯỢC PHÉP (không có phiên để thiếu) ──
    if not cal.is_trading_day(req):
        prev = cal.prev_trading_day(req)
        reason = "weekend" if req.weekday() >= 5 else "holiday"
        return SessionResolution(
            requested_date=req_str,
            effective_date=prev.isoformat(),
            latest_available_date=latest,
            resolution=GATE_PREVIOUS_TRADING_SESSION,
            reason=reason,
        )

    # ── Trading day ──
    if latest is None:
        raise RuntimeError(f"NO_DATA: daily_ohlcv trống — không resolve được session cho {req_str}")

    if latest >= req_str:
        return SessionResolution(
            requested_date=req_str,
            effective_date=req_str,
            latest_available_date=latest,
            resolution=GATE_SAME_SESSION,
            reason="ok",
        )

    # Trading day nhưng data chưa về — DATA_STALE, KHÔNG silent fallback (I1).
    if not allow_fallback:
        raise RuntimeError(
            f"DATA_STALE: requested {req_str} nhưng latest data chỉ đến {latest} (ingestion_pending) — allow_fallback=False"
        )
    return SessionResolution(
        requested_date=req_str,
        effective_date=latest,
        latest_available_date=latest,
        resolution=GATE_DATA_STALE,
        reason="ingestion_pending",
    )


def format_provenance(res: SessionResolution) -> str:
    """Dòng provenance chuẩn cho CLI output (I2)."""
    marker = "" if res.resolution == GATE_SAME_SESSION else "  ℹ️ "
    return (
        f"{marker}Requested date: {res.requested_date} | "
        f"Data as of: {res.effective_date} | "
        f"Resolution: {res.resolution} ({res.reason})"
    )
