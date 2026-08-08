"""test_law_bridges.py — TDD cho Law Bridges.

WHY: UncertaintyLayer + ShockDetector KHÔNG được commit như hệ thống song song.
Chúng là DATA-PROVIDER cho 2 engine LAW ĐÃ TỒN TẠI nhưng chưa được nối vào
production (dead code — chỉ test pass):
  - ShockDetector       → MarginStressNode (LAW-008)     qua shock_to_margin_inputs
  - UncertaintyLayer    → EpistemicEngine (LAW-004/008)  qua uncertainty_to_nodes_data
"""

import pytest

from calibration.causal_dag_engine import MarginStressNode
from calibration.epistemic_engine import EpistemicEngine
from governor.law_bridges import (
    law_governor_signal,
    shock_to_margin_inputs,
    uncertainty_to_nodes_data,
)
from governor.governor_layer import combine_alloc_multiplier
from governor.shock_detector import ShockScore, stress_fraction
from governor.uncertainty_layer import UncertaintyResult

NODES = ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]


def _shock(z_vol=0.0, z_spread=0.0, z_fx=0.0, z_liq=0.0, z_breadth=0.0) -> ShockScore:
    return ShockScore(
        target_date="2022-03-01",
        severity=0.5,
        z_scores={
            "vol": z_vol,
            "spread": z_spread,
            "fx": z_fx,
            "liquidity": z_liq,
            "breadth": z_breadth,
        },
        source_trace={k: "trace" for k in ("vol", "spread", "fx", "liquidity", "breadth")},
        db_path="",
    )


# ── Bridge 1: ShockDetector → MarginStressNode ────────────────────────────


def test_shock_to_margin_inputs_maps_breadth_stress():
    s = _shock(z_breadth=-2.0)
    raw = shock_to_margin_inputs(s)
    assert raw["breadth_stress_ratio"] == pytest.approx(stress_fraction(-(-2.0)), abs=1e-4)
    assert raw["breadth_stress_ratio"] > 0.7
    # m_sys là proxy thanh khoản (volume collapse), KHÔNG phải margin thật.
    assert raw["system_margin_ratio"] == pytest.approx(stress_fraction(-0.0), abs=1e-4)
    # cross-contagion chưa đo — phải khai báo trung thực 0.0.
    assert raw["cross_contagion_index"] == 0.0


def test_shock_to_margin_inputs_maps_liquidity_drain():
    s = _shock(z_liq=-2.5)
    raw = shock_to_margin_inputs(s)
    assert raw["system_margin_ratio"] == pytest.approx(stress_fraction(-(-2.5)), abs=1e-4)
    assert raw["system_margin_ratio"] > 0.9


def test_shock_to_margin_inputs_is_pit_iso():
    s = _shock()
    raw = shock_to_margin_inputs(s)
    assert raw["now_time"] == "2022-03-01T00:00:00"
    assert raw["margin_last_updated"] == raw["now_time"]


def test_shock_feed_margin_stress_node():
    """Bridge phải nuôi được MarginStressNode: kết quả là MarginStressResult hợp lệ."""
    s = _shock(z_liq=-3.0, z_breadth=-3.0)
    raw = shock_to_margin_inputs(s)
    res = MarginStressNode().evaluate_node(
        raw["system_margin_ratio"],
        raw["breadth_stress_ratio"],
        raw["cross_contagion_index"],
        raw["margin_last_updated"],
        raw["now_time"],
    )
    assert 0.0 <= res.stress_index <= 1.0
    assert res.authority_modifier >= 0.0
    assert isinstance(res.veto_triggered, bool)
    assert res.reason != ""


# ── Bridge 2: UncertaintyLayer → EpistemicEngine ──────────────────────────


def _uncertainty() -> UncertaintyResult:
    comps = {"C": 0.9, "F": 0.8, "R": 0.7, "A": 0.9, "P": 0.6}
    per_node_components = {n: dict(comps) for n in NODES}
    return UncertaintyResult(
        target_date="2022-03-01",
        db_path="",
        u=0.25,
        band="LOW",
        components=comps,
        per_node={n: 0.25 for n in NODES},
        per_node_components=per_node_components,
        source_trace={},
    )


def test_uncertainty_to_nodes_data_has_all_nodes():
    nodes = uncertainty_to_nodes_data(_uncertainty())
    assert set(nodes.keys()) == set(NODES)


def test_uncertainty_to_nodes_data_fields_in_range():
    nodes = uncertainty_to_nodes_data(_uncertainty())
    for node, info in nodes.items():
        for key in ("importance", "available", "freshness", "reliability"):
            assert key in info
            assert 0.0 <= info[key] <= 1.0, (node, key, info[key])


def test_uncertainty_to_nodes_data_maps_components():
    nodes = uncertainty_to_nodes_data(_uncertainty())
    node = nodes[NODES[0]]
    # C (completeness) → available; F (freshness) → freshness; R → reliability.
    assert node["available"] == 0.9
    assert node["freshness"] == 0.8
    assert node["reliability"] == 0.7


def test_uncertainty_feed_epistemic_coverage():
    """Bridge phải nuôi được EpistemicEngine: coverage hợp lệ, cap ≤ 0.90."""
    nodes = uncertainty_to_nodes_data(_uncertainty())
    cov = EpistemicEngine().compute_coverage(nodes)
    assert 0.0 <= cov <= 0.90


# ── Orchestration: law_governor_signal ────────────────────────────────────


def test_law_governor_signal_runs_on_real_data():
    result = law_governor_signal("2022-03-01")
    assert 0.0 <= result["stress_index"] <= 1.0
    assert 0.0 <= result["coverage"] <= 0.90
    assert 0.0 <= result["uncertainty"] <= 1.0
    assert 0.0 <= result["shock"] <= 1.0
    assert isinstance(result["veto_triggered"], bool)
    assert isinstance(result["authority_modifier"], float)
    # Provenance bắt buộc: không có provenance = không có trust.
    assert "provenance" in result
    assert result["provenance"]["margin_stress"] == "causal_dag_engine.MarginStressNode (LAW-008)"
    assert result["provenance"]["coverage"] == "epistemic_engine.EpistemicEngine.compute_coverage (LAW-004/008)"


# ── Governor fusion: combine_alloc_multiplier + LAW params ────────────────


def test_combine_alloc_multiplier_law_veto_forces_zero():
    assert combine_alloc_multiplier(1.0, 0.9, 0.1, 0.2, veto=True) == 0.0


def test_combine_alloc_multiplier_authority_modifier_scales():
    base = combine_alloc_multiplier(1.0, 1.0, 0.0, 0.0)
    half = combine_alloc_multiplier(1.0, 1.0, 0.0, 0.0, authority_modifier=0.5)
    assert half < base
    assert abs(half - base * 0.5) < 1e-9


def test_combine_alloc_multiplier_backward_compat():
    """Không LAW params → hành vi cũ giữ nguyên."""
    before = combine_alloc_multiplier(0.8, 0.9, 0.1, 0.2)
    after = combine_alloc_multiplier(0.8, 0.9, 0.1, 0.2)
    assert before == after
    assert 0.0 <= before <= 1.0
