"""Lock Grid Search V2 optimal parameters into production fusion defaults.

Commit ecfaa64 grid results (2021-04 → 2026-08, step 0.05, 379 combos):
  Sharpe +0.819, Ret +77.9%, WinRate 46.4%, EmergencyExits 8.
  Optimal: M2(Fund)=0.45, M1(Macro)=0.15, Alpha=0.20, M3(Behav)=0.20
           Entry=0.55 Exit=0.30 Stop=7% Take=25% Hold=15.
"""

from src.governor import multi_factor_fusion as mf


def test_v2_weights_match_grid_search_optimal():
    w = mf.DEFAULT_FUSION_WEIGHTS
    assert w["M2_FUNDAMENTAL"] == 0.45
    assert w["M1_MACRO"] == 0.15
    assert w["ALPHA_MOMENTUM"] == 0.20
    assert w["M3_BEHAVIORAL"] == 0.20


def test_v2_weights_sum_to_one():
    assert abs(sum(mf.DEFAULT_FUSION_WEIGHTS.values()) - 1.0) < 1e-6


def test_v2_weights_anchor_on_m2_not_m3():
    """Kills the pre-V2 M3=0.70 behavioral bias."""
    w = mf.DEFAULT_FUSION_WEIGHTS
    assert w["M2_FUNDAMENTAL"] > w["M3_BEHAVIORAL"]


def test_v2_weights_within_5layer_matrix_bounds():
    """Approved 5-Layer Matrix bounds from grid_search_v2.generate_weight_grid."""
    assert 0.35 <= mf.DEFAULT_FUSION_WEIGHTS["M2_FUNDAMENTAL"] <= 0.50
    assert 0.10 <= mf.DEFAULT_FUSION_WEIGHTS["M1_MACRO"] <= 0.20
    assert 0.15 <= mf.DEFAULT_FUSION_WEIGHTS["ALPHA_MOMENTUM"] <= 0.30
    assert 0.10 <= mf.DEFAULT_FUSION_WEIGHTS["M3_BEHAVIORAL"] <= 0.25


def test_v2_trailing_stop_take():
    assert mf.TRAILING_STOP_PCT == 0.07
    assert mf.TRAILING_TAKE_PCT == 0.25


def test_v2_entry_exit_thresholds():
    assert mf.ENTRY_THRESHOLD == 0.55
    assert mf.EXIT_THRESHOLD == 0.30


def test_v2_min_hold_days():
    assert mf.MIN_HOLD_DAYS == 15


def test_fusion_uses_v2_defaults_when_no_weights_given():
    fusion = mf.MultiFactorFusion()
    assert fusion.weights == mf.DEFAULT_FUSION_WEIGHTS
    assert fusion.trailing_stop == 0.07
    assert fusion.trailing_take == 0.25


def test_decide_uses_v2_entry_threshold():
    fusion = mf.MultiFactorFusion()
    fs = mf.FactorScores(symbol="FPT", date="2026-08-06", sector="Technology")
    action, _, reason = fusion._decide(fs, 0.54, None)
    assert action == "HOLD"
    action, _, _ = fusion._decide(fs, 0.56, None)
    assert action == "BUY"


def test_decide_uses_v2_exit_threshold():
    fusion = mf.MultiFactorFusion()
    fs = mf.FactorScores(symbol="FPT", date="2026-08-06", sector="Technology")
    pos = mf.Position(
        symbol="FPT",
        shares=100,
        entry_price=10.0,
        entry_date="2026-07-01",
        peak_price=10.0,
        unrealized_pnl_pct=0.0,
    )
    # Composite between 0.30 and 0.55 → HOLD (healthy, not degraded)
    action, _, reason = fusion._decide(fs, 0.32, pos)
    assert action == "HOLD"
    # Composite below 0.30 → SELL (degraded)
    action, _, reason = fusion._decide(fs, 0.29, pos)
    assert action == "SELL"


def test_decide_v2_trailing_stop_boundary():
    fusion = mf.MultiFactorFusion()
    fs = mf.FactorScores(symbol="FPT", date="2026-08-06", sector="Technology")
    pos = mf.Position(
        symbol="FPT",
        shares=100,
        entry_price=10.0,
        entry_date="2026-07-01",
        peak_price=10.0,
        unrealized_pnl_pct=-0.08,  # beyond -7% stop
    )
    action, _, _ = fusion._decide(fs, 0.6, pos)
    assert action == "SELL"


def test_decide_v2_trailing_take_boundary():
    fusion = mf.MultiFactorFusion()
    fs = mf.FactorScores(symbol="FPT", date="2026-08-06", sector="Technology")
    pos = mf.Position(
        symbol="FPT",
        shares=100,
        entry_price=10.0,
        entry_date="2026-07-01",
        peak_price=12.6,
        unrealized_pnl_pct=0.26,  # beyond +25% take
    )
    action, _, _ = fusion._decide(fs, 0.6, pos)
    assert action == "SELL"
