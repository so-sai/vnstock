"""test_gold_calibration_audit_v03a.py — Unit tests Calibration & Interval Audit.

No network, no DB thật. Synthetic data chỉ để kiểm tra LOGIC:
  - standardized_residuals đúng công thức;
  - empirical_coverage ≈ nominal khi residual đúng Gaussian (z=1.28 → ~0.80);
  - coverage_by_year / by_regime trả dict hợp lệ;
  - standardized_resid_stats mean≈0/std≈1/skew≈0/kurt≈0 cho Gaussian;
  - rolling_residual_vol không crash, ratio hợp lý;
  - extreme_move_coverage chỉ xét subset |y|>q90;
  - width_vs_realized_vol trả spearman hợp lệ;
  - walk_forward_empirical_z: deterministic, z_emp frozen có trong OOS rows,
    KHÔNG dùng OOS data (z_emp từ train expanding).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_calibration_audit_v03a import (
    Z_GAUSS,
    audit,
    coverage_by_regime,
    coverage_by_year,
    empirical_coverage,
    extreme_move_coverage,
    raw_residual_stats,
    rolling_residual_vol,
    standardized_resid_stats,
    standardized_residuals,
    trailing_vol_regime,
    walk_forward_empirical_z,
    width_vs_realized_vol,
)
from src.research.gold_magnitude_forecast_v03 import add_log_return_target

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


def _mk_gaussian_df(n: int = 2000) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rs = np.random.RandomState(3)
    resid_std = np.full(n, 0.02062)  # khớp std của y - pred
    y = rs.normal(0, 0.02, n)
    return pd.DataFrame(
        {
            "y": y,
            "pred": rs.normal(0, 0.005, n),
            "resid_std": resid_std * (1 + 0.05 * np.sin(np.arange(n) / 50.0)),
        },
        index=idx,
    )


def test_standardized_residuals_formula():
    df = _mk_gaussian_df(500)
    z = standardized_residuals(df)
    expect = (df["y"] - df["pred"]) / df["resid_std"]
    assert np.allclose(z.values, expect.values, atol=1e-12)
    assert len(z) == 500


def test_empirical_coverage_gaussian_80():
    df = _mk_gaussian_df(20000)
    cov = empirical_coverage(df, Z_GAUSS[0.80])
    assert 0.78 <= cov <= 0.82
    cov90 = empirical_coverage(df, Z_GAUSS[0.90])
    assert 0.88 <= cov90 <= 0.92


def test_empirical_coverage_naive_gap_detection():
    """Residual vol thực lớn hơn model resid_std → coverage Gaussian dưới nominal."""
    rs = np.random.RandomState(5)
    n = 20000
    # actual resid vol 0.03 > model resid_std 0.02 → z có std ~1.5
    y = rs.normal(0, 0.03, n)
    df = pd.DataFrame({"y": y, "pred": 0.0, "resid_std": np.full(n, 0.02)},
                      index=pd.date_range("2022-01-03", periods=n, freq="B"))
    cov = empirical_coverage(df, Z_GAUSS[0.80])
    assert cov < 0.75


def test_coverage_by_year_keys():
    df = _mk_gaussian_df(600)
    cy = coverage_by_year(df, Z_GAUSS[0.80])
    assert "2022" in cy and "2023" in cy
    for v in cy.values():
        assert 0 <= v <= 1


def test_trailing_vol_regime_pit():
    panel = _mk_panel()
    reg = trailing_vol_regime(panel, 20)
    # 20 ngày đầu NaN (rolling) — không có look-ahead: vol tại t chỉ dùng r tới t
    assert np.isnan(reg.iloc[0])
    vals = reg.dropna()
    assert set(vals.unique()) <= {"Q1", "Q2", "Q3"}


def test_coverage_by_regime_subset():
    df = _mk_gaussian_df(600)
    panel = _mk_panel()
    cr = coverage_by_regime(df, panel, Z_GAUSS[0.80])
    assert isinstance(cr, dict)
    for v in cr.values():
        assert 0 <= v <= 1


def test_standardized_resid_stats_gaussian():
    df = _mk_gaussian_df(5000)
    s = standardized_resid_stats(df)
    assert abs(s["mean"]) < 0.1
    assert abs(s["std"] - 1.0) < 0.1
    assert abs(s["skew"]) < 0.2
    assert abs(s["kurtosis"]) < 0.5


def test_raw_residual_stats():
    df = _mk_gaussian_df(500)
    r = raw_residual_stats(df)
    assert r["n"] == 500
    assert r["min"] < r["max"]


def test_rolling_residual_vol_ratio():
    df = _mk_gaussian_df(600)
    rv = rolling_residual_vol(df, window=20)
    assert rv["ratio_mean"] == rv["ratio_mean"]  # not NaN
    assert 0.5 < rv["ratio_mean"] < 2.0
    assert -1 <= rv["corr_absresid_std"] <= 1


def test_extreme_move_coverage_subset():
    df = _mk_gaussian_df(5000)
    e = extreme_move_coverage(df, Z_GAUSS[0.80], q=0.90)
    assert e["n"] < 5000 * 0.2  # subset nhỏ hơn hẳn
    assert 0 <= e["coverage"] <= 1


def test_width_vs_realized_vol():
    df = _mk_gaussian_df(500)
    w = width_vs_realized_vol(df, Z_GAUSS[0.80])
    assert -1 <= w <= 1


def test_walk_forward_empirical_z_deterministic_and_frozen():
    df = _mk_panel()
    feats = ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "CB_IFS_z"]
    o1 = walk_forward_empirical_z(df, feats, 20)
    o2 = walk_forward_empirical_z(df, feats, 20)
    pd.testing.assert_frame_equal(o1, o2)
    sub = o1.dropna(subset=["pred", "resid_std", "z_emp_80", "z_emp_90"])
    assert len(sub) > 0
    assert (sub["z_emp_80"] > 1.0).any()  # |z| quantile >= Gaussian 1.28 thường lớn hơn 1
    assert (sub["z_emp_90"] >= sub["z_emp_80"]).all()


def test_audit_shape():
    df = _mk_gaussian_df(800)
    panel = _mk_panel()
    res = audit(df, panel)
    assert "coverage_80" in res and "coverage_90" in res
    assert "kurtosis" in res
    assert "raw" in res and "rolling" in res and "extreme_80" in res
    assert -1 <= res["width_vs_y_spearman"] <= 1
