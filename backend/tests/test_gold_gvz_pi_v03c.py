"""test_gold_gvz_pi_v03c.py — Unit tests Prediction Interval v0.3C (sigma_dyn=GVZ).

No network, no DB that. Synthetic:
  - build_frame: pred = walk-forward v0.2 (mu invariant), gvz column align;
  - frozen_scores: chi fit q tren <= 2024, test = sau cutoff;
  - pi_halfwidth: PI = mu +/- q*sigma_dyn;
  - evaluate: du keys, frozen max_cal <= 2024;
  - check_row: dung boolean theo tung dieu kien;
  - stop-rule logic: overall = all(checks).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_gvz_pi_v03c import (
    GVZ_TO_LOG20,
    build_frame,
    check_row,
    evaluate,
    frozen_scores,
    pi_halfwidth,
)
from src.research.gold_magnitude_forecast_v03 import add_log_return_target

N = 900
IDX = pd.date_range("2022-01-03", periods=N, freq="B")
_RS = np.random.RandomState(11)
_LEVEL = 100 * np.exp(np.cumsum(_RS.normal(0, 0.008, size=N)))
_X = _RS.normal(size=(N, 5))


def _mk_panel() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "GOLD": _LEVEL,
            "DXY_z": _X[:, 0],
            "DXY_mom5": _X[:, 1],
            "US10Y_d5": _X[:, 2],
            "TIP_mom5": _X[:, 3],
            "CB_IFS_z": _X[:, 4],
        },
        index=IDX,
    )
    return add_log_return_target(df, 20)


def _mk_gvz_series(panel: pd.DataFrame, value: float = 20.0) -> pd.Series:
    return pd.Series(value * GVZ_TO_LOG20, index=panel.index, name="gvz")


def test_gvz_scale_formula():
    assert abs(20 * GVZ_TO_LOG20 - 0.20 * np.sqrt(20 / 252)) < 1e-12


def test_build_frame_mu_invariant():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    assert {"y", "pred", "gvz", "logR20"} <= set(df.columns)
    # pred la walk-forward OOS cua v0.2 (khong phai fit lai tren ca panel)
    assert df["pred"].notna().sum() > 0
    assert df["gvz"].notna().all()
    # gia tri gvz dung scale
    assert np.allclose(df["gvz"], 20 * GVZ_TO_LOG20, atol=1e-12)


def test_build_frame_gvz_inject_respected():
    panel = _mk_panel()
    gvz = _mk_gvz_series(panel, value=40.0)
    df = build_frame(panel, gvz_series=gvz)
    assert np.allclose(df["gvz"], 40 * GVZ_TO_LOG20, atol=1e-12)


def test_frozen_scores_cutoff():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    scores, test, qs = frozen_scores(df)
    assert len(scores) > 0
    assert set(qs) == {0.80, 0.90}
    # test chi chua rows sau 2024
    assert (test.index > "2024-12-31").all()
    assert qs[0.80] > 0 and qs[0.90] > qs[0.80]


def test_pi_halfwidth_formula():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    hw = pi_halfwidth(df, q=2.0)
    assert np.allclose(hw, 2.0 * 20 * GVZ_TO_LOG20, atol=1e-12)


def test_evaluate_keys_and_frozen():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    scores, test, qs = frozen_scores(df)
    g = evaluate(df, scores, test, qs, panel)
    for key in (
        "n_cal",
        "n_test",
        "max_cal_date",
        "coverage_80_cal",
        "coverage_80_test",
        "coverage_90_test",
        "mid_mae_test",
        "naive_mae_test",
        "mid_rmse_test",
        "naive_rmse_test",
        "robust_1pct",
        "robust_5pct",
        "regime_80",
        "year_80",
    ):
        assert key in g
    assert g["max_cal_date"] <= "2024-12-31"
    assert 0 <= g["coverage_80_test"] <= 1


def test_check_row_returns_all_keys():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    scores, test, qs = frozen_scores(df)
    g = evaluate(df, scores, test, qs, panel)
    chk = check_row(g)
    assert set(chk) == {"coverage80", "coverage90", "midpoint_mae", "robust", "regime80", "frozen"}
    assert all(isinstance(v, bool) for v in chk.values())


def test_stop_rule_false_when_coverage80_outside():
    panel = _mk_panel()
    df = build_frame(panel, gvz_series=_mk_gvz_series(panel))
    scores, test, qs = frozen_scores(df)
    g = evaluate(df, scores, test, qs, panel)
    g["coverage_80_test"] = 0.50  # gia lap fail
    chk = check_row(g)
    assert chk["coverage80"] is False
    assert all(chk.values()) is False  # stop-rule trigger


def test_gaussian_calibration_produces_coverage_near_level():
    """Du lieu ideal (residual scale khong doi, sigma_dyn dung) -> coverage OOS ~ level."""
    n = 4000
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rs = np.random.RandomState(5)
    sig = np.full(n, 0.03)
    pred = rs.normal(0, 0.005, n)
    y = pred + rs.normal(0, sig)
    df = pd.DataFrame({"y": y, "pred": pred, "gvz": sig}, index=idx)
    panel = pd.DataFrame({"GOLD": 100 * np.exp(np.cumsum(np.full(n, 0.001)))}, index=idx)
    scores, test, qs = frozen_scores(df)
    g = evaluate(df, scores, test, qs, panel)
    assert 0.72 <= g["coverage_80_test"] <= 0.88
    assert 0.83 <= g["coverage_90_test"] <= 0.97
