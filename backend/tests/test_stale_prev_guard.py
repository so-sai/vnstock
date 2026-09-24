"""test_stale_prev_guard.py — TDD: cấm gán nhãn 1D khi prev ≠ T-1 HOSE.

Bug EOD 24/09/2026: daily_ohlcv thiếu 18/21/22/23/09 → LAG/pct_change trên
24/09 = Δ 17→24 (5 phiên) nhưng consumer vẫn label "1D" (VPB −21.6%…).

Cases frozen:
  C1. prev = T-1 thật (Thứ Hai ↔ Thứ Sáu) → PASS, return_1d có giá trị.
  C2. prev = 17/09, current = 24/09 → STALE_PREV_GAP, return_1d = NO_DATA,
      return_gap / multi_session_return tách schema, gap_label Δ_5D.
"""

from datetime import date

from src.database.stale_prev_guard import (
    FLAG_OK,
    FLAG_STALE_PREV,
    NO_DATA,
    check_prev_continuity,
    count_trading_sessions,
    expected_prev_trading_day,
    safe_return_1d,
)


class TestCase1TruePrevDay:
    """Friday close → Monday current: T-1 hợp lệ, tính 1D được."""

    def test_monday_prev_is_friday(self):
        assert expected_prev_trading_day(date(2026, 8, 24)) == date(2026, 8, 21)

    def test_continuity_ok(self):
        r = check_prev_continuity(date(2026, 8, 21), date(2026, 8, 24))
        assert r["flag"] == FLAG_OK
        assert r["sessions"] == 1
        assert r["gap_label"] is None
        assert r["expected_prev"] == "2026-08-21"

    def test_safe_return_1d_pass(self):
        out = safe_return_1d(
            prev_close=100.0,
            curr_close=102.0,
            prev_date="2026-08-21",
            current_date="2026-08-24",
        )
        assert out["flag"] == FLAG_OK
        assert out["return_1d"] == 2.0
        assert out["return_gap"] == 2.0
        assert out["multi_session_return"] is NO_DATA
        assert out["gap_label"] is None

    def test_string_dates_accepted(self):
        r = check_prev_continuity("2026-08-21", "2026-08-24")
        assert r["flag"] == FLAG_OK


class TestCase2MultiSessionGap17to24:
    """Data-gap 17→24 (thiếu 18,21,22,23): return_1d phải NO_DATA."""

    def test_expected_prev_for_24th_is_23rd(self):
        assert expected_prev_trading_day(date(2026, 9, 24)) == date(2026, 9, 23)

    def test_continuity_stale(self):
        r = check_prev_continuity(date(2026, 9, 17), date(2026, 9, 24))
        assert r["flag"] == FLAG_STALE_PREV
        assert r["expected_prev"] == "2026-09-23"
        assert r["prev_date"] == "2026-09-17"
        assert r["sessions"] == 5
        assert r["gap_label"] == "Δ_5D (2026-09-17->2026-09-24)"

    def test_return_1d_no_data_gap_kept_separate(self):
        # VPB-like: 17→24 multi-session must not be labeled 1D
        out = safe_return_1d(
            prev_close=23_000.0,
            curr_close=18_024.0,
            prev_date=date(2026, 9, 17),
            current_date=date(2026, 9, 24),
        )
        assert out["flag"] == FLAG_STALE_PREV
        assert out["return_1d"] is NO_DATA
        assert out["return_gap"] is not None
        assert out["multi_session_return"] == out["return_gap"]
        assert out["gap_label"] == "Δ_5D (2026-09-17->2026-09-24)"
        assert out["sessions"] == 5

    def test_count_sessions_gap_is_five(self):
        assert count_trading_sessions(date(2026, 9, 17), date(2026, 9, 24)) == 5


class TestCaseEdge:
    def test_missing_prev_flagged(self):
        out = safe_return_1d(None, 100.0, None, "2026-09-24")
        assert out["flag"] == FLAG_STALE_PREV
        assert out["return_1d"] is NO_DATA
        assert out["gap_label"] is not None

    def test_nonpositive_prev_close_flagged(self):
        out = safe_return_1d(0.0, 100.0, "2026-09-23", "2026-09-24")
        assert out["flag"] == FLAG_STALE_PREV
        assert out["return_1d"] is NO_DATA

    def test_calendar_skips_weekend_only(self):
        # 18/09 (Fri) prev must be 17/09 (Thu)
        assert expected_prev_trading_day(date(2026, 9, 18)) == date(2026, 9, 17)
        # 21/09 (Mon) prev must be 18/09 (Fri)
        assert expected_prev_trading_day(date(2026, 9, 21)) == date(2026, 9, 18)
