"""test_gold_magnitude_forecast_v03.py — Unit tests Gold H20 Magnitude Gate.

No network, no DB thật. Kiểm tra:
  - add_log_return_target: logR_h = log(G_{t+h}/G_t) đúng (không look-ahead trong value).
  - expanding_mean_baseline: PIT-safe (chỉ dùng logR đã realized tại t-h).
  - walk_forward_regression: deterministic, OOS bắt đầu >= MIN_TRAIN, resid_std đủ.
  - evaluate_magnitude: mae/rmse/spearman/sign_acc/coverage80 hợp lệ.
  - robustness_extreme: loại 1% cực trị → không crash, n giảm.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_forecast_engine_v01 import MIN_TRAIN
from src.research.gold_magnitude_forecast_v03 import (
    INTERVAL_Z,
    H,
    add_log_return_target,
    evaluate_magnitude,
    expanding_mean_baseline,
    robustness_extreme,
    walk_forward_regression,
)

N = 500
IDX = pd.date_range("2022-01-03", periods=N, freq="B")
_RS = np.random.RandomState(7)
_LEVEL = 100 * np.exp(np.cumsum(_RS.normal(0, 0.01, size=N)))
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


def test_add_log_return_target_formula():
    df = _mk_panel()
    r20 = df["logR20"].dropna()
    # spot check 2 điểm
    i0, i1 = r20.index[0], r20.index[5]
    g = df["GOLD"]
    expect0 = np.log(g.loc[g.index[20]] / g.iloc[0])
    assert abs(r20[i0] - expect0) < 1e-12
    expect1 = np.log(g.loc[g.index[25]] / g.iloc[5])
    assert abs(r20[i1] - expect1) < 1e-12
    # 20 điểm cuối NaN (chưa có forward)
    assert np.isnan(df["logR20"].iloc[-1]) and np.isnan(df["logR20"].iloc[-20])


def test_expanding_mean_baseline_pit():
    df = _mk_panel()
    s = expanding_mean_baseline(df, 20)
    # tại index 100 chỉ dùng logR20[0..80] (j <= t-h)
    obs = s.dropna()
    assert len(obs) > 0
    # t = 100: chỉ mean của logR20 có j+h <= t → j <= 80
    t = IDX[100]
    realized = df["logR20"].iloc[: 100 - H + 1].dropna().mean()
    assert abs(s.loc[t] - realized) < 1e-12
    # NaN trong 20 điểm đầu (chưa đủ realized)
    assert np.isnan(s.iloc[:20]).any()


def test_walk_forward_regression_deterministic_and_oos():
    df = _mk_panel()
    feats = ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "CB_IFS_z"]
    o1 = walk_forward_regression(df, feats, 20)
    o2 = walk_forward_regression(df, feats, 20)
    pd.testing.assert_frame_equal(o1, o2)
    pred_idx = o1.dropna(subset=["pred"]).index
    assert len(pred_idx) > 0
    assert pred_idx.min() >= IDX[MIN_TRAIN]
    assert o1["pred"].notna().sum() > 0


def test_walk_forward_regression_resid_std_present():
    df = _mk_panel()
    o = walk_forward_regression(df, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "CB_IFS_z"], 20)
    sub = o.dropna(subset=["pred", "resid_std"])
    assert len(sub) > 0
    assert (sub["resid_std"] > 0).all()


def test_evaluate_magnitude_metrics():
    df = _mk_panel()
    reg = walk_forward_regression(df, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "CB_IFS_z"], 20)
    df["pred_model"] = reg["pred"]
    df["resid_std"] = reg["resid_std"]
    ev = evaluate_magnitude(df, "pred_model", 20)
    assert ev["n"] > 0
    assert 0 < ev["mae"] < 0.2
    assert 0 < ev["rmse"] < 0.3
    assert -1 <= ev["spearman"] <= 1
    # sign_acc: pred hầu hết khác 0 → hợp lệ
    assert 0 <= ev["sign_acc"] <= 1
    # coverage80: interval ±1.28*resid_std → coverage hợp lý (khoảng 0.75-0.85, test 0.6-0.95)
    assert 0.6 <= ev["coverage80"] <= 0.95


def test_evaluate_magnitude_naive_zero():
    df = _mk_panel()
    df["pred_naive"] = 0.0
    ev = evaluate_magnitude(df, "pred_naive", 20)
    assert ev["n"] > 0
    assert ev["mean_abs"] > 0
    assert ev["sign_acc"] != ev["sign_acc"]  # NaN (p=0 everywhere → không có sign)


def test_robustness_extreme_removes_outliers():
    df = _mk_panel()
    df["pred_naive"] = 0.0
    full = evaluate_magnitude(df, "pred_naive", 20)
    rb = robustness_extreme(df, "pred_naive", 20, drop_pct=0.01)
    assert rb["n"] <= full["n"]
    assert rb["n"] > 0
    assert rb["mae"] <= full["mae"] + 1e-9  # loại cực trị → MAE giảm/không tăng


def test_interval_constant_std_coverage():
    """Sanity: với resid_std chuẩn, coverage80 ≈ 0.80 (nominal)."""
    y = np.random.RandomState(1).normal(0, 0.02, 2000)
    p = np.zeros_like(y)
    rs = np.full_like(y, np.std(y))
    lo = p - INTERVAL_Z * rs
    hi = p + INTERVAL_Z * rs
    cov = float(np.mean((y >= lo) & (y <= hi)))
    assert 0.78 <= cov <= 0.82


def test_per_year_mae_uses_years():
    from src.research.gold_magnitude_forecast_v03 import _per_year_mae

    df = _mk_panel()
    df["pred_naive"] = 0.0
    py = _per_year_mae(df, "pred_naive", 20)
    assert "2022" in py or "2023" in py or "2024" in py
    for v in py.values():
        assert v > 0
