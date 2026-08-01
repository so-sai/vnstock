"""Tests — CSI scan batch + Market Pulse last_trading_day fallback.

Covers new features added in the same session:
1. csi_explain.scan_all() — batch CSI matrix with liquidity filter
2. csi_explain._liquid_symbols() — Vol20D filter from daily_ohlcv
3. breadth_engine.run_breadth_analysis() — weekend/holiday fallback to
   last trading day (no more "Volume > 50,000 = 0" garbage warning)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path Setup ──────────────────────────────────────────────────
PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import pytest


# ===================================================================
# 1. csi_explain.scan_all — batch CSI matrix
# ===================================================================

class TestScanAll:
    def test_scan_all_returns_compressed_rows(self):
        import src.governor.csi_explain as mod
        from src.governor.csi_explain import scan_all

        rows = [
            {"symbol": "VCB", "csi": {"p_gain": 0.37, "action": "REDUCE",
                                      "mos": 49.5, "market_context": "X"},
             "date": "2026-08-01", "archetype": "FRANCHISE_BANK",
             "entropy": {"csi_confidence": 0.72}},
            {"symbol": "HPG", "csi": {"p_gain": 0.28, "action": "VETO",
                                      "mos": None, "market_context": "Y"},
             "date": "2026-08-01", "archetype": "CYCLICAL_HEAVY",
             "entropy": {"csi_confidence": 0.68}},
        ]

        with patch.object(mod, "CSIExplainEngine") as MockEngine:
            MockEngine().explain.side_effect = rows
            result = scan_all(symbols=["VCB", "HPG"], min_vol=100_000)

        assert len(result) == 2
        assert result[0]["symbol"] == "VCB"
        assert result[0]["csi_p_gain"] == 0.37
        assert result[0]["action"] == "REDUCE"
        assert result[0]["mos"] == 49.5
        assert result[0]["csi_confidence"] == 0.72
        assert result[1]["action"] == "VETO"

    def test_scan_all_exception_becomes_nan_row(self):
        import src.governor.csi_explain as mod
        from src.governor.csi_explain import scan_all

        with patch.object(mod, "CSIExplainEngine") as MockEngine:
            MockEngine().explain.side_effect = RuntimeError("Governor no data")
            result = scan_all(symbols=["XXX"], min_vol=100_000)

        assert len(result) == 1
        assert result[0]["symbol"] == "XXX"
        assert result[0]["csi_p_gain"] is None
        assert result[0]["action"] == "N/A"

    def test_scan_all_empty_symbols_returns_empty(self):
        import src.governor.csi_explain as mod
        from src.governor.csi_explain import scan_all

        with patch.object(mod, "_liquid_symbols", return_value=[]):
            result = scan_all(symbols=None, min_vol=100_000)
        assert result == []

    def test_scan_all_progress_callback(self):
        import src.governor.csi_explain as mod
        from src.governor.csi_explain import scan_all

        calls = []
        with patch.object(mod, "CSIExplainEngine") as MockEngine:
            MockEngine().explain.side_effect = [
                {"symbol": "A", "csi": {"p_gain": 0.5, "action": "REDUCE",
                                        "mos": 0, "market_context": "X"},
                 "date": "2026-08-01", "archetype": "A", "entropy": {"csi_confidence": 0.5}},
                {"symbol": "B", "csi": {"p_gain": 0.4, "action": "REDUCE",
                                        "mos": 0, "market_context": "X"},
                 "date": "2026-08-01", "archetype": "A", "entropy": {"csi_confidence": 0.5}},
            ]
            scan_all(symbols=["A", "B"], progress_cb=lambda i, t, s: calls.append((i, t, s)))

        assert calls == [(1, 2, "A"), (2, 2, "B")]


# ===================================================================
# 2. csi_explain._liquid_symbols — Vol20D filter
# ===================================================================

class TestLiquidSymbols:
    def _df(self):
        import pandas as pd
        # 3 symbols, 30 sessions, volumes 150k / 60k / 5k
        import numpy as np
        dates = pd.bdate_range(end="2026-07-31", periods=30)
        rows = []
        for sym, vol in (("AAA", 150_000), ("BBB", 60_000), ("CCC", 5_000)):
            for d in dates:
                rows.append({"symbol": sym, "date": d, "volume": vol})
        return pd.DataFrame(rows)

    def test_liquid_symbols_filters_by_vol20d(self):
        import pandas as pd
        from src.governor.csi_explain import _liquid_symbols

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        # First connection query returns distinct dates list.
        mock_conn.execute.return_value.fetchall.return_value = [
            (str(d.date()),) for d in pd.bdate_range(end="2026-07-31", periods=30)
        ]
        df = self._df()
        df["date"] = df["date"].astype(str)

        # csi_explain import pandas trong thân hàm → patch pd.read_sql.
        with patch("src.database.db_core.get_connection", return_value=mock_conn) as gc, \
             patch.object(pd, "read_sql", return_value=df) as rs:
            liquid = _liquid_symbols(100_000)
            assert "AAA" in liquid
            assert "BBB" not in liquid
            assert "CCC" not in liquid

            liquid50 = _liquid_symbols(50_000)
            assert "BBB" in liquid50


# ===================================================================
# 3. breadth_engine.run_breadth_analysis — last_trading_day fallback
# ===================================================================

class TestBreadthFallback:
    def _make_df(self, trading_dates):
        """Build daily_ohlcv-like df with 2 symbols and given dates.

        WHY: rolling(20) cho avg_vol_20d cần >=20 phiên/symbol. Hàm nhận
        danh sách 1-2 mốc, tự sinh 30 phiên kết thúc ở mốc cuối để
        MA20/avg_vol_20d không bị NaN → total_active không trống.
        """
        import pandas as pd
        end = pd.Timestamp(trading_dates[-1])
        sessions = pd.bdate_range(end=end, periods=30)
        rows = []
        for d in sessions:
            for sym, close in (("AAA", 10.0), ("BBB", 20.0)):
                rows.append({
                    "symbol": sym,
                    "date": d,
                    "close": close,
                    "volume": 200_000,
                    "high": close * 1.01,
                })
        return pd.DataFrame(rows)

    def test_weekend_falls_back_to_last_trading_day(self, capsys):
        from src.engine import breadth_engine

        # Data only has 2026-07-31 (Fri). Request 2026-08-01 (Sat).
        df = self._make_df(["2026-07-31"])
        with patch("src.engine.breadth_engine.get_connection") as gc, \
             patch("src.engine.breadth_engine.pd.read_sql", return_value=df):
            result = breadth_engine.run_breadth_analysis("2026-08-01")

        out = capsys.readouterr().out
        assert result is not None
        assert result["date"] == "2026-07-31"
        assert result["total_active"] == 2
        assert "fallback" in out
        assert "Không có mã nào thỏa mãn" not in out

    def test_trading_day_uses_date_directly(self, capsys):
        from src.engine import breadth_engine

        df = self._make_df(["2026-07-31"])
        with patch("src.engine.breadth_engine.get_connection") as gc, \
             patch("src.engine.breadth_engine.pd.read_sql", return_value=df):
            result = breadth_engine.run_breadth_analysis("2026-07-31")

        assert result is not None
        assert result["date"] == "2026-07-31"
        assert result["total_active"] == 2

    def test_no_data_before_ref_date_returns_none(self, capsys):
        from src.engine import breadth_engine

        # Data is AFTER the requested date → no valid_dates.
        # WHY: dùng df riêng chỉ có ngày > ref_date, không lẫn ngày trước đó.
        import pandas as pd
        df = pd.DataFrame({
            "symbol": ["AAA", "BBB"],
            "date": [pd.Timestamp("2026-08-03"), pd.Timestamp("2026-08-03")],
            "close": [10.0, 20.0],
            "volume": [200_000, 200_000],
            "high": [10.1, 20.2],
        })
        with patch("src.engine.breadth_engine.get_connection") as gc, \
             patch("src.engine.breadth_engine.pd.read_sql", return_value=df):
            result = breadth_engine.run_breadth_analysis("2026-07-31")

        assert result is None
