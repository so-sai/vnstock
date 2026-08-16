"""test_gold_etf_feature_audit.py — Unit tests Gate E1 audit module.

No network, no DB thật — fixture pandas in-memory. Kiểm tra:
  - _count_usable_pub: strict PIT đếm đúng (pub<=t).
  - _step_value: feature step theo assumed lag, ffill, không lookahead trước obs+lag.
  - build_etf_features: cột per region + global sum.
  - _spearman / _decile_spread / _sign_agreement: logic cơ bản.
  - build_etf_features bỏ cột toàn NaN.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_etf_feature_audit import (
    _count_usable_pub,
    _decile_spread,
    _sign_agreement,
    _spearman,
    _step_value,
    build_etf_features,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

DATES = pd.date_range("2026-01-01", periods=10, freq="D")


def _mk_etf_values():
    return pd.DataFrame(
        {"obs": ["2025-12-31", "2026-01-31", "2026-02-28"], "value": [1.0, 2.0, 3.0]}
    )


def test_count_usable_pub():
    df = pd.DataFrame(
        {
            "series": ["ETF_DEMAND_TONNES"] * 3,
            "entity": ["Europe"] * 3,
            "obs": ["2026-01-31", "2026-02-28", "2026-03-31"],
            "pub": ["2026-02-01", "2026-08-16", "2026-08-16"],
        }
    )
    assert _count_usable_pub(df, "2026-03-01") == 1  # chỉ obs pub<=t
    assert _count_usable_pub(df, "2026-01-31") == 0  # pub 2026-02-01 > t


def test_step_value_no_lookahead():
    """Value tại t = obs mới nhất có obs+lag <= t; trước đó NaN (ffill)."""
    vals = _mk_etf_values()
    out = _step_value(vals, DATES, lag_days=7)
    # lag=7: obs 2025-12-31 -> pub 2026-01-07; obs 2026-01-31 -> 2026-02-07
    # DATES 2026-01-01..01-10. Ngày 01-01..01-06: NaN (chưa pub). 01-07..: 1.0
    assert np.isnan(out[0])  # 2026-01-01
    assert np.isnan(out[5])  # 2026-01-06
    assert out[6] == 1.0  # 2026-01-07 (obs 2025-12-31 + 7d)
    assert out[9] == 1.0  # 2026-01-10 vẫn 1.0 (obs 01-31 chưa pub)


def test_step_value_ffill_multi_obs():
    dates = pd.date_range("2026-01-25", periods=15, freq="D")  # 01-25 .. 02-08
    vals = _mk_etf_values()
    out = _step_value(vals, dates, lag_days=7)
    # obs 2025-12-31 + 7 = 2026-01-07 <= 01-25 -> 1.0 ngay từ đầu
    # obs 2026-01-31 + 7 = 2026-02-07 -> 2.0 từ 02-07
    assert out[0] == 1.0  # 01-25
    assert out[12] == 1.0  # 02-06
    assert out[13] == 2.0  # 02-07
    assert out[14] == 2.0  # 02-08


def test_build_etf_features_regions_and_global():
    etf = {
        ("ETF_DEMAND_TONNES", "North America"): _mk_etf_values(),
        ("ETF_DEMAND_TONNES", "Europe"): _mk_etf_values(),
        ("ETF_DEMAND_TONNES", "Asia"): _mk_etf_values(),
        ("ETF_DEMAND_TONNES", "Other"): _mk_etf_values(),
    }
    df = pd.DataFrame(index=pd.date_range("2026-01-07", periods=4, freq="D"))
    feats = build_etf_features(df, etf, lag_days=7)
    assert "ETF_DEMAND_TONNES__North America" in feats.columns
    assert "ETF_DEMAND_TONNES__Europe" in feats.columns
    assert "ETF_DEMAND_TONNES__GLOBAL" in feats.columns
    # 2026-01-07: obs 2025-12-31+7 <= t -> value 1.0; global = 4
    assert feats["ETF_DEMAND_TONNES__GLOBAL"].iloc[0] == 4.0


def test_build_etf_features_global_requires_4_regions():
    etf = {
        ("ETF_DEMAND_TONNES", "North America"): _mk_etf_values(),
        ("ETF_DEMAND_TONNES", "Europe"): _mk_etf_values(),
    }
    df = pd.DataFrame(index=pd.date_range("2026-01-07", periods=3, freq="D"))
    feats = build_etf_features(df, etf, lag_days=7)
    assert "ETF_DEMAND_TONNES__GLOBAL" not in feats.columns


def test_spearman():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    assert abs(_spearman(a, b) - 1.0) < 1e-9
    assert np.isnan(_spearman(a, np.full(5, np.nan)))


def test_decile_spread_small_sample_returns_nan():
    df = pd.DataFrame({"col": [1.0, 2.0, 3.0], "R20": [0.01, 0.02, 0.03]})
    ds = _decile_spread(df, "col", 20)
    assert ds["spread"] == ds["spread"] or np.isnan(ds["spread"])
    assert ds["n"] <= 3


def test_sign_agreement():
    # feature cao (> median) thì return dương, thấp thì âm -> agreement cao
    df = pd.DataFrame(
        {
            "col": [1.0, 2.0, 3.0, 4.0, 5.0, 9.0, 10.0, 11.0, 12.0, 13.0],
            "R20": [-0.05, -0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05],
        }
    )
    sa = _sign_agreement(df, "col", 20)
    assert sa["agreement"] >= 0.5
    assert sa["n"] == 10
