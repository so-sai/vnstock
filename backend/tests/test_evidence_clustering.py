"""test_evidence_clustering.py — TDD cho Evidence Independence."""

import pytest

from calibration.evidence_clustering import (
    CLUSTER_VERSION,
    cluster_evidence,
)


def test_four_base_clusters_always_present():
    r = cluster_evidence({})
    assert set(r["clusters"].keys()) == {"macro", "sector", "fundamental", "behavior"}
    for c in r["clusters"].values():
        assert "sources" in c and "states" in c


def test_n_independent_counts_only_active_clusters():
    r = cluster_evidence({
        "macro_state": "US_LIQUIDITY",
        "transmission_phase": "PHASE_1",
        "sector_phase": "EXPANSION",
        "health_archetype": "COMPOUNDER",
        "valuation_zone": "UNDERVALUED",
        "behavior_position": "OVERSOLD",
    })
    assert r["n_independent_evidence"] == 4
    assert r["decision_quality"] == "Strong"


def test_unknown_and_neutral_not_counted():
    r = cluster_evidence({
        "macro_state": "US_LIQUIDITY",
        "transmission_phase": "UNKNOWN",
        "sector_phase": "NEUTRAL",
        "health_archetype": "",
        "valuation_zone": "N/A",
        "behavior_position": "OVERSOLD",
    })
    # macro active (chỉ 1/2 state đủ) + behavior active = 2 → Mixed
    assert r["n_independent_evidence"] == 2
    assert r["decision_quality"] == "Mixed"


def test_quality_weak_when_no_active():
    r = cluster_evidence({
        "macro_state": "UNKNOWN",
        "transmission_phase": "NEUTRAL",
        "sector_phase": "",
        "health_archetype": "N/A",
        "valuation_zone": None,
        "behavior_position": "UNKNOWN_VAL",
    })
    assert r["n_independent_evidence"] == 0
    assert r["decision_quality"] == "Weak"


def test_macro_cluster_merges_two_states():
    r = cluster_evidence({"macro_state": "US_LIQUIDITY", "transmission_phase": "PHASE_2"})
    macro = r["clusters"]["macro"]
    assert macro["states"]["macro_state"] == "US_LIQUIDITY"
    assert macro["states"]["transmission_phase"] == "PHASE_2"
    # macro + (fundamental empty) + (sector empty) + (behavior empty) = 1
    assert r["n_independent_evidence"] == 1
    assert r["decision_quality"] == "Weak"


def test_shock_and_coverage_optional():
    r = cluster_evidence({}, shock_band="SHOCK", coverage=0.88)
    assert set(r["clusters"].keys()) == {"macro", "sector", "fundamental", "behavior", "shock", "coverage"}
    assert r["clusters"]["shock"]["states"]["shock_band"] == "SHOCK"
    assert r["clusters"]["coverage"]["states"]["coverage"] == 0.88
    assert r["n_independent_evidence"] == 2
    assert r["decision_quality"] == "Mixed"


def test_sources_provenance_immutable():
    r = cluster_evidence({"sector_phase": "EXPANSION"})
    assert "sector_exposure_matrix" in r["clusters"]["sector"]["sources"]
    assert r["version"] == CLUSTER_VERSION


def test_higher_quality_requires_more_clusters():
    weak = cluster_evidence({"behavior_position": "OVERSOLD"})
    strong = cluster_evidence({
        "macro_state": "US_LIQUIDITY", "sector_phase": "EXPANSION",
        "health_archetype": "COMPOUNDER", "behavior_position": "OVERSOLD",
    })
    assert weak["decision_quality"] == "Weak"
    assert strong["decision_quality"] == "Strong"
