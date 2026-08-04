"""test_composite_score_projector.py — TDD cho CompositeScoreProjector (giai đoạn Dual-Layer Architecture).

Kiểm thử:
  - Ánh xạ phi tuyến từ Bayesian Mandate sang Composite Score (0-100)
  - Cờ Phủ quyết (Veto Gate Caps)
  - Tách bạch Target Allocation % (không âm) và Delta Position Adjustment
  - Cột Coverage (Bao phủ) & Coherence (Đồng thuận)
  - Decoupled Policy Layer (ExecutionPolicy: Conservative / Balanced / Aggressive)
"""


def test_projector_subscores_and_bounds():
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector

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
    assert res.governor_mandate == "NEUTRAL_DEFENSIVE"
    assert "Macro: CREDIT_STRESS" in res.why_drivers


def test_hard_veto_caps_score_under_30():
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector

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
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector, ExecutionPolicy

    mandate = BayesianMandate(
        symbol="DGC",
        action="REDUCE",
        action_vn="Giảm vị thế",
        expected_utility=0.20,
        p_gain=0.48,
        calibration_penalty=0.0,
        allocation_pct=0.0,
        conviction=0.6,
        macro_state="CREDIT_STRESS",
        transmission_phase="LIQUIDITY_TRAP",
        sector_phase="MID",
        health_archetype="STEADY_EARNER",
        valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP",
        valuation_zone_ts="CHEAP",
        behavior_position="NEUTRAL",
        margin_of_safety=78.8,
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


def test_full_margin_trigger_and_display_flag():
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector

    mandate_simulated = BayesianMandate(
        symbol="GAS",
        action="SCALE_IN",
        action_vn="Mua tích lũy",
        expected_utility=0.85,
        p_gain=0.88,
        calibration_penalty=0.0,
        allocation_pct=30.0,
        conviction=0.90,
        macro_state="RECOVERY",
        transmission_phase="EXPANSION",
        sector_phase="EARLY",
        health_archetype="COMPOUNDER",
        valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP",
        valuation_zone_ts="CHEAP",
        behavior_position="BULLISH",
        margin_of_safety=65.7,
        contextual_health_score=0.92,
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate_simulated)

    assert res.final_score >= 85.0
    assert "FULL_MARGIN" in res.display_flag
    assert "MUA_TOI_DA_DON_BAY" in res.recommendation


# ═══════════════════════════════════════════════════════════════════════════
# Step 1 — The Great Surgery: Causal DAG wiring tests
# WHY: coverage/coherence MUST oscillate per CausalGraph results,
#      NOT be hardcoded 0.85/0.88. These tests prove the wiring works.
# ═══════════════════════════════════════════════════════════════════════════


def test_coverage_coherence_not_hardcoded():
    """coverage/coherence must come from causal_confidence/causal_coherence,
    NOT from hardcoded defaults 0.85/0.88."""
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector

    # Mandate with explicit Causal DAG values
    mandate = BayesianMandate(
        symbol="HPG",
        action="OPEN",
        action_vn="Mở vị thế",
        expected_utility=0.7,
        p_gain=0.72,
        calibration_penalty=0.0,
        allocation_pct=15.0,
        conviction=0.8,
        macro_state="RECOVERY",
        transmission_phase="EXPANSION",
        sector_phase="EARLY",
        health_archetype="COMPOUNDER",
        valuation_zone="CHEAP",
        valuation_zone_peer="CHEAP",
        valuation_zone_ts="CHEAP",
        behavior_position="BULLISH",
        margin_of_safety=35.0,
        contextual_health_score=0.85,
        causal_confidence=0.92,
        causal_coherence=0.87,
    )

    projector = CompositeScoreProjector()
    res = projector.project(mandate)

    # MUST reflect actual CausalGraph values, not hardcoded 0.85/0.88
    assert res.coverage == 0.92, f"Expected 0.92 from causal_confidence, got {res.coverage}"
    assert res.coherence == 0.87, f"Expected 0.87 from causal_coherence, got {res.coherence}"


def test_coverage_oscillates_with_causal_graph():
    """Different CausalGraph confidence → different coverage/coherence.
    Proves the values are NOT static hardcoded defaults."""
    from governor.company_state import BayesianMandate
    from governor.composite_score_projector import CompositeScoreProjector

    def _make_mandate(conf, coh):
        return BayesianMandate(
            symbol="VCB",
            action="HOLD",
            action_vn="Nắm giữ",
            expected_utility=0.5,
            p_gain=0.55,
            calibration_penalty=0.0,
            allocation_pct=0.0,
            conviction=0.6,
            macro_state="STABLE",
            transmission_phase="NEUTRAL",
            sector_phase="MID",
            health_archetype="FRANCHISE_BANK",
            valuation_zone="FAIR",
            valuation_zone_peer="FAIR",
            valuation_zone_ts="FAIR",
            behavior_position="NEUTRAL",
            causal_confidence=conf,
            causal_coherence=coh,
        )

    projector = CompositeScoreProjector()
    r_high = projector.project(_make_mandate(conf=0.95, coh=0.90))
    r_low = projector.project(_make_mandate(conf=0.20, coh=0.15))

    # High confidence → high coverage; Low confidence → low coverage
    assert r_high.coverage > r_low.coverage, f"Coverage should oscillate: high={r_high.coverage} > low={r_low.coverage}"
    assert r_high.coherence > r_low.coherence, f"Coherence should oscillate: high={r_high.coherence} > low={r_low.coherence}"
    # Neither should be the old hardcoded 0.85/0.88
    assert r_low.coverage != 0.85, "coverage must NOT be hardcoded 0.85"
    assert r_low.coherence != 0.88, "coherence must NOT be hardcoded 0.88"


def test_causal_dag_fields_in_bayesian_mandate():
    """BayesianMandate must carry causal_confidence, causal_coherence,
    causal_lag_months, causal_paths_found, sector_macro_score, sector_macro_lr."""
    from governor.company_state import BayesianMandate

    mandate = BayesianMandate(
        symbol="FPT",
        action="WAIT",
        action_vn="Chờ đợi",
        expected_utility=0.3,
        p_gain=0.45,
        calibration_penalty=0.1,
        allocation_pct=0.0,
        conviction=0.4,
        macro_state="STABLE",
        transmission_phase="NEUTRAL",
        sector_phase="NEUTRAL",
        health_archetype="COMPOUNDER",
        valuation_zone="FAIR",
        valuation_zone_peer="FAIR",
        valuation_zone_ts="FAIR",
        behavior_position="NEUTRAL",
        causal_confidence=0.78,
        causal_coherence=0.65,
        causal_lag_months=3,
        causal_paths_found=5,
        sector_macro_score=0.42,
        sector_macro_lr=1.14,
    )

    assert mandate.causal_confidence == 0.78
    assert mandate.causal_coherence == 0.65
    assert mandate.causal_lag_months == 3
    assert mandate.causal_paths_found == 5
    assert mandate.sector_macro_score == 0.42
    assert mandate.sector_macro_lr == 1.14


def test_sector_macro_lr_affects_gain_probability():
    """sector_macro_lr must actually shift P(Gain) through compute_gain_probability."""
    from governor.company_state import compute_gain_probability

    base_args = dict(
        macro_state="STABLE",
        transmission_phase="NEUTRAL",
        sector_phase="MID",
        health_archetype="COMPOUNDER",
        valuation_zone="FAIR",
        behavior_position="NEUTRAL",
        capital_allocation="TRANSITIONAL",
        macro_entropy=0.5,
        transmission_credit=50.0,
        archetype_prior_key="COMPOUNDER",
    )

    p_neutral, _, _ = compute_gain_probability(**base_args, sector_macro_lr=1.0)
    p_bullish, _, _ = compute_gain_probability(**base_args, sector_macro_lr=1.8)
    p_bearish, _, _ = compute_gain_probability(**base_args, sector_macro_lr=0.4)

    # Bullish sector macro → higher P(Gain); Bearish → lower
    assert p_bullish > p_neutral > p_bearish, (
        f"sector_macro_lr must shift P(Gain): bullish={p_bullish:.4f} > neutral={p_neutral:.4f} > bearish={p_bearish:.4f}"
    )
