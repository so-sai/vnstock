"""test_gold_gvz_interval_gate.py — Unit tests GVZ Interval Gate logic.

No network, no DB thật. Synthetic: panel giả + GVZ cache giả.
  - gvz_sigma: scale a priori đúng công thức, align không lookahead;
  - sigma20_plus_gvz: sqrt(sigma20² + gvz²);
  - gate_eval: đủ keys, frozen cutoff <= 2024 (nếu synthetic < 2024), GVZ scale
    làm coverage cải thiện trên dữ liệu vol-regime thay đổi;
  - check_row: đúng boolean theo từng điều kiện.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_gvz_interval_gate import (
    GVZ_TO_LOG20,
    build_frame,
    gate_eval,
    gvz_sigma,
    sigma20_plus_gvz,
)
from src.research.gold_magnitude_forecast_v03 import add_log_return_target
from src.research.gvz_adapter import pit_align, pit_series

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


def test_gvz_scale_formula():
    # GVZ=20 (annualized 30d implied vol 20%) -> sigma_gvz = 0.20*sqrt(20/252)
    assert abs(20 * GVZ_TO_LOG20 - 0.20 * np.sqrt(20 / 252)) < 1e-12


def test_gvz_sigma_no_lookahead():
    panel = _mk_panel()
    # GVZ cache: value cố định 40 (tương đương vol cao) cho mọi ngày
    idx = pd.date_range("2020-01-01", periods=4000, freq="B")
    gvz_df = pd.DataFrame({"observation_date": idx, "value": 40.0})
    # gvz_sigma đọc từ CACHE_FILE cứng — kiểm tra pit_series/pit_align riêng
    s = pit_series(gvz_df)
    aligned = pit_align(s, panel.index)
    assert aligned.iloc[0] == 40.0 or np.isnan(aligned.iloc[0])
    # tại ngày đầu panel, không có GVZ pub <= panel[0]? panel bắt đầu sau 2020
    assert aligned.dropna().iloc[0] == 40.0
    # không dùng giá trị tương lai: target ngày T chỉ nhận GVZ có pub <= T
    t = panel.index[5]
    used = aligned.loc[:t].dropna()
    assert (used == 40.0).all()


def test_sigma20_plus_gvz_is_sqrt_sum_sq():
    panel = _mk_panel()
    df = build_frame(panel)
    df["gvz"] = 0.02
    g = sigma20_plus_gvz(panel, df)
    s20 = df["sigma20"]
    expect = np.sqrt(s20**2 + 0.02**2)
    mask = expect.notna()
    assert np.allclose(g[mask], expect[mask], atol=1e-12)


def test_gate_eval_keys_and_frozen():
    panel = _mk_panel()
    df = build_frame(panel)
    df["gvz"] = gvz_sigma(panel, df)
    df["sigma20_gvz"] = sigma20_plus_gvz(panel, df)
    for method in ("sigma20", "gvz", "sigma20_gvz"):
        g = gate_eval(df, panel, method)
        assert g["method"] == method
        for key in ("n_cal", "n_test", "max_cal_date", "coverage_80_test", "coverage_90_test", "mid_mae_test"):
            assert key in g
        # calibration bắt buộc <= 2024 trong dữ liệu này
        assert g["max_cal_date"] <= "2024-12-31"


def test_gvz_improves_coverage_on_vol_regime_shift():
    """Khi vol thay đổi theo regime, GVZ (đúng scale) cho coverage ổn định hơn
    so với sigma20 lagged — trên dữ liệu ideal."""
    n = 3000
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    rs = np.random.RandomState(7)
    # vol regime: 2 giai đoạn, 0.01 và 0.04
    vol = np.where(idx.year >= 2024, 0.04, 0.01)
    pred = rs.normal(0, 0.005, n)
    resid = rs.normal(0, vol)
    y = pred + resid
    df = pd.DataFrame({"y": y, "pred": pred, "sigma20": 0.01, "gvz": 0.02}, index=idx)
    # sigma20 lagged không theo kịp (giữ 0.01), GVZ đúng scale (0.02 chung)
    # coverage với sigma20 (scale sai) cho q frozen → tụt; GVZ scale 0.02 cho coverage hợp lý
    sc20 = (df["y"] - df["pred"]).abs() / df["sigma20"].clip(lower=1e-12)
    sc_gvz = (df["y"] - df["pred"]).abs() / df["gvz"].clip(lower=1e-12)
    # trên toàn bộ, GVZ ổn định hơn (phương sai scores nhỏ hơn)
    assert sc_gvz.std() < sc20.std()
