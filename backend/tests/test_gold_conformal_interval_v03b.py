"""test_gold_conformal_interval_v03b.py — Unit tests Locally-Adaptive Conformal PI.

No network, no DB thật. Synthetic data chỉ kiểm tra LOGIC:
  - sigma20/ewma20 PIT-safe (chỉ dùng logR đã realized tại t-h);
  - conformal_scores công thức đúng;
  - frozen_quantiles: trên dữ liệu Gaussian, q_80 cho coverage ~0.80 calibration;
  - interval_coverage / by_year / by_regime / robustness không crash, hợp lệ;
  - gate_eval tôn trọng calibration cutoff <= 2024 (nếu synthetic < 2024);
  - EWMA20 deterministic.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_conformal_interval_v03b import (
    CALIB_CUTOFF,
    LEVELS,
    conformal_scores,
    coverage_by_regime,
    coverage_by_year,
    ewma20_vol,
    frozen_quantiles,
    interval_coverage,
    robustness_extreme,
    sigma20_rolling,
)
from src.research.gold_magnitude_forecast_v03 import add_log_return_target

N = 800
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


def _mk_gaussian_scores_df(n: int = 3000) -> pd.DataFrame:
    """y - pred = N(0, 0.02) tĩnh, sigma_dyn đúng 0.02 → s |chi|-like, q80→0.80."""
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rs = np.random.RandomState(3)
    pred = rs.normal(0, 0.005, n)
    y = pred + rs.normal(0, 0.02, n)
    return pd.DataFrame(
        {"y": y, "pred": pred, "sigma20": np.full(n, 0.02), "ewma20": np.full(n, 0.02)},
        index=idx,
    )


def test_sigma20_rolling_pit():
    df = _mk_panel()
    s = sigma20_rolling(df, 20, window=20)
    # tại t, chỉ dùng logR20[j] với j+h <= t → j <= t-h
    t = IDX[120]
    realized = df["logR20"].iloc[: 120 - 20 + 1].dropna().tail(20)
    assert abs(s.loc[t] - realized.std()) < 1e-12
    # 20+19 ngày đầu NaN (chưa đủ window)
    assert np.isnan(s.iloc[0])
    assert np.isnan(s.iloc[38])  # cần tối thiểu 20 giá trị sau lag 20 → index 39


def test_ewma20_vol_pit():
    df = _mk_panel()
    e = ewma20_vol(df, 20, span=20)
    assert e.notna().sum() > 0
    assert (e.dropna() > 0).all()
    # PIT: tại t chỉ dùng logR20^2 tới j <= t-h
    t = IDX[120]
    realized = df["logR20"].iloc[: 101].dropna() ** 2
    expect = realized.ewm(span=20, adjust=False).mean().iloc[-1] ** 0.5
    assert abs(e.loc[t] - expect) < 1e-9


def test_conformal_scores_formula():
    df = _mk_gaussian_scores_df(500)
    sc = conformal_scores(df, df["sigma20"])
    expect = (df["y"] - df["pred"]).abs() / df["sigma20"]
    assert np.allclose(sc.values, expect.values, atol=1e-12)
    assert (sc >= 0).all()


def test_conformal_scores_skips_zero_sigma():
    df = _mk_gaussian_scores_df(100)
    df["sigma20"] = 0.0
    sc = conformal_scores(df, df["sigma20"])
    assert len(sc) == 0


def test_frozen_quantiles_coverage_gaussian():
    """Trên dữ liệu Gaussian chuẩn, q80 frozen cho coverage ~0.80."""
    df = _mk_gaussian_scores_df(6000)
    cal = df.iloc[:3000]
    test = df.iloc[3000:]
    sc = conformal_scores(cal, cal["sigma20"])
    qs = frozen_quantiles(sc, LEVELS)
    assert abs(qs[0.80] - np.quantile(np.abs(np.random.normal(0, 1, 100000)), 0.80)) < 0.05
    cov80 = interval_coverage(test, test["sigma20"], qs[0.80])
    assert 0.75 <= cov80 <= 0.85
    cov90 = interval_coverage(test, test["sigma20"], qs[0.90])
    assert 0.86 <= cov90 <= 0.94


def test_interval_coverage_valid():
    df = _mk_gaussian_scores_df(500)
    cov = interval_coverage(df, df["sigma20"], 1.0)
    assert 0 <= cov <= 1


def test_coverage_by_year_keys():
    df = _mk_gaussian_scores_df(600)
    cy = coverage_by_year(df, df["sigma20"], 1.0)
    assert "2022" in cy and "2023" in cy
    for v in cy.values():
        assert 0 <= v <= 1


def test_coverage_by_regime_subset():
    df = _mk_gaussian_scores_df(600)
    panel = _mk_panel()
    cr = coverage_by_regime(df, panel, df["sigma20"], 1.0)
    assert isinstance(cr, dict)
    for v in cr.values():
        assert 0 <= v <= 1


def test_robustness_extreme_subset():
    df = _mk_gaussian_scores_df(3000)
    rb = robustness_extreme(df, df["sigma20"], 1.0, drop_frac=0.05)
    assert rb["n"] < 3000
    assert rb["n"] > 0
    assert 0 <= rb["coverage"] <= 1


def test_ewma20_deterministic():
    df = _mk_panel()
    e1 = ewma20_vol(df, 20, span=20)
    e2 = ewma20_vol(df, 20, span=20)
    pd.testing.assert_series_equal(e1, e2)


def test_calibration_cutoff_constant():
    assert CALIB_CUTOFF == "2024-12-31"
