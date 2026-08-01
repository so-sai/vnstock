"""test_margin_stress_node.py - TDD for MarginStressNode."""

from datetime import datetime

from calibration.causal_dag_engine import (
    CausalDAGEngine,
    MarginStressNode,
)


def test_compute_stress_index_bounds():
    node = MarginStressNode()

    for m_sys in [-1.0, 0.0, 1.0]:
        for b_stress in [-1.0, 0.0, 1.0]:
            for c_cross in [-1.0, 0.0, 1.0]:
                idx = node.compute_stress_index(
                    m_sys, b_stress, c_cross
                )
                assert 0.0 <= idx <= 1.0, (
                    f"stress_index {idx} out of bounds"
                )


def test_veto_at_threshold_0_80():
    now_iso = "2026-08-01T00:00:00"
    node = MarginStressNode(theta=-2.0)
    res = node.evaluate_node(
        1.0, 1.0, 1.0, now_iso, now_iso
    )
    assert res.veto_triggered is True
    assert res.mandate_override == "CAPITAL_PRESERVATION"

    res_normal = node.evaluate_governor_impact(0.50)
    assert res_normal.veto_triggered is False
    assert 0 < res_normal.authority_modifier < 1.0


def test_authority_modifier_linear_decay():
    node = MarginStressNode()

    res_0_45 = node.evaluate_governor_impact(0.45)
    expected_0_45 = max(0.05, 1.0 - 1.25 * (0.45 - 0.40))
    assert abs(res_0_45.authority_modifier - expected_0_45) < 0.01

    res_0_50 = node.evaluate_governor_impact(0.50)
    expected_0_50 = max(0.05, 1.0 - 1.25 * (0.50 - 0.40))
    assert abs(res_0_50.authority_modifier - expected_0_50) < 0.01

    res_0_70 = node.evaluate_governor_impact(0.70)
    assert res_0_70.veto_triggered is False

    res_0_80 = node.evaluate_governor_impact(0.80)
    assert res_0_80.veto_triggered is True
    assert res_0_80.authority_modifier == 0.0


def test_decay_factor_halflife():
    node = MarginStressNode(half_life_hours=72.0)

    now = datetime(2026, 8, 4, 0, 0, 0)
    last_72h_ago = datetime(2026, 8, 1, 0, 0, 0)

    decay, stale_days = node._calculate_decay(
        last_72h_ago.isoformat(), now.isoformat()
    )
    assert abs(decay - 0.5) < 0.01
    assert stale_days == 3

    now_36h = datetime(2026, 8, 2, 12, 0, 0)
    now_48h = datetime(2026, 8, 3, 0, 0, 0)

    decay_36h, _ = node._calculate_decay(
        last_72h_ago.isoformat(), now_36h.isoformat()
    )
    decay_48h, _ = node._calculate_decay(
        last_72h_ago.isoformat(), now_48h.isoformat()
    )

    assert decay_36h > decay_48h
    assert decay_48h > 0.5


def test_process_causal_graph_integration():
    engine = CausalDAGEngine()

    now = datetime(2026, 8, 1, 12, 0, 0).isoformat()

    raw_inputs = {
        "system_margin_ratio": 1.0,
        "breadth_stress_ratio": 0.8,
        "cross_contagion_index": 0.5,
        "margin_last_updated": now,
        "now_time": now,
        "base_epistemic_authority": 1.0,
        "independent_breadth_active": True,
    }

    result = engine.process_causal_graph(raw_inputs)

    assert "dag_nodes_evaluated" in result
    assert "margin_stress" in result
    assert "adjusted_epistemic_authority" in result
    assert "governor_override" in result
    assert "causal_coherence_passed" in result
    assert "cluster_compression" in result

    ms = result["margin_stress"]
    assert "effective_index" in ms
    assert "raw_index" in ms
    assert "decay_factor" in ms
    assert "stale_days" in ms
    assert "authority_modifier" in ms
    assert "veto_triggered" in ms
    assert "reason" in ms

    assert result["dag_nodes_evaluated"] == 5
    assert isinstance(result["adjusted_epistemic_authority"], float)
    assert 0.0 <= result["adjusted_epistemic_authority"] <= 1.0
