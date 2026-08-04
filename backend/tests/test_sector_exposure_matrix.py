"""test_sector_exposure_matrix.py — Tests for Cross-Sector Macro Exposure.

Tests:
  1. Dot product computation (M · W_i)
  2. Weight vector normalization (sums to 1.0)
  3. Score bounds [0, 1]
  4. Sector ranking
  5. MoS adjustment formula
  6. Regional influence engine integration
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.regional_influence_engine import (
    RegionalInfluenceEngine,
    _normalize,
)
from src.governor.sector_exposure_matrix import (
    MACRO_NODES,
    SECTOR_EXPOSURE_WEIGHTS,
    SectorExposureMatrix,
)

# ── Test: Weight Vector Normalization ─────────────────────────────────


class TestWeightNormalization:
    """Test that exposure weight vectors sum to 1.0."""

    def test_all_sectors_sum_to_one(self):
        """Every sector's weights should sum to 1.0."""
        for sector, weights in SECTOR_EXPOSURE_WEIGHTS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-6, f"{sector} weights sum to {total}, expected 1.0"

    def test_all_nodes_have_weights(self):
        """Every sector should have weights for all 4 macro nodes."""
        for sector, weights in SECTOR_EXPOSURE_WEIGHTS.items():
            for node in MACRO_NODES:
                assert node in weights, f"{sector} missing weight for {node}"

    def test_weights_are_non_negative(self):
        """All weights should be >= 0."""
        for weights in SECTOR_EXPOSURE_WEIGHTS.values():
            for w in weights.values():
                assert w >= 0.0


# ── Test: Dot Product Computation ─────────────────────────────────────


class TestDotProduct:
    """Test sector macro score via dot product M · W_i."""

    def test_neutral_macro_gives_neutral_score(self):
        """When M = [0.5, 0.5, 0.5, 0.5], all sectors should score 0.5."""
        matrix = SectorExposureMatrix()
        neutral = {node: 0.5 for node in MACRO_NODES}

        for sector in SECTOR_EXPOSURE_WEIGHTS:
            result = matrix.compute_sector_macro_score(sector, neutral)
            assert abs(result.macro_score - 0.5) < 1e-6, f"{sector} scored {result.macro_score} with neutral M"

    def test_extreme_bull_macro(self):
        """When M = [1.0, 1.0, 1.0, 1.0], all sectors should score 1.0."""
        matrix = SectorExposureMatrix()
        bull = {node: 1.0 for node in MACRO_NODES}

        for sector in SECTOR_EXPOSURE_WEIGHTS:
            result = matrix.compute_sector_macro_score(sector, bull)
            assert abs(result.macro_score - 1.0) < 1e-6

    def test_extreme_bear_macro(self):
        """When M = [0.0, 0.0, 0.0, 0.0], all sectors should score 0.0."""
        matrix = SectorExposureMatrix()
        bear = {node: 0.0 for node in MACRO_NODES}

        for sector in SECTOR_EXPOSURE_WEIGHTS:
            result = matrix.compute_sector_macro_score(sector, bear)
            assert abs(result.macro_score - 0.0) < 1e-6

    def test_bank_sensitive_to_domestic(self):
        """BANK should have highest weight on Domestic_Liquidity."""
        matrix = SectorExposureMatrix()
        weights = matrix.get_exposure_weights("BANK")
        assert weights["Domestic_Liquidity"] > weights["US_Liquidity"]
        assert weights["Domestic_Liquidity"] > weights["China_Economy"]

    def test_steel_sensitive_to_china(self):
        """STEEL should have highest weight on China_Economy."""
        matrix = SectorExposureMatrix()
        weights = matrix.get_exposure_weights("STEEL")
        assert weights["China_Economy"] > weights["US_Liquidity"]
        assert weights["China_Economy"] > weights["Domestic_Liquidity"]


# ── Test: Score Bounds ────────────────────────────────────────────────


class TestScoreBounds:
    """Test that macro scores are bounded in [0, 1]."""

    def test_random_macro_vector(self):
        """Random macro vectors should produce scores in [0, 1]."""
        import random

        random.seed(42)

        matrix = SectorExposureMatrix()
        for _ in range(100):
            macro = {node: random.random() for node in MACRO_NODES}
            for sector in SECTOR_EXPOSURE_WEIGHTS:
                result = matrix.compute_sector_macro_score(sector, macro)
                assert 0.0 <= result.macro_score <= 1.0, f"{sector} score {result.macro_score} out of bounds"


# ── Test: Sector Ranking ─────────────────────────────────────────────


class TestSectorRanking:
    """Test sector ranking by macro score."""

    def test_ranking_length(self):
        """Ranking should include all defined sectors."""
        matrix = SectorExposureMatrix()
        macro = {node: 0.5 for node in MACRO_NODES}
        ranking = matrix.get_sector_ranking(macro)
        assert len(ranking) == len(SECTOR_EXPOSURE_WEIGHTS)

    def test_ranking_sorted_descending(self):
        """Default ranking should be descending by score."""
        import random

        random.seed(42)
        matrix = SectorExposureMatrix()
        macro = {node: random.random() for node in MACRO_NODES}
        ranking = matrix.get_sector_ranking(macro)
        scores = [s for _, s in ranking]
        assert scores == sorted(scores, reverse=True)


# ── Test: MoS Adjustment ──────────────────────────────────────────────


class TestMoSAdjustment:
    """Test Margin of Safety adjustment formula."""

    def test_neutral_macro_no_adjustment(self):
        """Neutral macro (0.5) should not change MoS."""
        matrix = SectorExposureMatrix()
        neutral = {node: 0.5 for node in MACRO_NODES}
        adjusted = matrix.compute_mos_adjustment("BANK", neutral, base_mos=1.0)
        assert abs(adjusted - 1.0) < 1e-4

    def test_bull_macro_increases_mos(self):
        """Bull macro (1.0) should increase MoS."""
        matrix = SectorExposureMatrix()
        bull = {node: 1.0 for node in MACRO_NODES}
        adjusted = matrix.compute_mos_adjustment("BANK", bull, base_mos=1.0)
        assert adjusted > 1.0

    def test_bear_macro_decreases_mos(self):
        """Bear macro (0.0) should decrease MoS."""
        matrix = SectorExposureMatrix()
        bear = {node: 0.0 for node in MACRO_NODES}
        adjusted = matrix.compute_mos_adjustment("BANK", bear, base_mos=1.0)
        assert adjusted < 1.0

    def test_mos_clamped(self):
        """MoS adjustment should be clamped to [0.5, 1.5]."""
        matrix = SectorExposureMatrix()
        bear = {node: 0.0 for node in MACRO_NODES}
        adjusted = matrix.compute_mos_adjustment("BANK", bear, base_mos=3.0)
        assert adjusted <= 1.5 * 3.0  # clamped


# ── Test: Regional Influence Engine ───────────────────────────────────


class TestRegionalInfluenceEngine:
    """Test macro state vector computation."""

    def test_normalize_function(self):
        """_normalize should map values to [0, 1]."""
        assert _normalize(3.0, 2.0, 8.0) == pytest.approx(0.1667, abs=0.01)
        assert _normalize(5.0, 2.0, 8.0) == pytest.approx(0.5, abs=0.01)
        assert _normalize(8.0, 2.0, 8.0) == pytest.approx(1.0, abs=0.01)

    def test_normalize_inverted(self):
        """Inverted normalization should flip the result."""
        assert _normalize(2.0, 2.0, 8.0, invert=True) == pytest.approx(1.0, abs=0.01)
        assert _normalize(8.0, 2.0, 8.0, invert=True) == pytest.approx(0.0, abs=0.01)

    def test_normalize_none_returns_neutral(self):
        """None value should return 0.5 (neutral)."""
        assert _normalize(None, 2.0, 8.0) == 0.5

    def test_macro_vector_keys(self):
        """Computed macro vector should have all 4 nodes."""
        # Create a minimal DB for testing
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
        # Insert some test data
        for var, val in [
            ("INTERBANK_ON", 5.72),
            ("DXY", 103.5),
            ("US10Y", 4.25),
            ("BRENT_OIL", 82.0),
            ("COPPER_HG", 9500.0),
            ("USD_CNY", 7.15),
        ]:
            conn.execute(
                "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                (var, "2026-08-03", val),
            )
        conn.commit()
        conn.close()

        engine = RegionalInfluenceEngine(tmp.name)
        result = engine.compute("2026-08-03")

        assert isinstance(result.macro_vector, dict)
        for node in MACRO_NODES:
            assert node in result.macro_vector
            assert 0.0 <= result.macro_vector[node] <= 1.0

        Path(tmp.name).unlink()


# ── Test: Integration ─────────────────────────────────────────────────


class TestIntegration:
    """End-to-end: Engine → Matrix → Score."""

    def test_full_pipeline(self):
        """Full pipeline: macro data → M vector → sector scores."""
        # Create test DB
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
        for var, val in [
            ("INTERBANK_ON", 5.72),
            ("DXY", 103.5),
            ("US10Y", 4.25),
            ("BRENT_OIL", 82.0),
            ("COPPER_HG", 9500.0),
            ("USD_CNY", 7.15),
        ]:
            conn.execute(
                "INSERT INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                (var, "2026-08-03", val),
            )
        conn.commit()
        conn.close()

        # Compute M vector
        engine = RegionalInfluenceEngine(tmp.name)
        macro_result = engine.compute("2026-08-03")
        M = macro_result.macro_vector

        # Compute sector scores
        matrix = SectorExposureMatrix()
        scores = matrix.compute_all_sector_scores(M)

        # Verify all sectors have scores
        assert len(scores) == len(SECTOR_EXPOSURE_WEIGHTS)

        # Verify BANK and STEEL have different scores
        bank_score = scores["BANK"].macro_score
        steel_score = scores["STEEL"].macro_score
        assert bank_score != steel_score, "Different sectors should have different scores"

        # Verify components sum to total
        for sector, result in scores.items():
            component_sum = sum(result.components.values())
            assert abs(component_sum - result.macro_score) < 1e-5

        Path(tmp.name).unlink()
