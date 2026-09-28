# -*- coding: utf-8 -*-
"""Tests — 4 lỗi EOD pipeline phát hiện 2026-09-25 (E1..E4).

E1  daily_updater bơm row close=0/volume=0 (KBS price_board trả cả mã
    không giao dịch) → ô nhiễm pct_change / MA20 / advance-decline.
E2  breadth_engine ghi market_pulse.json vào PROJECT_ROOT/data/output,
    market_state_coordinator đọc backend/data/output → breadth_state trễ T-1.
E3  freeze_from_eod() không có caller → paper_watch_state.json đóng băng.
E4a update_beta_posteriors(days=) ≠ chữ ký days_back → TypeError mỗi daily-close.
E4b market_wide_breadth --end hardcode "2026-09-11" → bảng đóng băng tại 11/09.
"""

import inspect
import sys
from pathlib import Path

# ── Path Setup ──────────────────────────────────────────────────
PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ===================================================================
# E1 — ZERO-ROW GUARD
# ===================================================================


class TestE1ZeroRowGuard:
    def test_drops_zero_null_and_negative_close(self):
        import pandas as pd

        from src.daily_updater import drop_untraded_rows

        df = pd.DataFrame(
            {
                "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
                "close": [15000.0, 0.0, None, -100.0, 20650.0],
                "volume": [100, 0, 0, 0, 14623900],
            }
        )
        out = drop_untraded_rows(df)
        assert list(out["symbol"]) == ["AAA", "EEE"], "chỉ giữ mã có close > 0"

    def test_empty_df_passthrough(self):
        import pandas as pd

        from src.daily_updater import drop_untraded_rows

        df = pd.DataFrame({"symbol": [], "close": []})
        assert drop_untraded_rows(df).empty

    def test_missing_close_column_passthrough(self):
        import pandas as pd

        from src.daily_updater import drop_untraded_rows

        df = pd.DataFrame({"symbol": ["AAA"], "volume": [100]})
        assert len(drop_untraded_rows(df)) == 1

    def test_idempotent(self):
        import pandas as pd

        from src.daily_updater import drop_untraded_rows

        df = pd.DataFrame({"symbol": ["AAA", "BBB"], "close": [1000.0, 0.0]})
        once = drop_untraded_rows(df)
        twice = drop_untraded_rows(once)
        assert list(once["symbol"]) == list(twice["symbol"]) == ["AAA"]

    def test_string_close_coerced(self):
        import pandas as pd

        from src.daily_updater import drop_untraded_rows

        df = pd.DataFrame({"symbol": ["AAA", "BBB"], "close": ["20650", "0"]})
        assert list(drop_untraded_rows(df)["symbol"]) == ["AAA"]

    def test_market_wide_breadth_ignores_untraded(self):
        from src.analysis.market_wide_breadth import compute_day

        rec = compute_day(
            {"AAA": (100.0, 1000.0), "ZERO": (0.0, 0.0), "NULLP": (None, 500.0)},
            {"AAA": 95.0, "ZERO": 100.0, "NULLP": 90.0},
        )
        assert rec["total_active"] == 1, "mã close=0/NULL không được đếm vào breadth"
        assert rec["advances"] == 1


# ===================================================================
# E2 — market_pulse.json writer == reader
# ===================================================================


class TestE2PulseOutputPath:
    def test_writer_path_equals_reader_path(self):
        from src.engine.breadth_engine import PULSE_OUTPUT_DIR
        from src.core.market_state_coordinator import OUTPUT_DIR

        writer = Path(PULSE_OUTPUT_DIR) / "market_pulse.json"
        reader = Path(OUTPUT_DIR) / "market_pulse.json"
        assert writer == reader, (
            "breadth_engine ghi sai thư mục → market_state.breadth_state trễ T-1"
        )

    def test_writer_path_is_under_data_dir(self):
        import conftest
        import src.config
        from src.engine.breadth_engine import PULSE_OUTPUT_DIR

        expected = Path(str(src.config.DATA_DIR)) / "output"
        shadow = conftest.sandbox_shadow()
        if shadow is not None:
            expected = shadow / "output"
        assert Path(PULSE_OUTPUT_DIR) == expected

    def test_not_written_to_legacy_root_output(self):
        from src.engine import breadth_engine

        src_text = inspect.getsource(breadth_engine)
        assert 'os.path.join(src.config.PROJECT_ROOT, "data", "output")' not in src_text


# ===================================================================
# E3 — daily_closer wires freeze_from_eod
# ===================================================================


class TestE3PaperWatchWiring:
    def test_closer_calls_freeze_from_eod(self):
        from src import daily_closer

        src_text = inspect.getsource(daily_closer)
        assert "freeze_from_eod" in src_text, "daily_closer phải gọi freeze_from_eod (E3)"

    def test_closer_freeze_step_labelled(self):
        from src import daily_closer

        src_text = inspect.getsource(daily_closer)
        assert "Step 4.2" in src_text

    def test_freeze_reads_only_positive_close(self):
        from src import daily_closer

        src_text = inspect.getsource(daily_closer)
        assert "close > 0" in src_text, "không được nạp close<=0 vào freeze"


# ===================================================================
# E4a — update_beta_posteriors kwarg
# ===================================================================


class TestE4aBetaPosteriorKwarg:
    def test_signature_uses_days_back(self):
        from src.calibration.calibrator import update_beta_posteriors

        params = inspect.signature(update_beta_posteriors).parameters
        assert "days_back" in params
        assert "days" not in params

    def test_call_site_uses_days_back(self):
        from src.calibration.calibrator import resolve_pending_outcomes

        src_text = inspect.getsource(resolve_pending_outcomes)
        assert "update_beta_posteriors(days=" not in src_text, "kwarg sai đã gây TypeError"
        assert "update_beta_posteriors(days_back=" in src_text

    def test_call_site_accepts_lookback(self):
        from src.calibration.calibrator import resolve_pending_outcomes

        assert "lookback_days" in inspect.signature(resolve_pending_outcomes).parameters

    def test_old_kwarg_raises_and_new_kwarg_binds(self):
        """Runtime proof: bind() kwarg cũ (days=) TypeError, kwarg mới OK."""
        from src.calibration.calibrator import update_beta_posteriors

        sig = inspect.signature(update_beta_posteriors)
        sig.bind(days_back=90)  # không được raise
        try:
            sig.bind(days=90)
        except TypeError:
            pass
        else:
            raise AssertionError("kwarg 'days' không còn TypeError — chữ ký đã đổi?")


# ===================================================================
# E4b — market_wide_breadth --end
# ===================================================================


class TestE4bMarketWideBreadthEnd:
    def test_end_default_not_hardcoded(self):
        from src.analysis import market_wide_breadth as mwb

        assert 'default="2026-09-11"' not in inspect.getsource(mwb.main), (
            "--end hardcode làm bảng đóng băng tại 11/09"
        )

    def test_latest_db_date_returns_iso_or_none(self):
        from src.analysis.market_wide_breadth import latest_db_date

        d = latest_db_date()
        assert d is None or (len(d) == 10 and d[4] == "-" and d[7] == "-")

    def test_closer_refreshes_market_wide_breadth(self):
        from src import daily_closer

        src_text = inspect.getsource(daily_closer)
        assert "market_wide_breadth" in src_text, "daily_closer phải refresh breadth table (E4b)"

    def test_backfill_is_idempotent_for_same_date(self):
        from src.analysis.market_wide_breadth import compute_day

        a = compute_day({"AAA": (100.0, 10.0)}, {"AAA": 90.0})
        b = compute_day({"AAA": (100.0, 10.0)}, {"AAA": 90.0})
        assert a == b
