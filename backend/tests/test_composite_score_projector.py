"""test_composite_score_projector.py — TDD cho CompositeScoreProjector (giai đoạn Dual-Layer Architecture).

Kiểm thử:
  - Ánh xạ phi tuyến từ Bayesian Mandate sang Composite Score (0-100)
  - Cờ Phủ quyết (Veto Gate Caps)
  - Tách bạch Target Allocation % (không âm) và Delta Position Adjustment
  - Cột Coverage (Bao phủ) & Coherence (Đồng thuận)
  - Decoupled Policy Layer (ExecutionPolicy: Conservative / Balanced / Aggressive)
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
        allocation_pct=-1.6,  # Position delta adjustment (-1.6%)
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
    # Target allocation % must be non-negative [0%, 100%]
    assert res.allocation_pct >= 0.0
    assert res.delta_pct == -1.6
    assert 0.0 <= res.coverage <= 1.0
    assert 0.0 <= res.coherence <= 1.0


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


def test_execution_policy_decoupling():
    from governor.composite_score_projector import CompositeScoreProjector, ExecutionPolicy
    from governor.company_state import BayesianMandate

    mandate = BayesianMandate(
        symbol="DGC", action="REDUCE", action_vn="Giảm vị thế",
        expected_utility=0.20, p_gain=0.48, calibration_penalty=0.0,
        allocation_pct=0.0, conviction=0.6, macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP", sector_phase="MID",
        health_archetype="STEADY_EARNER", valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP", valuation_zone_ts="CHEAP",
        behavior_position="NEUTRAL", margin_of_safety=78.8,
    )

    pol_cons = ExecutionPolicy("CONSERVATIVE", buy_threshold=75.0, full_margin_threshold=90.0)
    pol_aggr = ExecutionPolicy("AGGRESSIVE", buy_threshold=65.0, full_margin_threshold=80.0)

    p1 = CompositeScoreProjector(policy=pol_cons)
    p2 = CompositeScoreProjector(policy=pol_aggr)

    r1 = p1.project(mandate)
    r2 = p2.project(mandate)

    assert r1.buy_gap == round(75.0 - r1.final_score, 1)
    assert r2.buy_gap == round(65.0 - r2.final_score, 1)
    assert r1.buy_gap > r2.buy_gap


def test_full_margin_trigger_and_margin_status():
    from governor.composite_score_projector import CompositeScoreProjector
    from governor.company_state import BayesianMandate

    mandate_simulated = BayesianMandate(
        symbol="GAS", action="SCALE_IN", action_vn="Mua tích lũy",
        expected_utility=0.85, p_gain=0.88, calibration_penalty=0.0,
        allocation_pct=30.0, conviction=0.90, macro_state="RECOVERY",
        transmission_phase="EXPANSION", sector_phase="EARLY",
        health_archetype="COMPOUNDER", valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP", valuation_zone_ts="CHEAP",
        behavior_position="BULLISH", margin_of_safety=65.7, contextual_health_score=0.92
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate_simulated)

    assert res.final_score >= 85.0
    assert "FULL MARGIN" in res.margin_status
    assert "MUA_TOI_DA_DON_BAY" in res.recommendation
