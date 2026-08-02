"""test_forensic_engine.py — TDD for ForensicEngine (Beneish M-Score, Sloan, ARI).

Cases covered:
  1. Contiguous 8-quarter series -> full score set, only first row non-adjacent.
  2. Missing quarter -> neutral index (DSRI=1.0) and NaN accrual (Sloan).
  3. Fiscal-year change (ordinal jump) -> no false red flag.
  4. Real store smoke test.
  5. Edge cases: empty input, zero/negative equity, bank-style missing metrics.
"""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from src.financial.forensic_engine import (
    BENEISH_THRESHOLD,
    OUTPUT_COLUMNS,
    ForensicEngine,
)


def _wide_df(n: int = 8, start_year: int = 2023, start_quarter: int = 1, symbol: str = "PNJ", **overrides) -> pd.DataFrame:
    """Build a wide-format fact frame of `n` contiguous quarters."""
    rows = []
    year, quarter = start_year, start_quarter
    for i in range(n):
        rows.append(
            {
                "symbol": symbol,
                "period": f"{year}Q{quarter}",
                "fiscal_year": year,
                "fiscal_quarter": quarter,
                "REVENUE": 1000.0 + i * 100.0,
                "NET_INCOME": 100.0 + i * 5.0,
                "CFO": 80.0 + i * 5.0,
                "TOTAL_ASSETS": 5000.0 + i * 200.0,
                "CURRENT_ASSETS": 2000.0 + i * 100.0,
                "TOTAL_EQUITY": 2000.0 + i * 100.0,
                "TOTAL_LIABILITIES": 3000.0 + i * 100.0,
                "RECEIVABLES": 200.0 + i * 10.0,
                "COGS": 600.0 + i * 60.0,
                "GROSS_PROFIT": 400.0 + i * 40.0,
                "DEPRECIATION": 50.0 + i * 5.0,
                "ADMIN_EXPENSES": 80.0 + i * 5.0,
            }
        )
        if quarter == 4:
            year, quarter = year + 1, 1
        else:
            quarter += 1
    df = pd.DataFrame(rows)
    for key, val in overrides.items():
        df[key] = val
    return df


def _drop_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    return df[df["period"] != period].reset_index(drop=True)


class TestContiguousSeries:
    def test_full_screen_returns_expected_columns(self):
        out = ForensicEngine().compute_forensics(_wide_df())
        assert set(OUTPUT_COLUMNS).issubset(out.columns)
        assert len(out) == 8
        assert out["symbol"].eq("PNJ").all()

    def test_only_first_row_is_non_adjacent(self):
        out = ForensicEngine().compute_forensics(_wide_df())
        assert out["adjacent"].iloc[0] is not True
        assert out["adjacent"].iloc[1:].all()

    def test_sloan_ratio_matches_two_period_average(self):
        out = ForensicEngine().compute_forensics(_wide_df())
        row = out.loc[1]
        ni = 105.0
        cfo = 85.0
        avg = (5000.0 + 200.0 + 5000.0) / 2.0
        assert row["Sloan_Ratio"] == pytest.approx((ni - cfo) / avg)

    def test_m_score_flag_fires_when_receivables_inflate(self):
        df = pd.DataFrame(
            {
                "symbol": ["PNJ"] * 4,
                "period": ["2024Q1", "2024Q2", "2024Q3", "2024Q4"],
                "fiscal_year": [2024] * 4,
                "fiscal_quarter": [1, 2, 3, 4],
                "REVENUE": [1000.0, 1100.0, 1210.0, 1331.0],
                "RECEIVABLES": [200.0, 330.0, 484.0, 665.5],
                "NET_INCOME": [100.0] * 4,
                "CFO": [-400.0] * 4,
                "TOTAL_ASSETS": [5000.0] * 4,
                "CURRENT_ASSETS": [2000.0] * 4,
                "TOTAL_EQUITY": [2000.0] * 4,
                "TOTAL_LIABILITIES": [3000.0] * 4,
                "GROSS_PROFIT": [400.0] * 4,
                "COGS": [600.0] * 4,
                "DEPRECIATION": [50.0] * 4,
                "ADMIN_EXPENSES": [80.0] * 4,
            }
        )
        out = ForensicEngine().compute_forensics(df)
        assert out["DSRI"].iloc[1] == pytest.approx(1.5)
        assert out["M_Score"].iloc[1] > BENEISH_THRESHOLD
        assert out["M_Score_Flag"].iloc[1]


class TestPeriodAdjacency:
    def test_missing_quarter_neutralizes_index_and_nans_accrual(self):
        df = _drop_period(_wide_df(n=8), "2023Q2")
        out = ForensicEngine().compute_forensics(df)
        # At 2023Q3 the previous stored period is 2023Q1 -> ordinal gap of 2.
        q3 = out[out["period"] == "2023Q3"].iloc[0]
        assert q3["adjacent"] is not True
        assert q3["DSRI"] == 1.0
        assert np.isnan(q3["Sloan_Ratio"])
        assert not q3["M_Score_Flag"]
        assert not q3["Hard_Violation"]

    def test_fiscal_year_change_no_false_flag(self):
        # Jump 2023Q4 -> 2024Q2 (skips 2024Q1): ordinal delta = 2.
        df = _drop_period(_wide_df(n=8), "2024Q1")
        df["RECEIVABLES"] = df["REVENUE"] * 3.0  # extreme inflation
        out = ForensicEngine().compute_forensics(df)
        q2 = out[out["period"] == "2024Q2"].iloc[0]
        assert q2["adjacent"] is not True
        assert q2["DSRI"] == 1.0
        assert np.isnan(q2["Sloan_Ratio"])
        assert not q2["M_Score_Flag"]
        assert not q2["Hard_Violation"]
        assert q2["Risk_Score"] < 1.0

    def test_hard_violation_still_detected_on_clean_series(self):
        df = _wide_df()
        df["RECEIVABLES"] = df["TOTAL_EQUITY"] * 0.8
        df["CFO"] = df["NET_INCOME"] - 0.3 * df["TOTAL_ASSETS"]
        out = ForensicEngine().compute_forensics(df)
        viol = out[out["period"] == "2023Q2"].iloc[0]
        assert viol["Hard_Violation"]
        assert viol["Risk_Score"] == 1.0


class TestEdgeCases:
    def test_empty_input_returns_empty(self):
        out = ForensicEngine().compute_forensics(pd.DataFrame())
        assert len(out) == 0

    def test_single_quarter_no_score_no_crash(self):
        out = ForensicEngine().compute_forensics(_wide_df(n=1))
        assert len(out) == 1
        assert out["adjacent"].iloc[0] is not True
        assert np.isnan(out["Sloan_Ratio"].iloc[0])

    def test_zero_equity_ari_is_zero(self):
        df = _wide_df(n=3)
        df["TOTAL_EQUITY"] = 0.0
        out = ForensicEngine().compute_forensics(df)
        assert (out["ARI"] == 0.0).all()

    def test_bank_style_missing_metrics_degrade_to_neutral(self):
        df = pd.DataFrame(
            {
                "symbol": ["ACB"] * 4,
                "period": ["2024Q1", "2024Q2", "2024Q3", "2024Q4"],
                "fiscal_year": [2024, 2024, 2024, 2024],
                "fiscal_quarter": [1, 2, 3, 4],
                "NET_PROFIT": [1000.0] * 4,
                "CFO": [900.0] * 4,
                "TOTAL_ASSETS": [100000.0] * 4,
                "TOTAL_EQUITY": [20000.0] * 4,
            }
        )
        out = ForensicEngine().compute_forensics(df)
        assert len(out) == 4
        assert out["M_Score"].notna().all()
        assert (out["DSRI"] == 1.0).all()
        assert not out["M_Score_Flag"].any()


class TestRealStore:
    def test_run_screens_all_symbols(self):
        engine = ForensicEngine()
        result = engine.run()
        if result.empty:
            pytest.skip("financial_facts.db empty")
        assert set(OUTPUT_COLUMNS).issubset(result.columns)
        assert result["symbol"].nunique() >= 1

    def test_load_facts_returns_wide_metrics(self):
        engine = ForensicEngine()
        with sqlite3.connect(engine.db_path) as conn:
            df = engine.load_facts(conn)
        if df.empty:
            pytest.skip("financial_facts.db empty")
        assert {"symbol", "period", "fiscal_year", "fiscal_quarter"}.issubset(df.columns)
        assert any(c in df.columns for c in ("REVENUE", "NET_INCOME", "CFO"))
