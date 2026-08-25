"""test_session_resolver.py — Canonical market session date resolution (Patch C-1).

Root cause đã phát hiện 23/08 (Chủ nhật): CLI mặc định query date=hôm nay
-> DB không có phiên -> Data Quality Guard phát alert giả "0 mã đủ thanh
khoảng" dù DB hoàn toàn khỏe (350 mã vol>50k phiên 21/08).

Invariants (spec-frozen: backend/src/research/session_resolver_spec_note.md):
  I1. DATA_STALE ≠ PREVIOUS_TRADING_SESSION:
      - Non-trading day + data tồn tại  -> PREVIOUS_TRADING_SESSION
      - Trading day + data thiếu       -> DATA_STALE (KHÔNG silent fallback)
  I2. Explicit --date không bao giờ bị silent override — result luôn giữ
      requested_date + resolution + reason.
  I3. Resolver quyết định NGÀY trước; Data Quality Guard giữ nguyên nhiệm vụ
      (phát hiện dữ liệu bất thường trên ngày ĐÃ resolve).
"""

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BACKEND = ROOT / "backend"
for _p in [str(BACKEND / "src"), str(BACKEND), str(ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.core.session_resolver import (  # noqa: E402
    GATE_DATA_STALE,
    GATE_NO_DATA,
    GATE_PREVIOUS_TRADING_SESSION,
    GATE_SAME_SESSION,
    SessionResolution,
    resolve_session,
)
from src.services.macro.time_series_aligner import VNSessionCalendar  # noqa: E402


# 2026-08-21 = Thứ Sáu (phiên thật trong DB), 22/08 T7, 23/08 CN.
class TestNonTradingDayFallback:
    def test_sunday_resolves_to_friday(self):
        res = resolve_session("2026-08-23", latest_available_date="2026-08-21")
        assert res.resolution == GATE_PREVIOUS_TRADING_SESSION
        assert res.effective_date == "2026-08-21"
        assert res.requested_date == "2026-08-23"
        assert res.latest_available_date == "2026-08-21"
        assert res.reason in ("weekend", "holiday")

    def test_saturday_resolves_to_friday(self):
        res = resolve_session("2026-08-22", latest_available_date="2026-08-21")
        assert res.resolution == GATE_PREVIOUS_TRADING_SESSION
        assert res.effective_date == "2026-08-21"
        assert res.reason == "weekend"

    def test_vn_holiday_resolves_back(self):
        # 02/09/2026 (Quốc khánh) — Thứ Tư, là ngày nghỉ VN.
        cal = VNSessionCalendar()
        if cal.is_trading_day(date(2026, 9, 2)):
            pytest.skip("weekend_holidays.json chưa có 02/09 — không test được")
        res = resolve_session("2026-09-02", latest_available_date="2026-09-01")
        assert res.resolution == GATE_PREVIOUS_TRADING_SESSION
        assert res.effective_date == "2026-09-01"
        assert res.reason == "holiday"


class TestTradingDay:
    def test_data_available_same_session(self):
        res = resolve_session("2026-08-21", latest_available_date="2026-08-21")
        assert res.resolution == GATE_SAME_SESSION
        assert res.effective_date == "2026-08-21"
        assert res.reason == "ok"

    def test_data_missing_is_data_stale_not_silent_fallback(self):
        """I1: Thứ 5 + dữ liệu thứ 5 thiếu = DATA_STALE — KHÔNG được lùi ngày
        im lặng như non-trading day."""
        res = resolve_session("2026-08-20", latest_available_date="2026-08-19")
        assert res.resolution == GATE_DATA_STALE
        # effective_date = latest có thật (engine chạy được) NHƯNG cờ rõ ràng
        assert res.effective_date == "2026-08-19"
        assert res.requested_date == "2026-08-20"
        assert res.reason == "ingestion_pending"

    def test_allow_fallback_false_raises_on_stale(self):
        with pytest.raises(RuntimeError, match="DATA_STALE"):
            resolve_session(
                "2026-08-20", latest_available_date="2026-08-19", allow_fallback=False
            )

    def test_no_data_at_all(self):
        with pytest.raises(RuntimeError, match="NO_DATA"):
            resolve_session("2026-08-21", latest_available_date=None)


class TestExplicitDateProvenance:
    def test_explicit_non_trading_date_keeps_requested_visible(self):
        """I2: explicit --date vẫn resolve nhưng requested_date phải luôn
        nguyên vẹn trong result — caller in provenance."""
        res = resolve_session("2026-08-22", latest_available_date="2026-08-21")
        assert res.requested_date == "2026-08-22"
        assert res.effective_date == "2026-08-21"
        assert res.resolution != GATE_SAME_SESSION

    def test_resolution_dataclass_frozen(self):
        """Result là semantic record bất biến — không caller nào sửa được."""
        res = resolve_session("2026-08-23", latest_available_date="2026-08-21")
        with pytest.raises(Exception):
            res.resolution = GATE_SAME_SESSION


class TestCalendarPrevTradingDay:
    def test_prev_trading_day_exists_on_vn_calendar(self):
        cal = VNSessionCalendar()
        assert cal.prev_trading_day(date(2026, 8, 23)) == date(2026, 8, 21)
        assert cal.prev_trading_day(date(2026, 8, 21)) == date(2026, 8, 20)

    def test_is_trading_day_weekend(self):
        cal = VNSessionCalendar()
        assert cal.is_trading_day(date(2026, 8, 22)) is False
        assert cal.is_trading_day(date(2026, 8, 21)) is True
