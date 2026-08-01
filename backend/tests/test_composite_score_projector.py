"""test_composite_score_projector.py — TDD cho CompositeScoreProjector (giai đoạn Dual-Layer Architecture).

Kiểm thử ánh xạ phi tuyến từ Bayesian Mandate sang Composite Score (0-100)
kèm cờ Phủ quyết (Veto Gate) chuẩn bị cho REST API & CLI Dashboard.
"""

import pytest


def test_projector_subscores_and_bounds():
    from governor.composite_score_projector import CompositeScoreProjector
    from governor.company_state import BayesianMandate

    mandate = BayesianMandate(
        symbol="VCB",
        action="REDUCE",
        action_vn="Giảm vị thế",
        expected_utility=0.25,
        p_gain=0.42,
        calibration_penalty=0.0,
        allocation_pct=0.0,
        conviction=0.7,
        macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP",
        sector_phase="MID",
        health_archetype="STEADY_EARNER",
        valuation_zone="EXPENSIVE",
        valuation_zone_peer="FAIR",
        valuation_zone_ts="FAIR",
        behavior_position="NEUTRAL",
        margin_of_safety=49.5,
        contextual_health_score=0.85,
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate)

    assert res.symbol == "VCB"
    assert 0.0 <= res.macro_score <= 20.0
    assert 0.0 <= res.internal_score <= 30.0
    assert 0.0 <= res.market_score <= 50.0
    assert 0.0 <= res.final_score <= 100.0


def test_hard_veto_caps_score_under_30():
    from governor.composite_score_projector import CompositeScoreProjector
    from governor.company_state import BayesianMandate

    mandate = BayesianMandate(
        symbol="HPG",
        action="VETO",
        action_vn="Cấm tuyệt đối",
        expected_utility=0.10,
        p_gain=0.35,
        calibration_penalty=0.0,
        allocation_pct=0.0,
        conviction=0.8,
        macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP",
        sector_phase="MID",
        health_archetype="COMPOUNDER",
        valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP",
        valuation_zone_ts="CHEAP",
        behavior_position="NEUTRAL",
        margin_of_safety=24.3,
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate)

    assert res.final_score <= 30.0
    assert res.veto_flag in ("CRISIS_VETO", "HARD_VETO")
    assert "CAM_MUA" in res.recommendation


def test_overpriced_veto_for_ultra_expensive_mos():
    from governor.composite_score_projector import CompositeScoreProjector
    from governor.company_state import BayesianMandate

    mandate = BayesianMandate(
        symbol="BCM",
        action="VETO",
        action_vn="Cấm tuyệt đối",
        expected_utility=-0.50,
        p_gain=0.15,
        calibration_penalty=0.0,
        allocation_pct=0.0,
        conviction=0.9,
        macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP",
        sector_phase="MID",
        health_archetype="DISTRESSED",
        valuation_zone="ULTRA_EXPENSIVE",
        valuation_zone_peer="ULTRA_EXPENSIVE",
        valuation_zone_ts="ULTRA_EXPENSIVE",
        behavior_position="BEARISH",
        margin_of_safety=-502.7,
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate)

    assert res.final_score <= 25.0
    assert res.veto_flag in ("OVERPRICED_VETO", "DISTRESSED_VETO", "CRISIS_VETO")


def test_batch_project_sorts_descending():
    from governor.composite_score_projector import CompositeScoreProjector
    from governor.company_state import BayesianMandate

    m1 = BayesianMandate(
        symbol="DGC", action="REDUCE", action_vn="Giảm vị thế",
        expected_utility=0.20, p_gain=0.48, calibration_penalty=0.0,
        allocation_pct=0.0, conviction=0.6, macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP", sector_phase="MID",
        health_archetype="STEADY_EARNER", valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP", valuation_zone_ts="CHEAP",
        behavior_position="NEUTRAL", margin_of_safety=78.8,
    )
    m2 = BayesianMandate(
        symbol="BCM", action="VETO", action_vn="Cấm tuyệt đối",
        expected_utility=-0.50, p_gain=0.15, calibration_penalty=0.0,
        allocation_pct=0.0, conviction=0.9, macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP", sector_phase="MID",
        health_archetype="DISTRESSED", valuation_zone="ULTRA_EXPENSIVE",
        valuation_zone_peer="ULTRA_EXPENSIVE", valuation_zone_ts="ULTRA_EXPENSIVE",
        behavior_position="BEARISH", margin_of_safety=-502.7,
    )

    projector = CompositeScoreProjector()
    batch = projector.project_batch([m2, m1])

    assert len(batch) == 2
    assert batch[0].symbol == "DGC"
    assert batch[1].symbol == "BCM"
    assert batch[0].final_score > batch[1].final_score
