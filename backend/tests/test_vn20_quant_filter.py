"""test_vn20_quant_filter.py — Regression tests for PTCK_VN20 4-tier quant filter.

Locks in the 4 data-normalization fixes discovered during live 2026-08-03 run:
  1. ROE single-quarter → annualize x4 before 15% threshold
  2. RECEIVABLES_TO_REVENUE hardcoded 1.5 in crawler → derive from facts
  3. SHARES_OUT scale break (HPG 3.2B→292M) → median YoY dilution
  4. Sector momentum inf (broken prices) → drop inf before cumprod

Also locks `_period_at` / period-parsing (k[4:] not k[5:]).

Run:  python -m pytest backend/tests/test_vn20_quant_filter.py -q
"""

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.quant import vn20_quant_filter as vf


def make_conn():
    """In-memory SQLite with the real financial_facts schema + Row factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    conn.execute("CREATE TABLE financial_facts (symbol TEXT, period TEXT, metric TEXT, value REAL)")
    return conn


def seed_health(conn, symbol, ratio, period_values):
    for period, val in period_values:
        conn.execute(
            "INSERT INTO health_ratios (symbol, period, ratio_name, ratio_value) VALUES (?,?,?,?)",
            (symbol, period, ratio, val),
        )


def seed_fact(conn, symbol, metric, period_values):
    for period, val in period_values:
        conn.execute(
            "INSERT INTO financial_facts (symbol, period, metric, value) VALUES (?,?,?,?)",
            (symbol, period, metric, val),
        )


# ── Bug 1: ROE annualization ─────────────────────────────────────────


class TestTier1RoeAnnualization:
    def test_single_quarter_roe_annualized_before_threshold(self):
        """FPT ROE 6%/quarter = 26% annualized → must PASS (was failing at raw 6%)."""
        conn = make_conn()
        # 12 quarters: ROE 0.065 quarterly (annualized 26%)
        qs = []
        for y in (2024, 2025, 2026):
            for q in range(1, 5):
                qs.append((f"{y}Q{q}", 0.065))
        seed_health(conn, "FPT", "ROE", qs)
        seed_health(conn, "FPT", "DEBT_TO_EQUITY", [("2026Q2", 0.4)])
        seed_health(conn, "FPT", "GROSS_MARGIN", [("2026Q2", 0.35)])
        seed_fact(conn, "FPT", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        periods = vf._periods_n_years("2026Q2", 3)
        r = vf.tier1_buffett_quality(conn, "FPT", "STANDARD", periods)
        assert r["pass"] is True, f"FPT should pass with annualized ROE 26%, got {r['reasons']}"
        assert r["roe"] > 0.25  # 0.065 * 4 = 0.26

    def test_quarterly_roe_below_15_after_annualize_fails(self):
        """ROE 3%/quarter = 12% annualized → must FAIL."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.03) for y in (2024, 2025, 2026) for q in range(1, 5)]
        seed_health(conn, "X", "ROE", qs)
        seed_health(conn, "X", "DEBT_TO_EQUITY", [("2026Q2", 0.3)])
        seed_health(conn, "X", "GROSS_MARGIN", [("2026Q2", 0.30)])
        seed_fact(conn, "X", "CFO", [("2026Q2", 1e12)])

        r = vf.tier1_buffett_quality(conn, "X", "STANDARD", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False
        assert any("ROE" in x for x in r["reasons"])

    def test_bank_uses_npl_not_de_threshold(self):
        """Banks: D/E threshold relaxed to 8.0; D/E 2.0 must pass."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "VCB", "ROE", qs)
        seed_health(conn, "VCB", "DEBT_TO_EQUITY", [("2026Q2", 2.0)])
        seed_health(conn, "VCB", "NPL_RATIO", [("2026Q2", 0.01)])
        seed_fact(conn, "VCB", "CFO", [("2026Q2", 1e12)])

        r = vf.tier1_buffett_quality(conn, "VCB", "BANK", vf._periods_n_years("2026Q2", 3))
        # ROE 20% pass, D/E 2.0 < 8.0 pass. CFO only 1 quarter → fails CFO continuity.
        assert "D/E" not in " ".join(r["reasons"])
        assert r["de_max"] == 8.0

    def test_bank_missing_de_and_gross_margin_not_fail(self):
        """Banks without DEBT_TO_EQUITY / GROSS_MARGIN (VCI bank statements
        don't emit them) must NOT fail Tier 1 on missing data."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "BID", "ROE", qs)
        seed_fact(conn, "BID", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])
        seed_fact(conn, "BID", "NET_PROFIT", [(f"{y}Q{q}", 5e11) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "BID", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True, f"Bank should pass without D/E/GM, got {r['reasons']}"


# ── Bug 2: Receivables derived from facts, not hardcoded 1.5 ─────────


class TestTier2ReceivablesFromFacts:
    def test_receivables_derived_from_facts_not_ratio_column(self):
        """HPG receivables 18.04T / revenue 55.16T = 32.7% → derived correctly."""
        conn = make_conn()
        # health_ratios has the BROKEN hardcoded 1.5 value — must be IGNORED
        seed_health(conn, "HPG", "RECEIVABLES_TO_REVENUE", [("2026Q2", 1.5)])
        seed_fact(conn, "HPG", "RECEIVABLES", [("2026Q2", 18040.0), ("2026Q1", 16693.0)])
        seed_fact(conn, "HPG", "REVENUE", [("2026Q2", 55159.0), ("2026Q1", 52901.0)])

        r = vf.tier2_governance_shield(conn, "HPG", vf._periods_n_years("2026Q2", 3))
        assert r["receivables_ratio"] is not None
        assert r["receivables_ratio"] < 0.5, "Must NOT be 1.5 (broken crawler column)"
        assert abs(r["receivables_ratio"] - 0.327) < 0.01  # 18040/55159
        assert r["receivables_ratio"] > vf.T2_RECEIVABLES_MAX  # 32.7% > 25% → flagged

    def test_receivables_under_25_passes(self):
        conn = make_conn()
        seed_fact(conn, "GOOD", "RECEIVABLES", [("2026Q2", 100.0)])
        seed_fact(conn, "GOOD", "REVENUE", [("2026Q2", 1000.0)])
        seed_fact(conn, "GOOD", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])

        r = vf.tier2_governance_shield(conn, "GOOD", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True
        assert abs(r["receivables_ratio"] - 0.10) < 0.001


# ── Bug 3: SHARES_OUT scale break → median YoY dilution ──────────────


class TestTier2DilutionScaleBreak:
    def _seed_shares_with_scale_break(self, conn, symbol):
        """HPG-style: 3.2B for 2023Q3-2025Q2, then broken 292M from 2025Q3."""
        for y, q in [(2023, 3), (2023, 4), (2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2)]:
            seed_fact(conn, symbol, "SHARES_OUT", [(f"{y}Q{q}", 3.2e9)])
        for y, q in [(2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
            seed_fact(conn, symbol, "SHARES_OUT", [(f"{y}Q{q}", 292e6)])

    def test_scale_break_does_not_fake_dilution(self):
        """One broken quarter (3.2B→292M) must NOT produce >5% dilution via median."""
        conn = make_conn()
        self._seed_shares_with_scale_break(conn, "HPG")
        r = vf.tier2_governance_shield(conn, "HPG", vf._periods_n_years("2026Q2", 3))
        assert r["dilution"] is not None
        assert r["dilution"] <= 0.05, f"Dilution {r['dilution']} should be ~0 (median), not scale-break fake"

    def test_real_dilution_detected(self):
        """Genuine 10% share issuance → must be flagged."""
        conn = make_conn()
        # YoY: 2026Q2 = 1.1x vs 2025Q2 = 1.0x consistently
        for y, q in [(2025, 2)]:
            seed_fact(conn, "DIL", "SHARES_OUT", [(f"{y}Q{q}", 1.0e9)])
        for y, q in [(2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
            seed_fact(conn, "DIL", "SHARES_OUT", [(f"{y}Q{q}", 1.1e9)])
        r = vf.tier2_governance_shield(conn, "DIL", vf._periods_n_years("2026Q2", 3))
        assert r["dilution"] is not None and r["dilution"] > 0.05

    def test_missing_shares_out_skips_dilution_not_fail(self):
        """VCI statements don't emit SHARES_OUT → dilution gate SKIPPED, not failed."""
        conn = make_conn()
        # STANDARD: provide receivables so the only question is the dilution gate
        seed_fact(conn, "NODIL", "RECEIVABLES", [("2026Q2", 100.0)])
        seed_fact(conn, "NODIL", "REVENUE", [("2026Q2", 1000.0)])
        r = vf.tier2_governance_shield(conn, "NODIL", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True, "Missing SHARES_OUT must not fail Tier 2"
        assert r["dilution"] is None

    def test_bank_skips_receivables_gate(self):
        """Banks: receivables gate skipped (no trade receivables)."""
        conn = make_conn()
        seed_fact(conn, "BK", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])
        r = vf.tier2_governance_shield(conn, "BK", vf._periods_n_years("2026Q2", 3), entity_type="BANK")
        assert r["pass"] is True
        assert r["receivables_ratio"] is None


# ── Bug 4: Sector momentum inf handling ─────────────────────────────


class TestSectorMomentumInf:
    def test_inf_pct_change_does_not_poison_cumprod(self):
        """inf return (broken price) must be dropped, not poison momentum to NaN."""
        import pandas as pd

        # Build a screener-like conn with symbol_industry + daily_ohlcv
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE symbol_industry (symbol TEXT, icb_name3 TEXT)")
        conn.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL)")
        for s in ("HPG", "NSH", "SMC"):
            conn.execute("INSERT INTO symbol_industry VALUES (?,?)", (s, "Kim loại"))
            # 30 days, last 3 days close=0 → pct_change inf
            for i in range(30):
                close = 0.0 if i >= 27 else 100.0 + i
                conn.execute(
                    "INSERT INTO daily_ohlcv VALUES (?,?,?)",
                    (s, f"2026-07-{1 + i:02d}" if i < 29 else "2026-08-03", close),
                )
        ctx = vf.compute_sector_context(conn, "Kim loại", lookback=60)
        # momentum must be a finite float or None — never NaN
        assert ctx["momentum"] is None or not pd.isna(ctx["momentum"])


# ── Period parsing (k[4:] not k[5:]) ────────────────────────────────


class TestPeriodParsing:
    def test_periods_n_years_range(self):
        ps = vf._periods_n_years("2026Q2", 3)
        assert ps[0] == "2024Q1"  # ASCENDING: oldest first
        assert ps[-1] == "2026Q2"  # newest last
        assert len(ps) == 10  # 2024Q1..2026Q2 = 10 quarters
        assert "2024Q1" in ps
        assert "2024Q2" in ps

    def test_periods_last4_is_most_recent(self):
        """periods[-4:] must be the 4 LATEST quarters (regression for D/E lookup)."""
        ps = vf._periods_n_years("2026Q2", 3)
        last4 = ps[-4:]
        assert "2025Q3" in last4 and "2026Q2" in last4
        assert "2024Q1" not in last4


# ── Tier 4 MoS ──────────────────────────────────────────────────────


class TestTier4Mos:
    def test_cheap_valuation_high_mos(self):
        conn = make_conn()
        conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
        conn.execute("INSERT INTO valuation_scores VALUES ('DGC','2026Q2','PE',-1.5)")
        conn.execute("INSERT INTO valuation_scores VALUES ('DGC','2026Q2','PB',-1.2)")
        r = vf.tier4_valuation_mos(conn, "DGC")
        assert r["mos"] is not None
        assert r["mos"] >= vf.T4_MOS_MIN  # cheap → high MoS → pass

    def test_expensive_valuation_low_mos_fails(self):
        conn = make_conn()
        conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
        conn.execute("INSERT INTO valuation_scores VALUES ('EXP','2026Q2','PE',2.5)")
        conn.execute("INSERT INTO valuation_scores VALUES ('EXP','2026Q2','PB',2.0)")
        r = vf.tier4_valuation_mos(conn, "EXP")
        assert r["pass"] is False
        assert r["mos"] < vf.T4_MOS_MIN


# ── Max Sector Concentration Gate ────────────────────────────────────


class TestSectorConcentrationGate:
    def test_all_banks_capped_to_50_pct(self):
        """6 banks (100% of picks) must be capped so banking sector <= 50%."""
        qualified = [{"symbol": f"BK{i}", "sector": "Ngân hàng", "score": 80 - i, "mos": 0.6} for i in range(6)]
        alloc = vf.allocate(qualified)
        sw = alloc["sector_weights"]
        assert alloc["sector_capped"] is True
        assert sw.get("Ngân hàng", 0) <= vf.T4_MAX_SECTOR_WEIGHT + 0.001
        assert alloc["cash"] >= 0.3, f"Excess bank weight must drain to cash, cash={alloc['cash']}"

    def test_diversified_no_cap(self):
        """Well-diversified picks across sectors → no cap, no cash drain."""
        sectors = ["Ngân hàng", "Hóa chất", "Kim loại", "Bán lẻ", "Công nghệ"]
        qualified = [{"symbol": f"S{i}", "sector": sectors[i % 5], "score": 80 - i, "mos": 0.6} for i in range(5)]
        alloc = vf.allocate(qualified)
        assert alloc["sector_capped"] is False
        for sec, sw in alloc["sector_weights"].items():
            assert sw <= vf.T4_MAX_SECTOR_WEIGHT + 0.001
