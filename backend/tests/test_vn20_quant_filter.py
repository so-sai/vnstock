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


# ── T1 Dual-Branch: BANK (SBV metrics) vs STANDARD ──────────────────


class TestTier1DualBranch:
    """T1 Bank refactor 2026-08-06: bank quality dùng NPL/NIM/CAR thay CFO/GM.
    Fix Type II Error — ngân hàng tốt bị loại vì CFO dao động/GM không tồn tại."""

    def test_t1_bank_pass_with_low_cfo_if_npl_nim_ok(self):
        """Bank CFO âm (mở rộng tín dụng) nhưng ROE > 15%, NPL < 2.5%,
        NIM > 1.8%, CAR > 5% → PASS T1 (CFO không được dùng cho bank)."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "BANK1", "ROE", qs)
        seed_health(conn, "BANK1", "NPL_RATIO", [("2026Q2", 0.015)])
        seed_health(conn, "BANK1", "NIM", [("2026Q2", 0.035)])
        seed_health(conn, "BANK1", "CAPITAL_RATIO", [("2026Q2", 0.12)])
        # CFO âm liên tục — đáng lẽ rớt ở nhánh STANDARD nhưng bank được miễn
        seed_fact(conn, "BANK1", "CFO", [(f"{y}Q{q}", -1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "BANK1", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True, f"Bank should pass despite negative CFO, got {r['reasons']}"
        assert r["is_bank"] is True

    def test_t1_bank_fail_high_npl(self):
        """Bank NPL 5% > 2.5% → FAIL T1."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "BADBK", "ROE", qs)
        seed_health(conn, "BADBK", "NPL_RATIO", [("2026Q2", 0.05)])
        seed_health(conn, "BADBK", "NIM", [("2026Q2", 0.035)])
        seed_health(conn, "BADBK", "CAPITAL_RATIO", [("2026Q2", 0.12)])

        r = vf.tier1_buffett_quality(conn, "BADBK", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False, "Bank with NPL 5% must fail Tier 1"
        assert any("NPL" in x for x in r["reasons"])

    def test_t1_bank_fail_low_nim(self):
        """Bank NIM quarterly 0.004 (annual 1.6% < 1.8%) → FAIL T1.

        Convention: health_ratios.NIM là quarterly (NII quarterly / loans),
        annualize x4 trước khi so ngưỡng 1.8%/năm — như ROE."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "WEAK", "ROE", qs)
        seed_health(conn, "WEAK", "NPL_RATIO", [("2026Q2", 0.015)])
        seed_health(conn, "WEAK", "NIM", [("2026Q2", 0.004)])
        seed_health(conn, "WEAK", "CAPITAL_RATIO", [("2026Q2", 0.12)])

        r = vf.tier1_buffett_quality(conn, "WEAK", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False, "Bank with NIM annual 1.6% must fail Tier 1"
        assert any("NIM" in x for x in r["reasons"])

    def test_t1_bank_quarterly_nim_annualized_passes(self):
        """Bank NIM quarterly 0.0097 (VCB thực) → annual 3.88% > 1.8% → PASS T1.

        Regression cho bug 2026-08-06: VCB bị fail oan vì so NIM quarterly trực
        tiếp với ngưỡng annual (0.97% < 1.8%), trong khi annualized là 3.88%."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "VCB", "ROE", qs)
        seed_health(conn, "VCB", "NPL_RATIO", [("2026Q2", 0.015)])
        seed_health(conn, "VCB", "NIM", [("2026Q2", 0.0097)])
        seed_health(conn, "VCB", "CAPITAL_RATIO", [("2026Q2", 0.12)])

        r = vf.tier1_buffett_quality(conn, "VCB", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True, f"VCB NIM annualized 3.88% should pass T1, got {r['reasons']}"
        assert r["nim"] == round(0.0097 * 4.0, 4)

    def test_t1_bank_missing_metrics_pass_with_roe(self):
        """Bank thiếu NPL/NIM/CAR (VCI không phát hành cho mọi mã) → KHÔNG fail;
        chỉ ROE bắt buộc. (No Provenance → không chặn khi chỉ số SBV không tồn tại.)"""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "NOBANKMETRIC", "ROE", qs)

        r = vf.tier1_buffett_quality(conn, "NOBANKMETRIC", "BANK", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is True, f"Bank without NPL/NIM/CAR should pass on ROE alone, got {r['reasons']}"

    def test_t1_standard_fails_low_gross_margin(self):
        """Mã sản xuất (STANDARD) GM 19% < 25% → FAIL T1 dù ROE tốt."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "STEEL", "ROE", qs)
        seed_health(conn, "STEEL", "GROSS_MARGIN", [("2026Q2", 0.19)])
        seed_health(conn, "STEEL", "DEBT_TO_EQUITY", [("2026Q2", 0.5)])
        seed_fact(conn, "STEEL", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "STEEL", "STANDARD", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False, "Standard GM 19% must fail Tier 1"
        assert any("Gross margin" in x for x in r["reasons"])


# ── T1 Steel Exception: GM >= 15% (thép là commodity, biên gộp thấp bẩm sinh) ─
class TestTier1SteelException:
    """Ngoại lệ ngành Thép: GROSS_MARGIN >= 15% thay vì 25% chung.

    WHY: Thép (HPG/HSG/NKG) là ngành hàng hóa (commodity) — biên gộp thường
    15-22%, dưới ngưỡng Buffett 25% nhưng vẫn là doanh nghiệp chất lượng.
    Ngưỡng chung 25% gây Type II Error loại sạch toàn bộ ngành thép VN khỏi T1."""

    def test_t1_steel_passes_with_gm_19pct(self):
        """Thép GM 19% (dưới 25% chung nhưng trên 15% thép) → PASS T1."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]  # 20% annualized
        seed_health(conn, "HPG", "ROE", qs)
        seed_health(conn, "HPG", "GROSS_MARGIN", [("2026Q2", 0.19)])
        seed_health(conn, "HPG", "DEBT_TO_EQUITY", [("2026Q2", 0.9)])
        seed_fact(conn, "HPG", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "HPG", "STANDARD", vf._periods_n_years("2026Q2", 3), is_steel=True)
        assert r["pass"] is True, f"Steel GM 19% must pass T1 with steel exception, got {r['reasons']}"

    def test_t1_steel_fails_below_15pct(self):
        """Thép GM 13% < 15% → vẫn FAIL (ngưỡng thép là sàn tuyệt đối)."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]
        seed_health(conn, "NKG", "ROE", qs)
        seed_health(conn, "NKG", "GROSS_MARGIN", [("2026Q2", 0.13)])
        seed_health(conn, "NKG", "DEBT_TO_EQUITY", [("2026Q2", 0.8)])
        seed_fact(conn, "NKG", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "NKG", "STANDARD", vf._periods_n_years("2026Q2", 3), is_steel=True)
        assert r["pass"] is False, "Steel GM 13% must still fail T1"
        assert any("Gross margin" in x for x in r["reasons"])

    def test_t1_non_steel_uses_25pct_threshold(self):
        """Symbol không phải thép (is_steel=False) vẫn dùng ngưỡng 25%."""
        conn = make_conn()
        qs = [(f"{y}Q{q}", 0.05) for y in (2024, 2025, 2026) for q in range(1, 5)]
        seed_health(conn, "NONSTL", "ROE", qs)
        seed_health(conn, "NONSTL", "GROSS_MARGIN", [("2026Q2", 0.19)])
        seed_health(conn, "NONSTL", "DEBT_TO_EQUITY", [("2026Q2", 0.5)])
        seed_fact(conn, "NONSTL", "CFO", [(f"{y}Q{q}", 1e12) for y in (2024, 2025, 2026) for q in (1, 4)])

        r = vf.tier1_buffett_quality(conn, "NONSTL", "STANDARD", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False, "Non-steel GM 19% must fail T1 at 25% threshold"
        assert any("Gross margin" in x for x in r["reasons"])


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


# ── Industry-Relative Percentile Gate (FPT Type II fix) ────────────


class TestTier2IndustryPercentile:
    """Tầng 2 Percentile: FPT (67.2% receivables) phải pass nếu < P75 ngành IT."""

    def test_fpt_passes_when_below_sector_pct75(self):
        """FPT 67.2% < P75 sector 'Phần mềm & Dịch vụ máy tính' (72%) → PASS."""
        conn = make_conn()
        seed_fact(conn, "FPT", "RECEIVABLES", [("2026Q2", 11175.0)])
        seed_fact(conn, "FPT", "REVENUE", [("2026Q2", 16624.0)])
        seed_fact(conn, "FPT", "SHARES_OUT", [(f"{y}Q{q}", 1e9) for y in (2024, 2025, 2026) for q in range(1, 5)])

        r = vf.tier2_governance_shield(
            conn,
            "FPT",
            vf._periods_n_years("2026Q2", 3),
            sector_pct75=0.72,
        )
        assert r["pass"] is True, f"FPT 67.2% should pass with sector P75=72%, got {r['reasons']}"
        assert abs(r["receivables_ratio"] - 0.672) < 0.01

    def test_symbol_fails_when_above_sector_pct75(self):
        """Symbol with receivables > sector P75 → FAIL."""
        conn = make_conn()
        seed_fact(conn, "BAD", "RECEIVABLES", [("2026Q2", 800.0)])
        seed_fact(conn, "BAD", "REVENUE", [("2026Q2", 1000.0)])
        seed_fact(conn, "BAD", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])

        r = vf.tier2_governance_shield(
            conn,
            "BAD",
            vf._periods_n_years("2026Q2", 3),
            sector_pct75=0.50,
        )
        assert r["pass"] is False
        assert r["receivables_ratio"] == 0.8
        assert any("P75" in x for x in r["reasons"])

    def test_low_receivables_always_passes(self):
        """Dù sector P75 thấp, ratio <= 25% luôn pass (safe harbor)."""
        conn = make_conn()
        seed_fact(conn, "GOOD", "RECEIVABLES", [("2026Q2", 100.0)])
        seed_fact(conn, "GOOD", "REVENUE", [("2026Q2", 1000.0)])
        seed_fact(conn, "GOOD", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])

        r = vf.tier2_governance_shield(
            conn,
            "GOOD",
            vf._periods_n_years("2026Q2", 3),
            sector_pct75=0.05,  # sector P75 thấp bất thường
        )
        assert r["pass"] is True, "Ratio 10% <= 25% safe harbor → always pass"

    def test_no_pct75_falls_back_to_static(self):
        """Nếu không có sector_pct75 (legacy), dùng T2_RECEIVABLES_MAX."""
        conn = make_conn()
        seed_fact(conn, "OLD", "RECEIVABLES", [("2026Q2", 300.0)])
        seed_fact(conn, "OLD", "REVENUE", [("2026Q2", 1000.0)])
        seed_fact(conn, "OLD", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])

        r = vf.tier2_governance_shield(conn, "OLD", vf._periods_n_years("2026Q2", 3))
        assert r["pass"] is False, "30% > 25% static → fail when no sector P75"

    def test_solo_sector_uses_p100_fallback(self):
        """Solo sector (1 stock): sector_pct75=max ratio → stock always passes."""
        conn = make_conn()
        seed_fact(conn, "SOLO", "RECEIVABLES", [("2026Q2", 672.0)])
        seed_fact(conn, "SOLO", "REVENUE", [("2026Q2", 1000.0)])
        seed_fact(conn, "SOLO", "SHARES_OUT", [(f"{y}Q{q}", 100.0) for y in (2024, 2025, 2026) for q in range(1, 5)])

        # Solo sector: pct75 = max ratio = 0.672
        r = vf.tier2_governance_shield(
            conn,
            "SOLO",
            vf._periods_n_years("2026Q2", 3),
            sector_pct75=0.672,  # computed as max(ratios) for solo sector
        )
        assert r["pass"] is True, f"Solo stock 67.2% should pass with P100={0.672}, got {r['reasons']}"


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


# ── Dynamic MoS (Biên An Toàn Động) 2026-08-07 ──────────────────────


class TestDynamicMosThreshold:
    def test_crisis_regular_company_25pct(self):
        """Regime CRISIS + ROE 15% (thường) → MoS min = 25%."""
        assert vf.get_dynamic_mos_threshold("CRISIS", 0.15) == 0.25

    def test_bearish_regular_company_25pct(self):
        assert vf.get_dynamic_mos_threshold("BEARISH", 0.10) == 0.25

    def test_ranging_regular_company_20pct(self):
        """Regime RANGING + ROE 15% → MoS min = 20% (thay vì 25% tĩnh)."""
        assert vf.get_dynamic_mos_threshold("RANGING", 0.15) == 0.20

    def test_ranging_elite_company_15pct(self):
        """Regime RANGING + ROE 22% (Siêu cổ phiếu) → MoS min = 15% (giải phóng VCB/FPT)."""
        assert vf.get_dynamic_mos_threshold("RANGING", 0.22) == 0.15

    def test_recovery_elite_company_15pct(self):
        assert vf.get_dynamic_mos_threshold("RECOVERY", 0.20) == 0.15

    def test_expansion_regular_company_15pct(self):
        """Regime EXPANSION + ROE 15% → MoS min = 15%."""
        assert vf.get_dynamic_mos_threshold("EXPANSION", 0.15) == 0.15

    def test_expansion_elite_company_10pct(self):
        """Regime EXPANSION + ROE 25% → MoS min = 10% (sàn tuyệt đối)."""
        assert vf.get_dynamic_mos_threshold("EXPANSION", 0.25) == 0.10

    def test_bull_elite_company_10pct(self):
        assert vf.get_dynamic_mos_threshold("BULL", 0.30) == 0.10

    def test_crisis_elite_company_20pct(self):
        """CRISIS + Siêu cổ phiếu → 25% - 5% = 20% (vẫn giữ chiết khấu sâu)."""
        assert vf.get_dynamic_mos_threshold("CRISIS", 0.30) == 0.20

    def test_default_regime_ranging(self):
        """Regime None/UNKNOWN → fallback RANGING = 20%."""
        assert vf.get_dynamic_mos_threshold(None, 0.10) == 0.20
        assert vf.get_dynamic_mos_threshold("", 0.10) == 0.20

    def test_floor_never_below_10pct(self):
        """Sàn tuyệt đối 10% — không bao giờ thấp hơn."""
        assert vf.get_dynamic_mos_threshold("EXPANSION", 0.99) == 0.10
        assert vf.get_dynamic_mos_threshold("CRISIS", 0.99) >= 0.10

    def test_roe_boundary_20pct(self):
        """ROE đúng 20% → đủ điều kiện ưu đãi; dưới 20% thì không."""
        assert vf.get_dynamic_mos_threshold("RANGING", 0.20) == 0.15
        assert vf.get_dynamic_mos_threshold("RANGING", 0.1999) == 0.20

    def test_roe_none_no_discount(self):
        """Thiếu ROE → không ưu đãi, giữ ngưỡng base."""
        assert vf.get_dynamic_mos_threshold("RANGING", None) == 0.20
        assert vf.get_dynamic_mos_threshold("CRISIS", None) == 0.25


class TestLatestMarketRegime:
    def test_reads_latest_regime_history(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE regime_history (date TEXT, status TEXT)")
        conn.execute("INSERT INTO regime_history VALUES ('2026-08-06','RANGING')")
        conn.execute("INSERT INTO regime_history VALUES ('2026-08-05','TRENDING')")
        assert vf._latest_market_regime(conn) == "RANGING"

    def test_trending_maps_to_expansion(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE regime_history (date TEXT, status TEXT)")
        conn.execute("INSERT INTO regime_history VALUES ('2026-08-06','TRENDING')")
        assert vf._latest_market_regime(conn) == "EXPANSION"

    def test_crisis_passthrough(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE regime_history (date TEXT, status TEXT)")
        conn.execute("INSERT INTO regime_history VALUES ('2026-08-06','CRISIS')")
        assert vf._latest_market_regime(conn) == "CRISIS"

    def test_empty_db_falls_back_ranging(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        assert vf._latest_market_regime(conn) == "RANGING"

    def test_missing_table_falls_back_ranging(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        assert vf._latest_market_regime(conn) == "RANGING"


class TestTier4DynamicMosIntegration:
    def test_ranging_elite_low_mos_passes(self):
        """RANGING + ROE 22% → ngưỡng 15%. MoS 18% (<25% tĩnh nhưng >=15% động) → PASS."""
        conn = make_conn()
        conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
        conn.execute("INSERT INTO valuation_scores VALUES ('ELITE','2026Q2','PE',0.9)")
        conn.execute("INSERT INTO valuation_scores VALUES ('ELITE','2026Q2','PB',0.96)")
        r = vf.tier4_valuation_mos(conn, "ELITE", regime="RANGING", roe_annual=0.22)
        assert r["mos_threshold"] == 0.15
        assert r["mos"] >= 0.15, f"mos={r['mos']}"
        assert r["pass"] is True

    def test_crisis_same_mos_fails(self):
        """Cùng mã, cùng MoS nhưng CRISIS → ngưỡng 25% → FAIL (bảo vệ chiết khấu sâu)."""
        conn = make_conn()
        conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
        conn.execute("INSERT INTO valuation_scores VALUES ('ELITE','2026Q2','PE',0.9)")
        conn.execute("INSERT INTO valuation_scores VALUES ('ELITE','2026Q2','PB',0.96)")
        r = vf.tier4_valuation_mos(conn, "ELITE", regime="CRISIS", roe_annual=0.22)
        assert r["mos_threshold"] == 0.20
        assert r["mos"] < 0.20, f"mos={r['mos']}"
        assert r["pass"] is False

    def test_default_regime_ranging_threshold_in_result(self):
        conn = make_conn()
        conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
        conn.execute("INSERT INTO valuation_scores VALUES ('DGC','2026Q2','PE',-1.5)")
        r = vf.tier4_valuation_mos(conn, "DGC")
        assert r["mos_threshold"] == vf.get_dynamic_mos_threshold("RANGING", None)
