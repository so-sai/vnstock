"""test_gold_forecast_engine_v02.py — Unit tests Gold v0.2 model comparison.

No network, no DB thật. Kiểm tra:
  - compare_models: AUC/delta_auc, logloss/brier, spearman, per-year ΔAUC, hit-rate.
  - PIT purity: _pit_latest_step không look-ahead (via gold_cb_feature_audit helper).
  - Determinism: walk_forward cùng input → cùng output (tái lập).
  - compare_models dùng intersection OOS (không trộn prediction khác tập).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_cb_feature_audit import _pit_latest_step
from src.research.gold_forecast_engine_v01 import walk_forward
from src.research.gold_forecast_engine_v02 import (
    _brier,
    _high_conf_hit,
    _logloss,
    _per_year_delta_auc,
    _spearman,
    compare_models,
)

# ── Fixtures ────────────────────────────────────────────────────────────────
N = 400
IDX = pd.date_range("2022-01-03", periods=N, freq="B")
_RS = np.random.RandomState(42)
X1 = _RS.normal(size=(N, 4))
Y = (X1[:, 0] + 0.3 * _RS.normal(size=N) > 0).astype(float)


def _mk_panel() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "DXY_z": X1[:, 0],
            "DXY_mom5": X1[:, 1],
            "US10Y_d5": X1[:, 2],
            "TIP_mom5": X1[:, 3],
            "CB_IFS_z": X1[:, 0] + 0.5 * _RS.normal(size=N),
        },
        index=IDX,
    )
    for h in (20, 60, 120):
        fwd = np.full(N, np.nan)
        fwd[: N - h] = _RS.normal(size=N - h)
        df[f"R{h}_pos"] = (fwd > 0).astype(float)
    return df


def test_pit_latest_step_no_lookahead():
    dates = pd.date_range("2026-01-05", periods=8, freq="D")
    rows = [
        ("2025-12-31", 10.0, "2026-01-01"),
        ("2026-01-31", 20.0, "2026-02-01"),
        ("2026-01-31", 25.0, "2026-02-15"),
        ("2025-12-31", 12.0, "2026-01-08"),
    ]
    out = _pit_latest_step(rows, dates)
    assert out[0] == 10.0  # 01-05: chỉ obs1 pub 01-01
    assert out[3] == 12.0  # 01-08: obs1 revision
    assert out[7] == 12.0  # 01-12: obs2 (pub 02-01) chưa nhìn thấy → không look-ahead


def test_walk_forward_deterministic():
    panel = _mk_panel()
    o1 = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    o2 = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    pd.testing.assert_frame_equal(o1, o2)


def test_walk_forward_no_lookahead_in_pred():
    """prob OOS phải nằm trong block đã train (start>=MIN_TRAIN=250) — không leak."""
    panel = _mk_panel()
    out = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    pred_idx = out.dropna(subset=["prob"]).index
    assert len(pred_idx) > 0
    assert pred_idx.min() >= IDX[250]


def test_compare_models_metrics_and_intersection():
    o1 = walk_forward(_mk_panel(), ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    o2 = walk_forward(_mk_panel(), ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "CB_IFS_z"], 20)
    comp = compare_models(o1, o2, 20)
    assert comp["n_oos"] > 0
    assert "delta_auc" in comp and "logloss_m1" in comp and "logloss_m2" in comp
    assert comp["brier_m1"] is not None and comp["brier_m2"] is not None
    assert "spearman_prob" in comp
    assert "delta_auc_per_year" in comp and isinstance(comp["delta_auc_per_year"], dict)
    # logloss phải hợp lệ (0..~1)
    assert 0 <= comp["logloss_m1"] <= 1.5


def test_compare_models_redundant_models_high_spearman():
    """Cùng feature set → prediction gần như trùng → spearman ≈ 1."""
    panel = _mk_panel()
    o1 = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    o2 = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    comp = compare_models(o1, o2, 20)
    assert comp["spearman_prob"] > 0.9


def test_compare_models_uses_intersection_not_union():
    """o1 có prediction nhiều hơn o2 → metrics chỉ tính trên chung."""
    panel = _mk_panel()
    o1 = walk_forward(panel, ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"], 20)
    o2 = o1.copy()
    # cắt bớt 5 dòng prediction của o2
    drop_idx = o2.dropna(subset=["prob"]).index[:5]
    o2.loc[drop_idx, "prob"] = np.nan
    comp = compare_models(o1, o2, 20)
    expected_n = o1.dropna(subset=["prob"]).shape[0] - 5
    assert comp["n_oos"] == expected_n


def test_per_year_delta_auc():
    idx = pd.date_range("2022-01-03", periods=10, freq="B")
    o1 = pd.DataFrame(
        {"y": [1, 0, 1, 0, 1, 1, 0, 1, 0, 1], "prob": [0.6, 0.4, 0.7, 0.3, 0.6, 0.7, 0.3, 0.8, 0.2, 0.6]},
        index=idx,
    )
    o2 = o1.copy()
    o2["prob_2"] = o1["prob"]  # đổi tên cho join
    o1["prob_2"] = o1["prob"]
    out = _per_year_delta_auc(o1, o2, 20)
    assert out == {"2022": 0.0}


def test_per_year_delta_auc_skips_single_class():
    idx = pd.date_range("2022-01-03", periods=5, freq="B")
    o1 = pd.DataFrame({"y": [1, 1, 1, 1, 1], "prob": [0.6, 0.7, 0.6, 0.8, 0.9]}, index=idx)
    o2 = o1.copy()
    o1["prob_2"] = o1["prob"]
    o2["prob_2"] = o2["prob"]
    assert _per_year_delta_auc(o1, o2, 20) == {}


def test_logloss_and_brier():
    y = np.array([1, 0, 1, 0])
    p = np.array([0.9, 0.1, 0.8, 0.2])
    assert 0 < _logloss(y, p) < 0.5
    assert 0 < _brier(y, p) < 0.1
    assert _logloss(np.array([1.0]), np.array([0.5])) is None  # <2 obs


def test_spearman():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    assert abs(_spearman(a, b) - 1.0) < 1e-9
    assert np.isnan(_spearman(a, np.full(5, np.nan)))


def test_high_conf_hit():
    df = pd.DataFrame(
        {
            "y": [1, 1, 0, 0, 1, 0, 1, 0],
            "prob": [0.9, 0.8, 0.7, 0.3, 0.2, 0.5, 0.55, 0.4],
            "prob_2": [0.8, 0.8, 0.7, 0.3, 0.2, 0.5, 0.55, 0.4],
        }
    )
    out = _high_conf_hit(df)
    assert out["hit_high_n_m1"] == 5  # >0.6: 3; <0.4: 2 → 5
    assert 0 <= out["hit_high_m1"] <= 1
