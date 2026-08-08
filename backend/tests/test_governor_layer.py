"""TDD tests for GovernorLayer — alloc_multiplier fusion (Risk Modifier).

Alloc_final = Alloc_Kelly × Confidence × (1 - U_shock)

  Alloc_Kelly  — base position sizing (LRI dimmer he_so from DecisionGuard)
  Confidence   — data completeness C from UncertaintyLayer
  U_shock      — independent fusion of Uncertainty U and Shock severity S:
                 U_shock = 1 - (1-U)·(1-S)  (no double-counting)

LAWS:
  - Output is a SIZING multiplier in [0,1]. It never creates orders.
  - Shock severity >= SYSTEMIC threshold → multiplier floor at 0 (freeze).
  - Monotonic: higher uncertainty/shock → lower multiplier.

Run: python -m pytest backend/tests/test_governor_layer.py -v
"""

import sys

import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from src.governor.governor_layer import (
    SHOCK_VETO_THRESHOLD,
    SYSTEMIC_SHOCK_SEVERITY,
    combine_alloc_multiplier,
    fuse_uncertainty_shock,
)


# ── Pure fusion (no double counting) ────────────────────────────────────────
class TestFuseUncertaintyShock:
    def test_no_uncertainty_no_shock(self):
        assert fuse_uncertainty_shock(0.0, 0.0) == pytest.approx(0.0)

    def test_full_uncertainty(self):
        assert fuse_uncertainty_shock(1.0, 0.0) == pytest.approx(1.0)

    def test_full_shock(self):
        assert fuse_uncertainty_shock(0.0, 1.0) == pytest.approx(1.0)

    def test_both_high_but_not_double_counted(self):
        # 1-(1-.5)(1-.5) = 0.75, NOT 1.0 (independent fusion).
        assert fuse_uncertainty_shock(0.5, 0.5) == pytest.approx(0.75)

    def test_monotonic(self):
        assert fuse_uncertainty_shock(0.6, 0.3) > fuse_uncertainty_shock(0.5, 0.3)
        assert fuse_uncertainty_shock(0.3, 0.6) > fuse_uncertainty_shock(0.3, 0.5)


# ── combine_alloc_multiplier ────────────────────────────────────────────────
class TestCombineAllocMultiplier:
    def test_full_confidence_no_risk(self):
        m = combine_alloc_multiplier(he_so=1.0, confidence=1.0, uncertainty=0.0, shock=0.0)
        assert m == pytest.approx(1.0)

    def test_full_kelly_half_confidence(self):
        m = combine_alloc_multiplier(he_so=1.0, confidence=0.5, uncertainty=0.0, shock=0.0)
        assert m == pytest.approx(0.5)

    def test_lri_dimmer_dominates(self):
        m = combine_alloc_multiplier(he_so=0.4, confidence=1.0, uncertainty=0.0, shock=0.0)
        assert m == pytest.approx(0.4)

    def test_uncertainty_scales_down(self):
        m = combine_alloc_multiplier(he_so=1.0, confidence=1.0, uncertainty=0.5, shock=0.0)
        assert m == pytest.approx(0.5)

    def test_shock_scales_down(self):
        m = combine_alloc_multiplier(he_so=1.0, confidence=1.0, uncertainty=0.0, shock=0.5)
        assert m == pytest.approx(0.5)

    def test_systemic_shock_freezes(self):
        m = combine_alloc_multiplier(
            he_so=1.0, confidence=1.0, uncertainty=0.0, shock=SYSTEMIC_SHOCK_SEVERITY
        )
        assert m == pytest.approx(0.0)

    def test_shock_band_veto_freezes(self):
        # SHOCK band (0.70) is the veto threshold — entries frozen.
        m = combine_alloc_multiplier(
            he_so=1.0, confidence=1.0, uncertainty=0.0, shock=SHOCK_VETO_THRESHOLD
        )
        assert m == pytest.approx(0.0)

    def test_below_veto_still_scales_down(self):
        # Just below the veto threshold: strongly reduced but not zero.
        m = combine_alloc_multiplier(
            he_so=1.0, confidence=1.0, uncertainty=0.0, shock=SHOCK_VETO_THRESHOLD - 0.01
        )
        assert 0.0 < m < 1.0

    def test_output_clamped(self):
        assert 0.0 <= combine_alloc_multiplier(1.0, 1.0, 1.0, 1.0) <= 1.0
        assert 0.0 <= combine_alloc_multiplier(0.0, 0.0, 0.0, 0.0) <= 1.0

    def test_negative_inputs_clamped(self):
        m = combine_alloc_multiplier(-1.0, 0.0, -1.0, 0.0)
        assert m == pytest.approx(0.0)

    def test_never_creates_orders_semantics(self):
        # returns a scalar multiplier, nothing with BUY/SELL semantics
        m = combine_alloc_multiplier(1.0, 0.8, 0.2, 0.3)
        assert isinstance(m, float)
