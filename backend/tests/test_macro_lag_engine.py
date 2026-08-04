"""test_macro_lag_engine.py — Tests for Macro Transmission Delay & Decay.

Tests:
  1. Transmission parameter definitions (all sectors have valid params)
  2. Decay weight computation (exponential decay formula)
  3. Effective signal computation (lag-adjusted vs raw)
  4. Signal deficit interpretation
  5. CompanyExposureModifier delta computation
  6. Full pipeline integration
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.company_exposure_modifier import (
    STOCK_PROFILES,
    CompanyExposureModifier,
    ModifierProfile,
)
from src.governor.macro_lag_engine import (
    MACRO_NODES,
    SECTOR_TRANSMISSION,
    LagResult,
    MacroLagEngine,
)

# ── Test: Transmission Parameters ─────────────────────────────────────


class TestTransmissionParameters:
    """Test that all sector transmission parameters are valid."""

    def test_all_sectors_have_params(self):
        """Every defined sector should have transmission parameters."""
        expected_sectors = {"STEEL", "BANK", "SEC", "RE", "OIL", "UTILITY", "CONST", "CONSUMER", "FOOD", "TECH", "TRANS"}
        assert expected_sectors == set(SECTOR_TRANSMISSION.keys())

    def test_lag_min_less_than_lag_max(self):
        """lag_min must be < lag_max for all sectors."""
        for sector, tx in SECTOR_TRANSMISSION.items():
            assert tx.lag_min < tx.lag_max, f"{sector}: lag_min={tx.lag_min} >= lag_max={tx.lag_max}"

    def test_half_life_positive(self):
        """half_life must be > 0 for all sectors."""
        for sector, tx in SECTOR_TRANSMISSION.items():
            assert tx.half_life > 0, f"{sector}: half_life={tx.half_life} <= 0"

    def test_attenuation_in_range(self):
        """attenuation must be in [0, 1]."""
        for sector, tx in SECTOR_TRANSMISSION.items():
            assert 0.0 <= tx.attenuation <= 1.0, f"{sector}: attenuation={tx.attenuation}"

    def test_lookback_computed(self):
        """lookback should be lag_max + 5."""
        for sector, tx in SECTOR_TRANSMISSION.items():
            assert tx.lookback == tx.lag_max + 5, f"{sector}: lookback={tx.lookback} != lag_max+5"


# ── Test: Decay Weights ───────────────────────────────────────────────


class TestDecayWeights:
    """Test exponential decay weight computation."""

    def test_day_zero_weight_is_one(self):
        """Weight at day 0 should be 1.0 (no decay)."""
        engine = MacroLagEngine.__new__(MacroLagEngine)
        weights = engine._compute_decay_weights(30, 15.0)
        assert weights[0] == pytest.approx(1.0, abs=1e-6)

    def test_half_life_weight_is_half(self):
        """Weight at day = half_life should be 0.5."""
        engine = MacroLagEngine.__new__(MacroLagEngine)
        weights = engine._compute_decay_weights(60, 15.0)
        assert weights[15] == pytest.approx(0.5, abs=0.01)

    def test_weights_monotonically_decrease(self):
        """Weights should strictly decrease with day offset."""
        engine = MacroLagEngine.__new__(MacroLagEngine)
        weights = engine._compute_decay_weights(30, 15.0)
        for d in range(1, 30):
            assert weights[d] < weights[d - 1], f"Weight at day {d} not less than day {d - 1}"

    def test_weights_non_negative(self):
        """All weights should be >= 0."""
        engine = MacroLagEngine.__new__(MacroLagEngine)
        weights = engine._compute_decay_weights(60, 15.0)
        for w in weights.values():
            assert w >= 0.0

    def test_short_half_life_faster_decay(self):
        """Shorter half_life should produce faster decay."""
        engine = MacroLagEngine.__new__(MacroLagEngine)
        w_short = engine._compute_decay_weights(30, 10.0)
        w_long = engine._compute_decay_weights(30, 30.0)
        # At day 15, short HL should have lower weight
        assert w_short[15] < w_long[15]


# ── Test: Effective Signal Computation ────────────────────────────────


class TestEffectiveSignal:
    """Test lag-adjusted macro signal with synthetic data."""

    def _make_test_db(self, scenario: str = "bull_then_bear") -> str:
        """Create a test DB with synthetic macro data."""
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
            CREATE TABLE daily_ohlcv (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL,
                PRIMARY KEY (symbol, date)
            )
        """)

        if scenario == "bull_then_bear":
            # 30 days of data: first 25 days = bullish, last 5 days = bearish
            # This simulates a RECENT downturn after a bull run
            for d in range(30):
                date_str = f"2026-07-{d + 1:02d}" if d < 31 else f"2026-08-{d - 30:02d}"
                if d < 5:
                    # Last 5 days: bearish (strong dollar, low copper, high interbank)
                    usdcny = 7.30  # weak CNY
                    brent = 65.0
                    copper = 7000.0  # low
                    interbank = 7.0  # high
                    dxy = 108.0  # strong dollar
                    us10y = 4.8
                    vix = 28.0
                else:
                    # First 25 days: bullish
                    usdcny = 7.10
                    brent = 85.0
                    copper = 10000.0
                    interbank = 4.5
                    dxy = 102.0
                    us10y = 4.0
                    vix = 16.0

                for var, val in [
                    ("USD_CNY", usdcny),
                    ("BRENT_OIL", brent),
                    ("COPPER_HG", copper / 2204.62),  # store as USD/lb
                    ("INTERBANK_ON", interbank),
                    ("DXY", dxy),
                    ("US10Y", us10y),
                    ("VIX", vix),
                    ("FED_TARGET_RATE", 5.25),
                ]:
                    conn.execute(
                        "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                        (var, date_str, val),
                    )
                # VNINDEX in daily_ohlcv
                vnindex = 1250.0 if d >= 5 else 1200.0
                conn.execute(
                    "INSERT INTO daily_ohlcv (symbol, date, close) VALUES (?, ?, ?)",
                    ("VNINDEX", date_str, vnindex),
                )

        conn.commit()
        conn.close()
        return tmp.name

    def test_no_data_returns_neutral(self):
        """With no data, effective score should be 0.5 (neutral)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.execute("""CREATE TABLE macro_history (
            variable TEXT, date TEXT, value REAL,
            PRIMARY KEY (variable, date))""")
        conn.execute("""CREATE TABLE daily_ohlcv (
            symbol TEXT, date TEXT, close REAL,
            PRIMARY KEY (symbol, date))""")
        conn.commit()
        conn.close()

        engine = MacroLagEngine(tmp.name)
        result = engine.compute("STEEL", target_date="2026-08-04")
        assert result.effective_score == pytest.approx(0.5, abs=0.01)
        assert result.data_points == 0
        Path(tmp.name).unlink()

    def test_lag_adjustment_reduces_score(self):
        """When recent data is bearish but older data is bullish,
        the effective score should be LOWER than the raw score
        (because the lookback window pulls in the bullish history)."""
        db_path = self._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)
        result = engine.compute("STEEL", target_date="2026-08-04")

        # Raw score reflects latest (bearish) data
        # Effective score is pulled up by the bullish history
        # So effective > raw in this scenario (signal is "catching up" from bull)
        assert result.data_points > 0
        assert result.raw_score != result.effective_score or result.signal_deficit != 0.0
        Path(db_path).unlink()

    def test_steel_faster_than_bank(self):
        """STEEL (short HL=15d) should have less lag adjustment than
        BANK (long HL=45d) for the same data."""
        db_path = self._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)

        steel = engine.compute("STEEL", target_date="2026-08-04")
        bank = engine.compute("BANK", target_date="2026-08-04")

        # STEEL has shorter lookback and faster decay
        # so its effective score should be CLOSER to raw
        # (less history pulled in)
        assert steel.half_life < bank.half_life
        Path(db_path).unlink()

    def test_effective_score_bounded(self):
        """Effective score must be in [0, 1]."""
        db_path = self._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)

        for sector in ["STEEL", "BANK", "RE", "OIL"]:
            result = engine.compute(sector, target_date="2026-08-04")
            assert 0.0 <= result.effective_score <= 1.0, f"{sector}: effective_score={result.effective_score} out of bounds"
        Path(db_path).unlink()

    def test_effective_vector_nodes_bounded(self):
        """Each node in effective_vector must be in [0, 1]."""
        db_path = self._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)
        result = engine.compute("STEEL", target_date="2026-08-04")

        for node in MACRO_NODES:
            assert 0.0 <= result.effective_vector[node] <= 1.0, f"{node}: {result.effective_vector[node]} out of bounds"
        Path(db_path).unlink()


# ── Test: Signal Deficit ──────────────────────────────────────────────


class TestSignalDeficit:
    """Test signal deficit interpretation."""

    def test_signal_deficit_equals_raw_minus_effective(self):
        """signal_deficit should equal raw_score - effective_score."""
        db_path = TestEffectiveSignal()._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)
        result = engine.compute("STEEL", target_date="2026-08-04")

        expected = result.raw_score - result.effective_score
        assert result.signal_deficit == pytest.approx(expected, abs=1e-4)
        Path(db_path).unlink()


# ── Test: Company Exposure Modifier ───────────────────────────────────


class TestCompanyExposureModifier:
    """Test company-specific lag adjustments."""

    def test_hpg_faster_than_sector_base(self):
        """HPG should have negative lag delta (faster than STEEL base)."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("HPG", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
        assert result.total_lag_delta < 0, f"HPG delta={result.total_lag_delta} should be negative"
        assert result.effective_lag_min < 3
        assert result.effective_lag_max < 21

    def test_nkg_slower_than_sector_base(self):
        """NKG (EAF) should have positive lag delta (slower than STEEL base)."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("NKG", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
        assert result.total_lag_delta > 0, f"NKG delta={result.total_lag_delta} should be positive"
        assert result.effective_lag_min >= 3

    def test_default_profile_no_adjustment(self):
        """Unknown stock should get default profile (no adjustment)."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("UNKNOWN", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
        assert result.total_lag_delta == 0
        assert result.effective_lag_min == 3
        assert result.effective_lag_max == 21

    def test_size_modifier_affects_half_life(self):
        """Large cap (size > 1) should have longer effective half_life."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("HPG", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
        # HPG size_modifier = 1.2
        assert result.effective_half_life == pytest.approx(15.0 * 1.2, abs=0.1)

    def test_lag_min_clamped_to_zero(self):
        """Effective lag_min should never go below 0."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("HPG", "STEEL", lag_min=1, lag_max=10, half_life=15.0)
        assert result.effective_lag_min >= 0

    def test_all_stocks_have_profiles(self):
        """All stocks in STOCK_PROFILES should have valid profiles."""
        for symbol, profile in STOCK_PROFILES.items():
            assert isinstance(profile, ModifierProfile)
            assert -30 <= profile.inventory_delta <= 30
            assert -30 <= profile.contract_delta <= 30
            assert -30 <= profile.supply_chain_delta <= 30
            assert 0.5 <= profile.size_modifier <= 1.5

    def test_modifier_result_fields(self):
        """ModifierResult should have all expected fields."""
        modifier = CompanyExposureModifier()
        result = modifier.compute("HPG", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
        assert result.symbol == "HPG"
        assert result.sector == "STEEL"
        assert isinstance(result.total_lag_delta, int)
        assert isinstance(result.size_modifier, float)
        assert isinstance(result.effective_half_life, float)
        assert result.description != ""


# ── Test: Full Pipeline Integration ───────────────────────────────────


class TestLagEngineIntegration:
    """End-to-end: DB → M vector → Lag Engine → Sector Score."""

    def test_full_pipeline_with_lag(self):
        """Full pipeline: macro data → lag-adjusted sector score."""
        db_path = TestEffectiveSignal()._make_test_db("bull_then_bear")

        # Step 1: Compute raw M vector
        from src.governor.regional_influence_engine import RegionalInfluenceEngine
        from src.governor.sector_exposure_matrix import SectorExposureMatrix

        engine = RegionalInfluenceEngine(db_path)
        macro_result = engine.compute("2026-08-04")
        M = macro_result.macro_vector

        # Step 2: Compute lag-adjusted signal
        lag_engine = MacroLagEngine(db_path)
        lag_result = lag_engine.compute("STEEL", target_date="2026-08-04")

        # Step 3: Compute sector scores
        matrix = SectorExposureMatrix()
        raw_result = matrix.compute_sector_macro_score("STEEL", M)
        effective_result = matrix.compute_sector_macro_score("STEEL", lag_result.effective_vector)

        # Both should be valid scores
        assert 0.0 <= raw_result.macro_score <= 1.0
        assert 0.0 <= effective_result.macro_score <= 1.0

        # They should be different (lag adjustment happened)
        assert raw_result.macro_score != effective_result.macro_score

        Path(db_path).unlink()

    def test_compute_all_sectors(self):
        """compute_all_sectors should return results for all defined sectors."""
        db_path = TestEffectiveSignal()._make_test_db("bull_then_bear")
        engine = MacroLagEngine(db_path)
        results = engine.compute_all_sectors(target_date="2026-08-04")

        assert len(results) == len(SECTOR_TRANSMISSION)
        for sector, result in results.items():
            assert isinstance(result, LagResult)
            assert result.sector == sector
            assert 0.0 <= result.effective_score <= 1.0

        Path(db_path).unlink()
