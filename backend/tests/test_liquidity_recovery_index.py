"""test_liquidity_recovery_index.py — TDD tests for LRI module.

Tests 3 macro cycles:
  - 2021 (post-COVID recovery): LRI high — plentiful liquidity
  - 2022 (Fed tightening): LRI low — liquidity stress
  - 2023-2024 (volatile): LRI oscillating — probe mode

Also tests edge cases: missing data, extreme values, normalization bounds.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.liquidity_recovery_index import (
    INTERBANK_HIGH,
    INTERBANK_LOW,
    LiquidityRecoveryIndex,
    _normalize,
)

# ── Helper: create in-memory DB with macro data ──────────────────────


def _make_db(interbank_on=None, usd_vnd=None, fii_flow=None, breadth=None):
    """Create a temporary SQLite DB with the given macro data."""
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

    # Seed data
    if interbank_on is not None:
        conn.execute(
            "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
            ("INTERBANK_ON", "2026-08-03", interbank_on),
        )
    if usd_vnd is not None:
        conn.execute(
            "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
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


# ── Test: Normalization function ─────────────────────────────────────


class TestNormalize:
    def test_midpoint(self):
        """Midpoint of range should normalize to 0.5."""
        mid = (INTERBANK_LOW + INTERBANK_HIGH) / 2
        assert _normalize(mid, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(0.5, abs=0.01)

    def test_inverted_low_value_gives_high_score(self):
        """Low interbank rate (good liquidity) should give high score."""
        assert _normalize(2.0, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(1.0, abs=0.01)

    def test_inverted_high_value_gives_low_score(self):
        """High interbank rate (tight liquidity) should give low score."""
        assert _normalize(6.0, INTERBANK_LOW, INTERBANK_HIGH, invert=True) == pytest.approx(0.0, abs=0.01)

    def test_direct_high_value_gives_high_score(self):
        """High breadth (good) should give high score."""
        assert _normalize(70.0, 30.0, 70.0, invert=False) == pytest.approx(1.0, abs=0.01)

    def test_none_gives_neutral(self):
        """Missing data should return neutral 0.5."""
        assert _normalize(None, 0, 100) == 0.5

    def test_clamped_above(self):
        """Value above range should clamp to 1.0."""
        assert _normalize(100.0, 0.0, 50.0) == 1.0

    def test_clamped_below(self):
        """Value below range should clamp to 0.0."""
        assert _normalize(-10.0, 0.0, 50.0) == 0.0


# ── Test: LRI with different macro regimes ───────────────────────────


class TestLRIRegimes:
    """Test LRI across 3 macro cycles."""

    def test_2021_recovery_high_liquidity(self):
        """2021: Low interbank (1.5%), stable FX, positive FII → LRI high."""
        db = _make_db(
            interbank_on=1.5,  # Very low — plentiful liquidity
            usd_vnd=23800,  # Below equilibrium — stable
            fii_flow=800.0,  # Strong foreign buying
            breadth=65.0,  # Broad market participation
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri >= 0.7, f"2021 recovery: LRI should be high, got {lri.lri}"
        assert lri.regime == "AGGRESSIVE" or lri.regime == "PROBE"
        assert lri.max_allocation_pct > 50.0
        Path(db).unlink()

    def test_2022_fed_tightening_stress(self):
        """2022: High interbank (7%), weak FX, FII outflow → LRI low."""
        db = _make_db(
            interbank_on=7.0,  # Very high — tight liquidity
            usd_vnd=25500,  # Above stress threshold
            fii_flow=-1500.0,  # Heavy foreign selling
            breadth=25.0,  # Narrow market
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri <= 0.3, f"2022 tightening: LRI should be low, got {lri.lri}"
        assert lri.regime == "DEFENSIVE"
        assert lri.max_allocation_pct == 0.0
        Path(db).unlink()

    def test_2023_volatile_probe_mode(self):
        """2023: Moderate interbank (4%), stable FX, mixed FII → LRI mid."""
        db = _make_db(
            interbank_on=4.0,  # Moderate
            usd_vnd=24200,  # Slightly above equilibrium
            fii_flow=-200.0,  # Mild outflow
            breadth=50.0,  # Neutral breadth
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.3 <= lri.lri <= 0.8, f"2023 volatile: LRI should be mid, got {lri.lri}"
        assert lri.regime == "PROBE"
        assert 0 < lri.max_allocation_pct < 100
        Path(db).unlink()


# ── Test: Edge cases ─────────────────────────────────────────────────


class TestLRIEdgeCases:
    def test_missing_data_returns_neutral(self):
        """Empty DB should return neutral LRI."""
        db = _make_db()  # no data
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.3 <= lri.lri <= 0.7, f"Missing data: LRI should be neutral, got {lri.lri}"
        Path(db).unlink()

    def test_extreme_interbank_floor(self):
        """Interbank at 15% (crisis) should give very low LRI."""
        db = _make_db(
            interbank_on=15.0,
            usd_vnd=26000,
            fii_flow=-3000.0,
            breadth=15.0,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri < 0.2, f"Extreme interbank: LRI should be very low, got {lri.lri}"
        assert lri.regime == "DEFENSIVE"
        Path(db).unlink()

    def test_extreme_breadth_high(self):
        """Breadth at 90% (strong rally) should boost LRI."""
        db = _make_db(
            interbank_on=3.0,
            usd_vnd=24000,
            fii_flow=1000.0,
            breadth=90.0,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert lri.lri >= 0.6, f"High breadth: LRI should be elevated, got {lri.lri}"
        Path(db).unlink()

    def test_weights_sum_to_one(self):
        """Weights must sum to 1.0."""
        total = sum(LiquidityRecoveryIndex.WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001), f"Weights sum to {total}, expected 1.0"

    def test_lri_bounded_zero_one(self):
        """LRI must always be in [0, 1]."""
        db = _make_db(
            interbank_on=100.0,  # extreme
            usd_vnd=50000.0,  # extreme
            fii_flow=-10000.0,  # extreme
            breadth=0.0,  # extreme
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert 0.0 <= lri.lri <= 1.0, f"LRI out of bounds: {lri.lri}"
        Path(db).unlink()

    def test_probe_regime_allocation_scales_with_lri(self):
        """In PROBE regime, max_allocation should equal LRI * 100."""
        db = _make_db(
            interbank_on=4.5,
            usd_vnd=24500,
            fii_flow=-100.0,
            breadth=45.0,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        if lri.regime == "PROBE":
            assert lri.max_allocation_pct == pytest.approx(lri.lri * 100, abs=1.0)
        Path(db).unlink()

    def test_audit_trail_components(self):
        """Result should include raw component values for audit."""
        db = _make_db(
            interbank_on=3.5,
            usd_vnd=24100,
            fii_flow=300.0,
            breadth=55.0,
        )
        lri = LiquidityRecoveryIndex(db).compute()
        assert "interbank_on" in lri.components_raw
        assert "usd_vnd" in lri.components_raw
        assert "fii_flow_10d" in lri.components_raw
        assert "breadth_pct" in lri.components_raw
        assert lri.components_raw["interbank_on"] == 3.5
        Path(db).unlink()
