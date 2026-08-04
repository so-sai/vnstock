"""test_interaction_engine.py — Tests for Non-linear Macro Synergy & Friction.

Tests:
  1. Interaction rule activation thresholds
  2. Multiplier clamping [0.70, 1.35]
  3. Excess scaling (smooth activation)
  4. CHINA_COMMODITY_SUPER_CYCLE activation
  5. DOUBLE_LIQUIDITY_EASING activation
  6. COMMODITY_COST_SQUEEZE (negative friction)
  7. RISK_OFF_DIVERGENCE (negative friction)
  8. STAGFLATION_SQUEEZE (negative friction)
  9. No interaction when macro is neutral
 10. Multiple simultaneous interactions
 11. compute_all_sectors coverage
 12. Integration with LagEngine
"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.interaction_engine import (
    INTERACTION_RULES,
    MULTIPLIER_HIGH,
    MULTIPLIER_LOW,
    InteractionEngine,
    InteractionResult,
)


# ── Test: Rule Definitions ────────────────────────────────────────────


class TestRuleDefinitions:
    """Test that interaction rules are well-formed."""

    def test_rules_exist(self):
        """Should have at least 5 interaction rules."""
        assert len(INTERACTION_RULES) >= 5

    def test_all_rules_have_required_fields(self):
        """Every rule must have name, pair, thresholds, impact, type."""
        for rule in INTERACTION_RULES:
            assert "name" in rule
            assert "pair" in rule
            assert "thresholds" in rule
            assert "impact" in rule
            assert "type" in rule
            assert rule["type"] in ("POSITIVE_BOOM", "NEGATIVE_FRICTION")

    def test_all_impact_values_are_numeric(self):
        """Impact values must be float/int."""
        for rule in INTERACTION_RULES:
            for sector, impact in rule["impact"].items():
                assert isinstance(impact, (int, float))
                assert 0.5 <= impact <= 2.0, f"{rule['name']}.{sector} impact={impact}"


# ── Test: Multiplier Clamping ─────────────────────────────────────────


class TestMultiplierClamping:
    """Test that multiplier is always within safe bounds."""

    def test_neutral_macro_gives_multiplier_one(self):
        """Neutral macro vector should produce multiplier = 1.0."""
        engine = InteractionEngine()
        neutral = {"US_Liquidity": 0.5, "China_Economy": 0.5,
                   "Commodity_Cycle": 0.5, "Domestic_Liquidity": 0.5}
        result = engine.compute(neutral, "STEEL")
        assert result.multiplier == pytest.approx(1.0, abs=0.01)

    def test_multiplier_never_below_070(self):
        """Multiplier must not go below 0.70."""
        engine = InteractionEngine()
        # Worst case: all frictions active
        bear = {"US_Liquidity": 0.1, "China_Economy": 0.1,
                "Commodity_Cycle": 0.9, "Domestic_Liquidity": 0.9}
        for sector in ["BANK", "RE", "SEC", "CONSUMER", "FOOD"]:
            result = engine.compute(bear, sector)
            assert result.multiplier >= MULTIPLIER_LOW

    def test_multiplier_never_above_135(self):
        """Multiplier must not exceed 1.35."""
        engine = InteractionEngine()
        # Best case: all synergies active
        bull = {"US_Liquidity": 0.9, "China_Economy": 0.9,
                "Commodity_Cycle": 0.9, "Domestic_Liquidity": 0.9}
        for sector in ["STEEL", "BANK", "RE", "SEC"]:
            result = engine.compute(bull, sector)
            assert result.multiplier <= MULTIPLIER_HIGH


# ── Test: CHINA_COMMODITY_SUPER_CYCLE ────────────────────────────────


class TestChinaCommoditySuperCycle:
    """Test China + Commodity synergy for STEEL."""

    def test_activation_both_above_threshold(self):
        """Should activate when both China > 0.55 and Commodity > 0.55."""
        engine = InteractionEngine()
        macro = {"China_Economy": 0.70, "Commodity_Cycle": 0.74,
                 "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        result = engine.compute(macro, "STEEL")
        assert result.multiplier > 1.0
        assert any(s["rule"] == "CHINA_COMMODITY_SUPER_CYCLE"
                   for s in result.active_synergies)

    def test_steel_higher_impact_than_oil(self):
        """STEEL should have higher impact than OIL in this rule."""
        engine = InteractionEngine()
        macro = {"China_Economy": 0.70, "Commodity_Cycle": 0.74,
                 "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        steel = engine.compute(macro, "STEEL")
        oil = engine.compute(macro, "OIL")
        assert steel.multiplier > oil.multiplier

    def test_no_activation_below_threshold(self):
        """Should NOT activate when China < 0.55."""
        engine = InteractionEngine()
        macro = {"China_Economy": 0.40, "Commodity_Cycle": 0.74,
                 "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        result = engine.compute(macro, "STEEL")
        assert not any(s["rule"] == "CHINA_COMMODITY_SUPER_CYCLE"
                       for s in result.active_synergies)

    def test_excess_scaling(self):
        """Higher excess should produce higher multiplier (up to cap)."""
        engine = InteractionEngine()
        macro_low = {"China_Economy": 0.60, "Commodity_Cycle": 0.60,
                     "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        macro_high = {"China_Economy": 0.80, "Commodity_Cycle": 0.85,
                      "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        low = engine.compute(macro_low, "STEEL")
        high = engine.compute(macro_high, "STEEL")
        assert high.multiplier >= low.multiplier


# ── Test: DOUBLE_LIQUIDITY_EASING ────────────────────────────────────


class TestDoubleLiquidityEasing:
    """Test US + Domestic liquidity synergy for BANK/RE/SEC."""

    def test_activation(self):
        """Should activate when both US > 0.55 and Domestic > 0.55."""
        engine = InteractionEngine()
        macro = {"US_Liquidity": 0.70, "Domestic_Liquidity": 0.65,
                 "China_Economy": 0.5, "Commodity_Cycle": 0.5}
        result = engine.compute(macro, "SEC")
        assert result.multiplier > 1.0
        assert any(s["rule"] == "DOUBLE_LIQUIDITY_EASING"
                   for s in result.active_synergies)

    def test_sec_highest_impact(self):
        """SEC should have highest impact (1.40) in this rule."""
        engine = InteractionEngine()
        macro = {"US_Liquidity": 0.70, "Domestic_Liquidity": 0.65,
                 "China_Economy": 0.5, "Commodity_Cycle": 0.5}
        sec = engine.compute(macro, "SEC")
        re = engine.compute(macro, "RE")
        bank = engine.compute(macro, "BANK")
        assert sec.multiplier >= re.multiplier
        assert sec.multiplier >= bank.multiplier


# ── Test: Negative Frictions ──────────────────────────────────────────


class TestNegativeFrictions:
    """Test negative friction rules (COMMODITY_COST, RISK_OFF, STAGFLATION)."""

    def test_commodity_cost_squeeze(self):
        """High commodity + tight domestic liquidity should reduce RE/BANK."""
        engine = InteractionEngine()
        macro = {"Commodity_Cycle": 0.80, "Domestic_Liquidity": 0.25,
                 "US_Liquidity": 0.5, "China_Economy": 0.5}
        result = engine.compute(macro, "RE")
        assert result.multiplier < 1.0
        assert any(s["type"] == "NEGATIVE_FRICTION"
                   for s in result.active_synergies)

    def test_risk_off_divergence(self):
        """Fed tight + SBV loose should reduce SEC/BANK."""
        engine = InteractionEngine()
        macro = {"US_Liquidity": 0.20, "Domestic_Liquidity": 0.70,
                 "China_Economy": 0.5, "Commodity_Cycle": 0.5}
        result = engine.compute(macro, "SEC")
        assert result.multiplier < 1.0

    def test_stagflation_squeeze(self):
        """High commodity + tight US liquidity should reduce CONSUMER/FOOD."""
        engine = InteractionEngine()
        macro = {"Commodity_Cycle": 0.85, "US_Liquidity": 0.20,
                 "China_Economy": 0.5, "Domestic_Liquidity": 0.5}
        result = engine.compute(macro, "CONSUMER")
        assert result.multiplier < 1.0


# ── Test: No Interaction ──────────────────────────────────────────────


class TestNoInteraction:
    """Test that neutral macro produces no interactions."""

    def test_neutral_no_synergies(self):
        """Neutral macro should have zero active synergies."""
        engine = InteractionEngine()
        neutral = {"US_Liquidity": 0.5, "China_Economy": 0.5,
                   "Commodity_Cycle": 0.5, "Domestic_Liquidity": 0.5}
        for sector in ["STEEL", "BANK", "RE", "SEC", "OIL"]:
            result = engine.compute(neutral, sector)
            assert len(result.active_synergies) == 0
            assert result.multiplier == pytest.approx(1.0, abs=0.01)


# ── Test: Multiple Simultaneous Interactions ──────────────────────────


class TestMultipleInteractions:
    """Test that multiple rules can fire simultaneously."""

    def test_two_positive_synergies(self):
        """China boom + liquidity easing can both fire."""
        engine = InteractionEngine()
        macro = {"US_Liquidity": 0.70, "China_Economy": 0.70,
                 "Commodity_Cycle": 0.70, "Domestic_Liquidity": 0.70}
        # STEEL gets CHINA_COMMODITY (+) but not DOUBLE_LIQUITY
        steel = engine.compute(macro, "STEEL")
        # SEC gets DOUBLE_LIQUITY (+) but not CHINA_COMMODITY
        sec = engine.compute(macro, "SEC")
        assert steel.multiplier > 1.0
        assert sec.multiplier > 1.0

    def test_positive_and_negative_cancel(self):
        """China boom (STEEL+) and cost squeeze (STEEL neutral) can coexist."""
        engine = InteractionEngine()
        # China high + Commodity high → CHINA_COMMODITY activates
        # Commodity high + Domestic low → COST_SQUEEZE activates
        macro = {"China_Economy": 0.70, "Commodity_Cycle": 0.80,
                 "US_Liquidity": 0.5, "Domestic_Liquidity": 0.25}
        result = engine.compute(macro, "RE")
        # RE not in CHINA_COMMODITY, but in COST_SQUEEZE (negative)
        assert result.multiplier <= 1.0


# ── Test: compute_all_sectors ─────────────────────────────────────────


class TestComputeAllSectors:
    """Test compute_all_sectors coverage."""

    def test_covers_all_rule_sectors(self):
        """Should return results for all sectors mentioned in rules."""
        engine = InteractionEngine()
        macro = {"US_Liquidity": 0.5, "China_Economy": 0.5,
                 "Commodity_Cycle": 0.5, "Domestic_Liquidity": 0.5}
        results = engine.compute_all_sectors(macro)
        # At least STEEL, OIL, TRANS, BANK, RE, SEC, CONSUMER, FOOD
        assert len(results) >= 8
        for sector, result in results.items():
            assert isinstance(result, InteractionResult)
            assert 0.70 <= result.multiplier <= 1.35


# ── Test: Result Dataclass ────────────────────────────────────────────


class TestResultDataclass:
    """Test InteractionResult fields."""

    def test_result_fields(self):
        """Result should have all expected fields."""
        engine = InteractionEngine()
        macro = {"China_Economy": 0.70, "Commodity_Cycle": 0.74,
                 "US_Liquidity": 0.5, "Domestic_Liquidity": 0.5}
        result = engine.compute(macro, "STEEL")
        assert result.sector == "STEEL"
        assert isinstance(result.multiplier, float)
        assert isinstance(result.raw_multiplier, float)
        assert isinstance(result.active_synergies, list)
        assert isinstance(result.n_positive, int)
        assert isinstance(result.n_negative, int)
        assert result.description != ""


# ── Test: Integration with full pipeline ──────────────────────────────


class TestIntegration:
    """End-to-end: M vector → LagEngine → InteractionEngine → adjusted score."""

    def test_full_chain(self):
        """InteractionEngine multiplier should adjust the lag-adjusted score."""
        from src.governor.sector_exposure_matrix import SectorExposureMatrix

        matrix = SectorExposureMatrix()
        engine = InteractionEngine()

        # Simulate a China boom scenario
        macro = {"US_Liquidity": 0.35, "China_Economy": 0.70,
                 "Commodity_Cycle": 0.74, "Domestic_Liquidity": 0.50}

        # Raw dot product
        raw = matrix.compute_sector_macro_score("STEEL", macro)

        # Interaction multiplier
        ix = engine.compute(macro, "STEEL")

        # Adjusted score
        adjusted = raw.macro_score * ix.multiplier
        adjusted = max(0.0, min(1.0, adjusted))

        # In China boom, STEEL should get amplified
        assert ix.multiplier > 1.0
        assert adjusted > raw.macro_score
