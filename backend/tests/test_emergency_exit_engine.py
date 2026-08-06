"""test_emergency_exit_engine.py — TDD tests for Emergency Exit Engine v2.

Tests managed emergency exit protocol when LRI < 0.30:
  - Buy lock activation
  - Trailing stop tightening (-5% → -2%)
  - Priority ranking (High Beta / Low MoS first)
  - Exit order generation
  - ADV liquidity-aware order slicing (position > 15% ADV → TWAP)
"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.emergency_exit_engine import (
    EmergencyExitEngine,
    ExitPriority,
)


class TestEmergencyExitBasic:
    """Basic functionality tests."""

    def test_no_exit_when_lri_above_threshold(self):
        """No exit orders when LRI >= 0.30."""
        engine = EmergencyExitEngine()
        result = engine.evaluate(lri_score=0.50, open_positions={"HPG": {"shares": 100}})
        assert result.is_defensive is False
        assert result.buy_locked is False
        assert len(result.exit_orders) == 0

    def test_exit_when_lri_below_threshold(self):
        """Exit orders generated when LRI < 0.30."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 100, "beta": 1.5, "mos_pct": 15.0}}
        result = engine.evaluate(lri_score=0.25, open_positions=positions)
        assert result.is_defensive is True
        assert result.buy_locked is True
        assert len(result.exit_orders) == 1

    def test_no_exit_when_no_positions(self):
        """No exit orders when no open positions."""
        engine = EmergencyExitEngine()
        result = engine.evaluate(lri_score=0.20, open_positions=None)
        assert result.is_defensive is True
        assert result.buy_locked is True
        assert len(result.exit_orders) == 0

    def test_empty_positions_dict(self):
        """No exit orders when positions dict is empty."""
        engine = EmergencyExitEngine()
        result = engine.evaluate(lri_score=0.20, open_positions={})
        assert result.is_defensive is True
        assert len(result.exit_orders) == 0


class TestTrailingStopTightening:
    """Trailing stop tightening tests."""

    def test_stops_tightened_to_minus_2pct(self):
        """When DEFENSIVE, all stops tightened from -5% to -2%."""
        engine = EmergencyExitEngine()
        positions = {"VNM": {"shares": 50, "beta": 0.5, "mos_pct": 60.0}}
        result = engine.evaluate(lri_score=0.25, open_positions=positions)
        assert result.tightened_stops["VNM"] == -0.02

    def test_exit_order_stop_tightened(self):
        """ExitOrder should have tightened_stop_pct = -0.02."""
        engine = EmergencyExitEngine()
        positions = {"FPT": {"shares": 200, "beta": 1.1, "mos_pct": 40.0}}
        result = engine.evaluate(lri_score=0.28, open_positions=positions)
        assert result.exit_orders[0].tightened_stop_pct == -0.02

    def test_stops_empty_when_not_defensive(self):
        """No tightened stops when not DEFENSIVE."""
        engine = EmergencyExitEngine()
        positions = {"VCB": {"shares": 100, "beta": 0.8, "mos_pct": 55.0}}
        result = engine.evaluate(lri_score=0.50, open_positions=positions)
        assert len(result.tightened_stops) == 0


class TestPriorityRanking:
    """Exit priority ranking tests (sell high-risk first)."""

    def test_critical_before_high(self):
        """CRITICAL priority should come before HIGH."""
        engine = EmergencyExitEngine()
        positions = {
            "RISKY": {"shares": 100, "beta": 2.0, "mos_pct": 10.0},  # CRITICAL
            "MODERATE": {"shares": 100, "beta": 1.3, "mos_pct": 25.0},  # HIGH
        }
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        symbols = [o.symbol for o in result.exit_orders]
        assert symbols[0] == "RISKY"
        assert symbols[1] == "MODERATE"

    def test_critical_beta_and_low_mos(self):
        """Beta > 1.5 AND MoS < 20% → CRITICAL."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 100, "beta": 1.6, "mos_pct": 15.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].priority == ExitPriority.CRITICAL

    def test_high_beta_only(self):
        """Beta > 1.2 (regardless of MoS) → HIGH."""
        engine = EmergencyExitEngine()
        positions = {"Y": {"shares": 100, "beta": 1.3, "mos_pct": 50.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].priority == ExitPriority.HIGH

    def test_high_low_mos_only(self):
        """MoS < 30% (regardless of Beta) → HIGH."""
        engine = EmergencyExitEngine()
        positions = {"Z": {"shares": 100, "beta": 0.8, "mos_pct": 25.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].priority == ExitPriority.HIGH

    def test_medium_beta_and_mos(self):
        """Beta > 0.8 AND MoS < 50% → MEDIUM."""
        engine = EmergencyExitEngine()
        positions = {"W": {"shares": 100, "beta": 0.9, "mos_pct": 45.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].priority == ExitPriority.MEDIUM

    def test_low_bluechip(self):
        """Low beta + high MoS → LOW (hold if possible)."""
        engine = EmergencyExitEngine()
        positions = {"VCB": {"shares": 100, "beta": 0.6, "mos_pct": 60.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].priority == ExitPriority.LOW

    def test_full_ranking_order(self):
        """4 positions should be ranked CRITICAL > HIGH > MEDIUM > LOW."""
        engine = EmergencyExitEngine()
        positions = {
            "BLUECHIP": {"shares": 100, "beta": 0.5, "mos_pct": 70.0},  # LOW
            "RISKY": {"shares": 100, "beta": 2.0, "mos_pct": 10.0},  # CRITICAL
            "GROWTH": {"shares": 100, "beta": 1.0, "mos_pct": 40.0},  # MEDIUM
            "VOLATILE": {"shares": 100, "beta": 1.4, "mos_pct": 20.0},  # HIGH
        }
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        priorities = [o.priority for o in result.exit_orders]
        assert priorities == [
            ExitPriority.CRITICAL,
            ExitPriority.HIGH,
            ExitPriority.MEDIUM,
            ExitPriority.LOW,
        ]


class TestExitOrderDetails:
    """Exit order content tests."""

    def test_order_action_is_emergency_sell(self):
        """All orders should have action = EMERGENCY_SELL."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 100, "beta": 1.5, "mos_pct": 15.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].action == "EMERGENCY_SELL"

    def test_order_shares_match_position(self):
        """Order shares should match position shares."""
        engine = EmergencyExitEngine()
        positions = {"VNM": {"shares": 500, "beta": 0.7, "mos_pct": 55.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].shares == 500

    def test_order_has_reason(self):
        """Each order should have a reason string."""
        engine = EmergencyExitEngine()
        positions = {"FPT": {"shares": 100, "beta": 1.2, "mos_pct": 35.0}}
        result = engine.evaluate(lri_score=0.25, open_positions=positions)
        assert len(result.exit_orders[0].reason) > 0

    def test_order_has_beta_and_mos(self):
        """Each order should carry beta and mos_pct."""
        engine = EmergencyExitEngine()
        positions = {"TCB": {"shares": 200, "beta": 1.1, "mos_pct": 42.0}}
        result = engine.evaluate(lri_score=0.28, open_positions=positions)
        order = result.exit_orders[0]
        assert order.beta == 1.1
        assert order.mos_pct == 42.0


class TestCounters:
    """Priority counter tests."""

    def test_counter_accurate(self):
        """Counters should match actual order counts."""
        engine = EmergencyExitEngine()
        positions = {
            "A": {"shares": 100, "beta": 2.0, "mos_pct": 10.0},  # CRITICAL
            "B": {"shares": 100, "beta": 1.3, "mos_pct": 25.0},  # HIGH
            "C": {"shares": 100, "beta": 0.9, "mos_pct": 45.0},  # MEDIUM
            "D": {"shares": 100, "beta": 0.5, "mos_pct": 60.0},  # LOW
            "E": {"shares": 100, "beta": 1.8, "mos_pct": 12.0},  # CRITICAL
        }
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.total_positions == 5
        assert result.critical_count == 2
        assert result.high_count == 1
        assert result.medium_count == 1
        assert result.low_count == 1

    def test_counters_zero_when_not_defensive(self):
        """All counters zero when not DEFENSIVE."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 100, "beta": 2.0, "mos_pct": 5.0}}
        result = engine.evaluate(lri_score=0.50, open_positions=positions)
        assert result.critical_count == 0
        assert result.high_count == 0
        assert result.medium_count == 0
        assert result.low_count == 0


class TestBoundaryConditions:
    """Boundary and edge case tests."""

    def test_exactly_at_threshold(self):
        """LRI = 0.30 exactly → NOT defensive (threshold is exclusive)."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 100, "beta": 2.0, "mos_pct": 5.0}}
        result = engine.evaluate(lri_score=0.30, open_positions=positions)
        assert result.is_defensive is False
        assert len(result.exit_orders) == 0

    def test_just_below_threshold(self):
        """LRI = 0.2999 → defensive."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 100, "beta": 2.0, "mos_pct": 5.0}}
        result = engine.evaluate(lri_score=0.2999, open_positions=positions)
        assert result.is_defensive is True

    def test_lri_zero(self):
        """LRI = 0.0 → defensive with all positions."""
        engine = EmergencyExitEngine()
        positions = {"A": {"shares": 100, "beta": 1.0, "mos_pct": 30.0}}
        result = engine.evaluate(lri_score=0.0, open_positions=positions)
        assert result.is_defensive is True
        assert len(result.exit_orders) == 1

    def test_single_position(self):
        """Single position should generate single exit order."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 100, "beta": 1.5, "mos_pct": 15.0}}
        result = engine.evaluate(lri_score=0.25, open_positions=positions)
        assert result.total_positions == 1
        assert len(result.exit_orders) == 1

    def test_default_beta_and_mos(self):
        """Missing beta/mos should default to 1.0 and 0.0."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 100}}  # no beta, no mos
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        order = result.exit_orders[0]
        assert order.beta == 1.0
        assert order.mos_pct == 0.0


class TestADVLiquidityFilter:
    """ADV liquidity-aware order slicing tests."""

    def test_no_slicing_when_position_small_vs_adv(self):
        """Position < 15% ADV → no slicing, full exit."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 1000, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 50000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        order = result.exit_orders[0]
        assert order.is_sliced is False
        assert order.max_order_shares == 1000
        assert order.estimated_days == 1
        assert order.position_pct_of_adv == pytest.approx(0.02, abs=0.001)

    def test_slicing_when_position_large_vs_adv(self):
        """Position > 15% ADV → TWAP slicing activated."""
        engine = EmergencyExitEngine()
        # Position 5000 shares vs ADV 10000 = 50% ADV → sliced
        positions = {"HPG": {"shares": 5000, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 10000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        order = result.exit_orders[0]
        assert order.is_sliced is True
        assert order.max_order_shares == 1000  # 10% of ADV 10000
        assert order.estimated_days == 5  # 5000 / 1000 = 5 days
        assert order.position_pct_of_adv == pytest.approx(0.5, abs=0.01)

    def test_slicing_exact_threshold(self):
        """Position = exactly 15% ADV → no slicing (threshold is exclusive)."""
        engine = EmergencyExitEngine()
        # 1500 / 10000 = 15% → not sliced
        positions = {"X": {"shares": 1500, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 10000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].is_sliced is False

    def test_slicing_just_above_threshold(self):
        """Position = 1501 / 10000 = 15.01% → sliced."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 1501, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 10000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].is_sliced is True

    def test_zero_adv_no_slicing(self):
        """Zero ADV → no slicing (can't determine liquidity)."""
        engine = EmergencyExitEngine()
        positions = {"X": {"shares": 10000, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 0.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].is_sliced is False
        assert result.exit_orders[0].max_order_shares == 10000

    def test_missing_volume_avg_20d_defaults_to_zero(self):
        """Missing volume_avg_20d → defaults to 0.0, no slicing."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 5000, "beta": 1.6, "mos_pct": 10.0}}  # no volume_avg_20d
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.exit_orders[0].is_sliced is False

    def test_sliced_count_in_result(self):
        """sliced_count should reflect number of sliced orders."""
        engine = EmergencyExitEngine()
        positions = {
            "BIG": {"shares": 5000, "beta": 2.0, "mos_pct": 5.0, "volume_avg_20d": 10000.0},  # sliced
            "SMALL": {"shares": 100, "beta": 1.0, "mos_pct": 40.0, "volume_avg_20d": 50000.0},  # not sliced
            "MID": {"shares": 2000, "beta": 1.3, "mos_pct": 20.0, "volume_avg_20d": 10000.0},  # sliced
        }
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert result.sliced_count == 2

    def test_sliced_order_reason_includes_twap_info(self):
        """Sliced order reason should contain TWAP slicing details."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 5000, "beta": 1.6, "mos_pct": 10.0, "volume_avg_20d": 10000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert "TWAP SLICING" in result.exit_orders[0].reason

    def test_unsliced_order_reason_says_full_exit(self):
        """Unsliced order reason should say 'full exit'."""
        engine = EmergencyExitEngine()
        positions = {"HPG": {"shares": 100, "beta": 0.5, "mos_pct": 60.0, "volume_avg_20d": 50000.0}}
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        assert "full exit" in result.exit_orders[0].reason

    def test_compute_exit_execution_directly(self):
        """Test compute_exit_execution method directly."""
        engine = EmergencyExitEngine()
        # Position 5000, ADV 20000 = 25% → sliced
        plan = engine.compute_exit_execution(5000, 20000.0)
        assert plan["is_sliced"] is True
        assert plan["max_order_shares"] == 2000  # 10% of 20000
        assert plan["estimated_days"] == 3  # ceil(5000/2000) = 3
        assert plan["position_pct_of_adv"] == 0.25

    def test_compute_exit_execution_small_position(self):
        """Small position vs ADV → no slicing."""
        engine = EmergencyExitEngine()
        plan = engine.compute_exit_execution(100, 50000.0)
        assert plan["is_sliced"] is False
        assert plan["max_order_shares"] == 100
        assert plan["estimated_days"] == 1

    def test_compute_exit_execution_zero_shares(self):
        """Zero shares → no slicing."""
        engine = EmergencyExitEngine()
        plan = engine.compute_exit_execution(0, 10000.0)
        assert plan["is_sliced"] is False
        assert plan["max_order_shares"] == 0

    def test_compute_exit_execution_zero_adv(self):
        """Zero ADV → no slicing."""
        engine = EmergencyExitEngine()
        plan = engine.compute_exit_execution(1000, 0.0)
        assert plan["is_sliced"] is False
        assert plan["max_order_shares"] == 1000

    def test_compute_exit_execution_negative_adv(self):
        """Negative ADV → no slicing."""
        engine = EmergencyExitEngine()
        plan = engine.compute_exit_execution(1000, -100.0)
        assert plan["is_sliced"] is False

    def test_compute_exit_execution_very_large_position(self):
        """Very large position vs small ADV → many days."""
        engine = EmergencyExitEngine()
        plan = engine.compute_exit_execution(100000, 1000.0)
        assert plan["is_sliced"] is True
        assert plan["max_order_shares"] == 100  # 10% of 1000
        assert plan["estimated_days"] == 1000  # 100000/100

    def test_multiple_sliced_orders_different_symbols(self):
        """Each symbol gets independent ADV evaluation."""
        engine = EmergencyExitEngine()
        positions = {
            "HPG": {"shares": 5000, "beta": 2.0, "mos_pct": 5.0, "volume_avg_20d": 10000.0},  # sliced
            "VCB": {"shares": 100, "beta": 0.5, "mos_pct": 60.0, "volume_avg_20d": 50000.0},  # not sliced
        }
        result = engine.evaluate(lri_score=0.20, open_positions=positions)
        hpg_order = [o for o in result.exit_orders if o.symbol == "HPG"][0]
        vcb_order = [o for o in result.exit_orders if o.symbol == "VCB"][0]
        assert hpg_order.is_sliced is True
        assert vcb_order.is_sliced is False
