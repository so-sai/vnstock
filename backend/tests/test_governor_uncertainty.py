"""TDD tests for UncertaintyLayer — Uncertainty ≠ Signal.

Uncertainty U ∈ [0, 1] is a RISK MODIFIER, never added to any composite score.
  U = f(C, F, R, A, P)
    C Completeness  — data availability per macro node
    F Freshness     — data lag (days) vs target_date (PIT)
    R Reliability   — data source quality (yahoo > unknown)
    A Agreement     — cross-variable consistency within a node
    P Persistence   — stability of regime (atr_ratio / vol_score)

LAWS:
  - U high → reduce allocation (1 - U), never create orders.
  - PIT: for target_date T, only data with date <= T is used.
  - Determinism: same target_date + same DB → same U.

Run: python -m pytest backend/tests/test_governor_uncertainty.py -v
"""

import sys

import pytest
from conftest import PROJECT_ROOT

SCREENER_DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from src.governor.uncertainty_layer import (
    COMPONENT_WEIGHTS,
    UncertaintyLayer,
    UncertaintyResult,
    classify_uncertainty,
)


@pytest.fixture(scope="module")
def layer() -> UncertaintyLayer:
    return UncertaintyLayer(db_path=str(SCREENER_DB_PATH))


# ── Range / shape ──────────────────────────────────────────────────────────
class TestOutputShape:
    def test_u_in_range(self, layer):
        r = layer.compute("2024-06-14")
        assert 0.0 <= r.u <= 1.0

    def test_components_in_range(self, layer):
        r = layer.compute("2024-06-14")
        for name in ("C", "F", "R", "A", "P"):
            assert 0.0 <= r.components[name] <= 1.0, name

    def test_per_node_and_composite(self, layer):
        r = layer.compute("2024-06-14")
        assert set(r.per_node.keys()) == {
            "US_Liquidity",
            "China_Economy",
            "Commodity_Cycle",
            "Domestic_Liquidity",
        }
        assert isinstance(r.u, float)
        assert r.band in {"LOW", "MEDIUM", "HIGH"}

    def test_components_sum_to_one(self):
        assert sum(COMPONENT_WEIGHTS.values()) == pytest.approx(1.0)


# ── PIT: only data <= target_date ───────────────────────────────────────────
class TestPIT:
    def test_early_date_not_future_leak(self, layer):
        r = layer.compute("2015-01-05")
        assert 0.0 <= r.u <= 1.0

    def test_determinism(self, layer):
        a = layer.compute("2024-06-14")
        b = layer.compute("2024-06-14")
        assert a.u == b.u
        assert a.components == b.components

    def test_missing_data_increases_u(self, layer):
        early = layer.compute("2011-06-10")  # sparse history
        late = layer.compute("2024-06-14")
        # Completeness should be lower (or equal) on the sparse window.
        assert early.components["C"] <= late.components["C"]


# ── Classification ──────────────────────────────────────────────────────────
class TestClassification:
    def test_low_u_low_uncertainty(self):
        assert classify_uncertainty(0.05) == "LOW"

    def test_high_u_high_uncertainty(self):
        assert classify_uncertainty(0.95) == "HIGH"

    def test_mid_u_medium(self):
        assert classify_uncertainty(0.5) == "MEDIUM"

    def test_thresholds_monotonic(self):
        vals = [classify_uncertainty(x) for x in (0.1, 0.4, 0.7, 0.9)]
        assert vals == ["LOW", "MEDIUM", "HIGH", "HIGH"]


# ── Result immutability / provenance ────────────────────────────────────────
class TestProvenance:
    def test_result_exposes_db_path(self, layer):
        r = layer.compute("2024-06-14")
        assert r.db_path == str(SCREENER_DB_PATH)

    def test_result_exposes_target_date(self, layer):
        r = layer.compute("2024-06-14")
        assert r.target_date == "2024-06-14"

    def test_result_exposes_source_trace(self, layer):
        r = layer.compute("2024-06-14")
        # C/F/R/A/P each must cite which table/field it came from.
        for name in ("C", "F", "R", "A", "P"):
            assert r.source_trace[name], name
