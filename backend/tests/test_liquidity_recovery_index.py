"""test_liquidity_recovery_index.py — TDD tests for LRI v2 module.

Tests adaptive normalization:
  - S_USDVND: MA90 deviation (penalizes spike, not structural depreciation)
  - S_Interbank: rolling P10/P90 bounds (self-adjusting to new normal)
  - PIT target_date: data cutoff for backtest safety
  - Degraded mode: logging when data is missing

Also tests edge cases: missing data, extreme values, normalization bounds.
"""

import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.liquidity_recovery_index import (
    INTERBANK_HIGH,
    INTERBANK_LOW,
    LiquidityRecoveryIndex,
    _normalize,
)


def _make_db(
    interbank_on=None,
    usd_vnd=None,
    fii_flow=None,
    breadth=None,
    seed_history=True,
    interbank_history=None,
    usd_vnd_history=None,
):
    """Create a temporary SQLite DB with macro data.

    When seed_history=True (default), seeds 120 days USD_VND + 260 days
    INTERBANK with realistic distributions for adaptive normalization tests.
    Custom history lists OVERRIDE default seeding for that variable.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE macro_history (
            variable TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL,
            is_stale INTEGER DEFAULT 0,
            PRIMARY KEY (variable, date)
        )
    """)
    conn.execute("""
        CREATE TABLE market_foreign_history (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            foreign_vol INTEGER,
            net_vol INTEGER,
            net_value REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("""
        CREATE TABLE regime_history (
            date TEXT PRIMARY KEY,
            regime_score REAL,
            status TEXT,
            breadth_pct REAL,
            breadth_velocity REAL,
            trend_score REAL,
            vol_score REAL,
            atr_ratio REAL,
            active_model TEXT,
            recovery_flag INTEGER
        )
    """)

    if seed_history:
        # Default USD_VND: 120 days of gentle drift around 26,000 (2026)
        if usd_vnd_history is None:
            base_usd = 26000.0
            start = date(2026, 4, 1)
            for i in range(120):
                d = start + timedelta(days=i)
                val = base_usd - ((120 - i) * 2.5) + ((i % 7) * 15)
                conn.execute(
                    "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                    ("USD_VND", d.isoformat(), val),
                )
        else:
            for ds, val in usd_vnd_history:
                conn.execute(
                    "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                    ("USD_VND", ds, val),
                )

        # Default INTERBANK: 260 days ranging 3.2-5.8 (2023-2024)
        if interbank_history is None:
            base_rates = [4.5, 4.2, 3.8, 3.5, 5.0, 5.5, 3.2, 4.0, 3.6, 5.8]
            start = date(2023, 1, 2)
            for i in range(260):
                d = start + timedelta(days=i)
                rate = base_rates[i % len(base_rates)] + ((i % 13) * 0.08)
                conn.execute(
                    "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                    ("INTERBANK_ON", d.isoformat(), round(rate, 2)),
                )
        else:
            for ds, val in interbank_history:
                conn.execute(
                    "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                    ("INTERBANK_ON", ds, val),
                )

    # Current-day values (OR REPLACE to avoid conflict)
    if interbank_on is not None:
        conn.execute(
            "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
            ("INTERBANK_ON", "2026-08-03", interbank_on),
        )
    if usd_vnd is not None:
        conn.execute(
            "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
            ("USD_VND", "2026-08-03", usd_vnd),
        )
    if fii_flow is not None:
        conn.execute(
            "INSERT INTO market_foreign_history (symbol, date, net_value) VALUES (?, ?, ?)",
            ("TOTAL", "2026-08-03", fii_flow),
        )
    if breadth is not None:
        conn.execute(
            "INSERT INTO regime_history (date, breadth_pct) VALUES (?, ?)",
            ("2026-08-03", breadth),
        )

    conn.commit()
    conn.close()
    return tmp.name


def _seed_history(n_days, start_date, base_val, noise_func):
    """Helper: generate unique-date history list."""
    result = []
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        result.append((d.isoformat(), round(noise_func(i, base_val), 2)))
    return result


# ── Test: Normalization function ─────────────────────────────────────


class TestNormalize:
    def test_midpoint(self):
        mid = (INTERBANK_LOW + INTERBANK_HIGH) / 2
        assert _normalize(mid, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(0.5, abs=0.01)

    def test_inverted_low_value_gives_high_score(self):
        assert _normalize(2.0, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(1.0, abs=0.01)

    def test_inverted_high_value_gives_low_score(self):
        assert _normalize(7.5, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(0.0, abs=0.01)

    def test_direct_high_value_gives_high_score(self):
        assert _normalize(70.0, 30.0, 70.0, invert=False) == pytest.approx(1.0, abs=0.01)

    def test_none_gives_neutral(self):
        assert _normalize(None, 0, 100) == 0.5

    def test_clamped_above(self):
        assert _normalize(100.0, 0.0, 50.0) == 1.0

    def test_clamped_below(self):
        assert _normalize(-10.0, 0.0, 50.0) == 0.0


# ── Test: Adaptive S_USDVND (MA90 deviation) ─────────────────────────


class TestAdaptiveUSDVND:
    def test_stable_high_fx_no_penalty(self):
        """USD/VND at 26,291 with MA90~26,185 -> dev~0.4% -> score high."""
        usd_hist = _seed_history(
            120,
            date(2026, 4, 1),
            26000.0,
            lambda i, base: base + (i * 1.5) + ((i % 5) * 5),
        )
        db = _make_db(
            usd_vnd=26291,
            interbank_on=4.0,
            fii_flow=200,
            breadth=55,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.s_usdvnd >= 0.6, f"Stable high FX: S_USDVND should be high, got {lri.s_usdvnd}"
        assert lri.components_raw["usdvnd_deviation_pct"] < 2.0
        Path(db).unlink()

    def test_spike_deviation_penalized(self):
        """USD/VND spikes from MA90~24,000 to 26,000 -> dev~8% -> score 0.0."""
        usd_hist = _seed_history(
            120,
            date(2026, 4, 1),
            24000.0,
            lambda i, base: base + ((i % 3) * 10),
        )
        db = _make_db(
            usd_vnd=26000,
            interbank_on=4.0,
            fii_flow=0,
            breadth=50,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.s_usdvnd <= 0.1, f"Spike FX: S_USDVND should be low, got {lri.s_usdvnd}"
        Path(db).unlink()

    def test_structural_depreciation_not_penalized(self):
        """Gradual drift from 25,000 to 25,500 -> dev small -> score high."""
        usd_hist = _seed_history(
            120,
            date(2026, 4, 1),
            25000.0,
            lambda i, base: base + (i * 4.0),
        )
        db = _make_db(
            usd_vnd=25500,
            interbank_on=4.0,
            fii_flow=0,
            breadth=50,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.s_usdvnd >= 0.5, f"Structural drift: S_USDVND should be >= 0.5, got {lri.s_usdvnd}"
        Path(db).unlink()


# ── Test: Regime-Aware Bayesian Bound S_Interbank ────────────────────


class TestRegimeBayesianInterbank:
    """Test S_Interbank with regime detection + Bayesian bound blending."""

    def test_regime_detection_low_rate(self):
        """Low interbank (3.2-3.5%) -> heuristic classifies EXPANSION or NORMAL."""
        ib_hist = _seed_history(
            50,
            date(2025, 1, 1),
            2.8,
            lambda i, base: base + ((i % 5) * 0.06),  # 2.8-3.1 range
        )
        db = _make_db(
            interbank_on=3.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["interbank_regime"] == "EXPANSION"
        Path(db).unlink()

    def test_regime_detection_normal(self):
        """90-day avg 3.5-5.5% -> NORMAL regime (heuristic fallback)."""
        ib_hist = _seed_history(
            50,
            date(2024, 6, 1),
            4.0,
            lambda i, base: base + ((i % 10) * 0.1),  # 4.0-4.9 range
        )
        db = _make_db(
            interbank_on=4.5,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["interbank_regime"] == "NORMAL"
        Path(db).unlink()

    def test_regime_detection_high_rate(self):
        """90-day avg 5.5-7.5% -> CONTRACTION regime (heuristic fallback)."""
        ib_hist = _seed_history(
            50,
            date(2023, 1, 1),
            5.5,
            lambda i, base: base + ((i % 10) * 0.15),  # 5.5-6.85 range
        )
        db = _make_db(
            interbank_on=6.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["interbank_regime"] == "CONTRACTION"
        Path(db).unlink()

    def test_regime_detection_crisis(self):
        """90-day avg > 7.5% -> CONTRACTION regime (heuristic fallback)."""
        ib_hist = _seed_history(
            50,
            date(2026, 7, 1),
            8.0,
            lambda i, base: base + ((i % 5) * 0.5),  # 8.0-10.0 range
        )
        db = _make_db(
            interbank_on=9.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["interbank_regime"] == "CONTRACTION"
        Path(db).unlink()

    def test_bayesian_blending_alpha_scales_with_n(self):
        """Alpha (data weight) should increase with sample size."""
        # Small sample
        ib_small = _seed_history(
            40,
            date(2025, 1, 1),
            4.0,
            lambda i, base: base + ((i % 5) * 0.1),
        )
        db = _make_db(
            interbank_on=4.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_small,
        )
        lri_small = LiquidityRecoveryIndex(db).compute()
        alpha_small = lri_small.components_raw["interbank_alpha"]

        # Large sample
        ib_large = _seed_history(
            300,
            date(2023, 1, 1),
            4.0,
            lambda i, base: base + ((i % 10) * 0.1),
        )
        db2 = _make_db(
            interbank_on=4.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_large,
        )
        lri_large = LiquidityRecoveryIndex(db2).compute()
        alpha_large = lri_large.components_raw["interbank_alpha"]

        assert alpha_large > alpha_small, f"Alpha should increase with sample: {alpha_small} vs {alpha_large}"
        Path(db).unlink()
        Path(db2).unlink()

    def test_bayesian_bounds_fallback_to_policy(self):
        """With < 30 obs, should fall back to regime + policy (alpha=0)."""
        ib_hist = [("2026-08-01", 4.0), ("2026-08-02", 4.2)]
        db = _make_db(
            interbank_on=4.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["interbank_alpha"] == 0.0
        Path(db).unlink()

    def test_bayesian_bounds_produce_valid_range(self):
        """P10 must be less than P90 after Bayesian blending."""
        ib_hist = _seed_history(
            260,
            date(2023, 1, 2),
            3.5,
            lambda i, base: base + (i % 20) * 0.15,
        )
        db = _make_db(
            interbank_on=5.72,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        p10 = lri.components_raw["interbank_p10"]
        p90 = lri.components_raw["interbank_p90"]
        assert p10 < p90, f"P10 ({p10}) must be < P90 ({p90})"
        Path(db).unlink()

    def test_572_neutral_rate_scores_midrange(self):
        """5.72% in NORMAL/EXPANSION regime should score low-to-mid range."""
        ib_hist = _seed_history(
            260,
            date(2023, 1, 2),
            4.0,
            lambda i, base: base + (i % 15) * 0.15,  # 4.0-6.0 range
        )
        db = _make_db(
            interbank_on=5.72,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.0 <= lri.s_interbank <= 0.7, f"5.72% neutral should score low-to-mid range, got {lri.s_interbank}"
        Path(db).unlink()

    def test_crisis_rate_scores_very_low(self):
        """12% in CRISIS regime should score very low."""
        ib_hist = _seed_history(
            90,
            date(2026, 7, 1),
            8.0,
            lambda i, base: base + ((i % 5) * 0.5),
        )
        db = _make_db(
            interbank_on=12.0,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.s_interbank <= 0.15, f"12% crisis should score very low, got {lri.s_interbank}"
        Path(db).unlink()

    def test_cheap_money_scores_high(self):
        """2.5% in LOW_RATE regime should score high."""
        ib_hist = _seed_history(
            90,
            date(2025, 1, 1),
            3.0,
            lambda i, base: base + ((i % 5) * 0.06),
        )
        db = _make_db(
            interbank_on=2.5,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.s_interbank >= 0.7, f"2.5% cheap should score high, got {lri.s_interbank}"
        Path(db).unlink()

    def test_regime_shift_changes_bounds(self):
        """Same rate (5.72%) should score differently in different regimes."""
        # EXPANSION regime context (low rates)
        ib_low = _seed_history(
            50,
            date(2025, 6, 1),
            3.2,
            lambda i, base: base + ((i % 5) * 0.06),
        )
        db_low = _make_db(
            interbank_on=5.72,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_low,
        )
        lri_low = LiquidityRecoveryIndex(db_low).compute()

        # CONTRACTION regime context (high rates)
        ib_high = _seed_history(
            50,
            date(2023, 1, 1),
            6.0,
            lambda i, base: base + ((i % 10) * 0.15),
        )
        db_high = _make_db(
            interbank_on=5.72,
            usd_vnd=26000,
            fii_flow=0,
            breadth=50,
            interbank_history=ib_high,
        )
        lri_high = LiquidityRecoveryIndex(db_high).compute()

        # In CONTRACTION context, 5.72% is less alarming -> higher score
        assert lri_high.s_interbank > lri_low.s_interbank, (
            f"CONTRACTION context should score 5.72% higher: {lri_high.s_interbank} vs {lri_low.s_interbank}"
        )
        Path(db_low).unlink()
        Path(db_high).unlink()


# ── Test: PIT target_date ────────────────────────────────────────────


class TestPITTargetDate:
    def test_target_date_cutoff(self):
        """Data after target_date should not be visible."""
        usd_hist = [
            ("2026-06-01", 25000),
            ("2026-07-01", 25200),
            ("2026-07-15", 25400),
        ]
        db = _make_db(
            usd_vnd=26300,
            interbank_on=4.0,
            fii_flow=0,
            breadth=50,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute(target_date="2026-07-15")
        assert lri.components_raw["usd_vnd"] == 25400
        Path(db).unlink()

    def test_live_mode_uses_latest(self):
        """Without target_date, should use latest data."""
        db = _make_db(usd_vnd=26291, interbank_on=5.72, fii_flow=200, breadth=68)
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.components_raw["usd_vnd"] == 26291
        assert lri.components_raw["interbank_on"] == 5.72
        Path(db).unlink()


# ── Test: Degraded mode ──────────────────────────────────────────────


class TestDegradedMode:
    def test_missing_interbank_logs_degraded(self):
        """Missing INTERBANK data -> degraded_components includes INTERBANK."""
        db = _make_db(
            usd_vnd=26000,
            breadth=50,
            seed_history=False,  # no seeded history at all
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert "INTERBANK" in lri.degraded_components
        Path(db).unlink()

    def test_missing_usdvnd_logs_degraded(self):
        """Missing USDVND data -> degraded_components includes USDVND."""
        db = _make_db(
            interbank_on=4.0,
            breadth=50,
            seed_history=False,  # no seeded history
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert "USDVND" in lri.degraded_components
        Path(db).unlink()

    def test_all_data_present_no_degraded(self):
        """All data present -> empty degraded_components."""
        db = _make_db(interbank_on=4.0, usd_vnd=26000, fii_flow=200, breadth=55)
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.degraded_components == []
        Path(db).unlink()


# ── Test: LRI with different macro regimes ───────────────────────────


class TestLRIRegimes:
    def test_2021_recovery_high_liquidity(self):
        """2021: Low interbank (1.5%), stable FX, positive FII -> LRI high."""
        ib_hist = _seed_history(
            120,
            date(2021, 1, 1),
            1.0,
            lambda i, base: base + (i % 10) * 0.1,  # 1.0-1.9
        )
        usd_hist = _seed_history(
            120,
            date(2021, 1, 1),
            22800.0,
            lambda i, base: base + ((i % 7) * 20),  # 22,800-22,920
        )
        db = _make_db(
            interbank_on=1.5,
            usd_vnd=22850,
            fii_flow=800.0,
            breadth=65.0,
            interbank_history=ib_hist,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri >= 0.7, f"2021 recovery: LRI should be high, got {lri.lri}"
        assert lri.regime in ("AGGRESSIVE", "PROBE")
        assert lri.max_allocation_pct > 50.0
        Path(db).unlink()

    def test_2022_fed_tightening_stress(self):
        """2022: High interbank (7%), weak FX, FII outflow -> LRI low."""
        ib_hist = _seed_history(
            260,
            date(2022, 1, 1),
            5.5,
            lambda i, base: base + (i % 15) * 0.1,  # 5.5-6.9
        )
        usd_hist = _seed_history(
            120,
            date(2022, 1, 1),
            24500.0,
            lambda i, base: base + (i * 8),  # rising
        )
        db = _make_db(
            interbank_on=7.0,
            usd_vnd=25500,
            fii_flow=-1500.0,
            breadth=25.0,
            interbank_history=ib_hist,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri <= 0.4, f"2022 tightening: LRI should be low, got {lri.lri}"
        Path(db).unlink()

    def test_2023_volatile_probe_mode(self):
        """2023: Moderate interbank (4%), stable FX, mixed FII -> LRI mid."""
        ib_hist = _seed_history(
            260,
            date(2023, 1, 2),
            3.5,
            lambda i, base: base + (i % 20) * 0.1,  # 3.5-5.4
        )
        usd_hist = _seed_history(
            120,
            date(2023, 1, 1),
            24000.0,
            lambda i, base: base + ((i % 7) * 30),  # 24,000-24,180
        )
        db = _make_db(
            interbank_on=4.0,
            usd_vnd=24100,
            fii_flow=-200.0,
            breadth=50.0,
            interbank_history=ib_hist,
            usd_vnd_history=usd_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.2 <= lri.lri <= 0.9, f"2023 volatile: LRI should be mid, got {lri.lri}"
        Path(db).unlink()


# ── Test: Edge cases ─────────────────────────────────────────────────


class TestLRIEdgeCases:
    def test_missing_data_returns_neutral(self):
        """Empty DB should return neutral LRI."""
        db = _make_db(seed_history=False)
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.3 <= lri.lri <= 0.7, f"Missing data: LRI should be neutral, got {lri.lri}"
        Path(db).unlink()

    def test_extreme_interbank_floor(self):
        """Interbank at 15% (crisis) should give very low LRI."""
        ib_hist = _seed_history(
            260,
            date(2023, 1, 2),
            5.0,
            lambda i, base: base + (i % 20) * 0.3,  # 5.0-10.7
        )
        db = _make_db(
            interbank_on=15.0,
            usd_vnd=26000,
            fii_flow=-3000.0,
            breadth=15.0,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri < 0.3, f"Extreme interbank: LRI should be very low, got {lri.lri}"
        Path(db).unlink()

    def test_extreme_breadth_high(self):
        """Breadth at 90% (strong rally) should boost LRI."""
        db = _make_db(interbank_on=3.0, usd_vnd=26000, fii_flow=1000.0, breadth=90.0)
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri >= 0.4, f"High breadth: LRI should be elevated, got {lri.lri}"
        Path(db).unlink()

    def test_weights_sum_to_one(self):
        total = sum(LiquidityRecoveryIndex.WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001), f"Weights sum to {total}"

    def test_lri_bounded_zero_one(self):
        """LRI must always be in [0, 1]."""
        ib_hist = _seed_history(
            260,
            date(2023, 1, 2),
            3.0,
            lambda i, base: base + (i % 20) * 0.5,
        )
        db = _make_db(
            interbank_on=100.0,
            usd_vnd=50000.0,
            fii_flow=-10000.0,
            breadth=0.0,
            interbank_history=ib_hist,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.0 <= lri.lri <= 1.0, f"LRI out of bounds: {lri.lri}"
        Path(db).unlink()

    def test_probe_regime_allocation_scales_with_lri(self):
        """In PROBE regime, max_allocation should equal LRI * 100."""
        db = _make_db(interbank_on=4.5, usd_vnd=26100, fii_flow=-100.0, breadth=45.0)
        lri = LiquidityRecoveryIndex(db).compute()
        if lri.regime == "PROBE":
            assert lri.max_allocation_pct == pytest.approx(lri.lri * 100, abs=1.0)
        Path(db).unlink()

    def test_audit_trail_components(self):
        """Result should include adaptive component values for audit."""
        db = _make_db(interbank_on=3.5, usd_vnd=26100, fii_flow=300.0, breadth=55.0)
        lri = LiquidityRecoveryIndex(db).compute()
        raw = lri.components_raw
        assert "interbank_on" in raw
        assert "interbank_regime" in raw
        assert "interbank_regime_probs" in raw
        assert "interbank_hmm_fitted" in raw
        assert "interbank_p10" in raw
        assert "interbank_p90" in raw
        assert "interbank_alpha" in raw
        assert "usd_vnd" in raw
        assert "usdvnd_ma90" in raw
        assert "usdvnd_deviation_pct" in raw
        assert "fii_flow_10d" in raw
        assert "breadth_pct" in raw
        assert raw["interbank_on"] == 3.5
        assert raw["interbank_regime"] in ("EXPANSION", "NORMAL", "CONTRACTION")
        Path(db).unlink()

    def test_degraded_components_field_exists(self):
        """LRIResult must have degraded_components field."""
        db = _make_db(interbank_on=4.0, usd_vnd=26000, breadth=55)
        lri = LiquidityRecoveryIndex(db).compute()
        assert hasattr(lri, "degraded_components")
        assert isinstance(lri.degraded_components, list)
        Path(db).unlink()
