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


def test_pnj_2026q2_pb_va_pe_deu_co_sau_khi_sua_eps():
    """2026Q2: PB ok (BVPS có) VÀ PE CÓ (≈13,2) — EPS thực tế là +4.772đ.

    LỊCH SỬ KỲ VỌNG (giữ dấu vết): test này từng khẳng định "PE không tồn tại vì EPS = −664 (VCI sai)".
    Luồng ingestion TCBS (Agent 2, commit 7f7fd39) đã sửa EPS PNJ 2026Q2 về ĐÚNG +4.772đ ⇒ PE = 13,202
    là kết quả ĐÚNG, có `is_clean_metric = 1`. Luật "EPS ≤ 0 ⇒ không tạo PE" vẫn giữ nguyên trong engine;
    ca âm ở 2026Q2 không còn tồn tại trong store nên luật được kiểm bằng ca TỔNG HỢP ở nơi khác, KHÔNG
    suy ra từ dòng đã được sửa.
    """
    import sqlite3

    db = Path(__file__).resolve().parent.parent / "data" / "financial_facts.db"
    conn = sqlite3.connect(db)
    try:
        rows = dict(
            conn.execute(
                "SELECT ratio_name, ratio_value FROM valuation_scores WHERE symbol='PNJ' AND period='2026Q2'"
            ).fetchall()
        )
    finally:
        conn.close()
    assert "PB" in rows, rows
    assert "PE" in rows, f"EPS đã được sửa về dương ⇒ PE phải tồn tại: {rows}"
    assert rows["PE"] > 0, rows
