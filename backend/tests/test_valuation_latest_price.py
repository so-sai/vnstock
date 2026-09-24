# -*- coding: utf-8 -*-
"""Regression: compute_valuation latest_price must match latest_period.

PNJ 2026Q2 has PB only (EPS<0 → no PE; NET_INCOME missing → no ROE).
Before fix, latest_price was last-written by ROE loop (2026Q1 @ Q1-end)
while latest_period was 2026Q2 → summary mismatch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_pnj_latest_price_matches_latest_period():
    from src.financial.valuation_engine import ValuationEngine

    ve = ValuationEngine()
    try:
        r = ve.compute_valuation("PNJ")
    finally:
        ve.close() if hasattr(ve, "close") else None

    assert r.get("status") in ("DONE", "NO_PRICE", "NO_VALUATION_RESULTS", "NO_DATA"), r
    if r.get("status") != "DONE":
        return

    lp, lper = r.get("latest_price"), r.get("latest_period")
    assert lp and lper, r
    # 2026Q2 quarter-end close in screener = 63,000 (not Q1-end 71,970)
    if lper == "2026Q2":
        assert lp == 63000.0, f"latest_price={lp} should be Q2-end 63000, not {lp}"
        assert lp != 71970.0, "stale Q1-end price leaked into summary"


def test_pnj_2026q2_pb_present_pe_absent():
    """2026Q2: PB ok (BVPS present); PE absent (EPS -664 → engine skips)."""
    import sqlite3

    db = Path(__file__).resolve().parent.parent / "data" / "financial_facts.db"
    conn = sqlite3.connect(db)
    try:
        rows = dict(
            conn.execute(
                "SELECT ratio_name, ratio_value FROM valuation_scores "
                "WHERE symbol='PNJ' AND period='2026Q2'"
            ).fetchall()
        )
    finally:
        conn.close()
    assert "PB" in rows, rows
    assert "PE" not in rows, "PE must not exist when EPS<=0"
