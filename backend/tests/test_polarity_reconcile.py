"""test_polarity_reconcile.py — C3-6 §6 contract: polarity wins + warning.

Test 3 tầng:
  1. calculate_path_sign — product, ANY-zero->0, rỗng/thiếu->0, fail-fast ValueError.
  2. reconcile — conflict ép hướng + POLARITY_NARRATIVE_CONFLICT + log POLARITY_MISMATCH;
     concordant / indeterminate / narrative không definite -> pass-through.
  3. Hook csi_explain._reconcile_action_polarity (log-only) + hop trace_path
     mang polarity (đầu vào thực của hook).

Run:  python -m pytest tests/test_polarity_reconcile.py -q   (từ backend/)
"""

import logging
from types import SimpleNamespace

import pytest
from src.calibration.causal_edge import CausalGraph
from src.calibration.polarity_reconcile import (
    WARNING_POLARITY_CONFLICT,
    calculate_path_sign,
    reconcile,
)
from src.governor.csi_explain import _reconcile_action_polarity


def _hop(polarity=None, **kw):
    hop = {"edge_id": "T1", "source": "A", "target": "B"}
    if polarity is not None:
        hop["polarity"] = polarity
    hop.update(kw)
    return hop


# ── 1. calculate_path_sign ───────────────────────────────────────────


class TestCalculatePathSign:
    def test_product_even_negatives_is_positive(self):
        assert calculate_path_sign([_hop(-1), _hop(-1)]) == 1

    def test_product_odd_negative(self):
        assert calculate_path_sign([_hop(1), _hop(-1), _hop(1)]) == -1

    def test_any_zero_makes_path_indeterminate(self):
        assert calculate_path_sign([_hop(1), _hop(0), _hop(-1)]) == 0

    def test_empty_chain_is_indeterminate_not_plus_one(self):
        # Zero-hallucination: không có cấu trúc -> không có khẳng định.
        assert calculate_path_sign([]) == 0

    def test_missing_polarity_treated_as_indeterminate(self):
        assert calculate_path_sign([_hop(), _hop(1)]) == 0

    def test_accepts_edge_objects_with_polarity_attr(self):
        edge = SimpleNamespace(polarity=-1)
        assert calculate_path_sign([edge]) == -1

    def test_invalid_polarity_raises_fail_fast(self):
        with pytest.raises(ValueError, match="Invalid edge polarity"):
            calculate_path_sign([_hop(2)])


# ── 2. reconcile (§6) ────────────────────────────────────────────────


class TestReconcile:
    def test_negative_conflict_polarity_wins_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = reconcile(-1, "BULLISH", context="HPG")
        assert result.direction == "BEARISH"
        assert result.warning == WARNING_POLARITY_CONFLICT
        assert result.path_sign == -1
        assert "POLARITY_MISMATCH" in caplog.text
        assert "HPG" in caplog.text

    def test_positive_conflict_polarity_wins_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = reconcile(1, "BEARISH", context="PNJ")
        assert result.direction == "BULLISH"
        assert result.warning == WARNING_POLARITY_CONFLICT
        assert "POLARITY_MISMATCH" in caplog.text

    def test_concordant_passes_without_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert reconcile(1, "BULLISH").direction == "BULLISH"
            assert reconcile(-1, "BEARISH").direction == "BEARISH"
        assert not [r for r in caplog.records if "POLARITY_MISMATCH" in r.getMessage()]

    def test_indeterminate_never_adjudicates(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = reconcile(0, "BULLISH", context="X")
        assert result.direction == "BULLISH"
        assert result.warning is None
        assert not caplog.records

    def test_non_definite_narrative_passes_through(self):
        for narrative in ("NEUTRAL", "TRANSITIONAL", "FRACTURED", "N/A"):
            result = reconcile(-1, narrative, context="Y")
            assert result.direction == narrative
            assert result.warning is None

    def test_invalid_path_sign_raises_fail_fast(self):
        with pytest.raises(ValueError, match="Invalid path_sign"):
            reconcile(2, "BULLISH")


# ── 3. Hook csi_explain (log-only) + hop polarity ────────────────────


class TestCsiExplainHook:
    def _trace(self, hops):
        return {"world_to_vn": None, "company_leg": {"hops": hops}}

    def test_conflict_flips_action_direction_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _reconcile_action_polarity("HPG", self._trace([_hop(-1)]), "OPEN")
        assert result is not None
        assert result.direction == "BEARISH"
        assert result.warning == WARNING_POLARITY_CONFLICT
        assert "POLARITY_MISMATCH" in caplog.text

    def test_concordant_action_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _reconcile_action_polarity(
                "HPG", self._trace([_hop(1), _hop(1)]), "SCALE_IN"
            )
        assert result is not None
        assert result.direction == "BULLISH"
        assert result.warning is None
        assert not caplog.records

    def test_non_directional_action_is_not_adjudicated(self):
        for action in ("HOLD", "WAIT", "N/A", "UNKNOWN_ACTION"):
            assert _reconcile_action_polarity("X", self._trace([_hop(-1)]), action) is None

    def test_regime_conditional_chain_never_adjudicates(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _reconcile_action_polarity("VCB", self._trace([_hop(1), _hop(0)]), "OPEN")
        assert result is not None
        assert result.warning is None
        assert not caplog.records


class TestTracePathHopsCarryPolarity:
    """trace_path hop là đầu vào thật của hook — phải mang polarity."""

    def test_logistics_chain_hops_have_polarity(self):
        cg = CausalGraph()
        path = cg.trace_path("GLOBAL_DEMAND", "OPERATING_PROFIT", "LOGISTICS_OPERATOR")
        assert path is not None and len(path) >= 2
        assert all("polarity" in hop for hop in path)
        assert calculate_path_sign(path) == 1

    def test_regime_conditional_bank_chain_path_sign_zero(self):
        cg = CausalGraph()
        path = cg.trace_path("INTEREST_RATE", "NIM", "FRANCHISE_BANK")
        assert path is not None
        assert calculate_path_sign(path) == 0
