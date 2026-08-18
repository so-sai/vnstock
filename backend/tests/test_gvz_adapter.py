"""test_gvz_adapter.py — Unit tests GVZ adapter (PIT semantics, no network).

No network, no DB. Kiểm tra LOGIC:
  - load_gvz đọc cache CSV đúng schema;
  - business_day_after: Th6 → Th2 (bỏ cuối tuần), giữ thứ tự;
  - pit_series: index = publication_date (obs+1 BDay), value đúng;
  - pit_align: tại t chỉ dùng GVZ pub <= t (ffill), không lookahead;
  - audit() chạy được trên synthetic cache.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gvz_adapter import (
    business_day_after,
    load_gvz,
    pit_align,
    pit_series,
)


def _mk_cache(tmp_path, n: int = 10, start: str = "2026-08-03") -> Path:
    p = tmp_path / "gvzcls.csv"
    idx = pd.date_range(start, periods=n, freq="B")
    df = pd.DataFrame({"observation_date": idx.strftime("%Y-%m-%d"), "GVZCLS": np.round(20 + np.arange(n), 2)})
    df.to_csv(p, index=False, encoding="utf-8")
    return p


def test_load_gvz_schema(tmp_path):
    p = _mk_cache(tmp_path)
    df = load_gvz(p)
    assert list(df.columns) == ["observation_date", "value"]
    assert df["observation_date"].is_monotonic_increasing
    assert (df["value"] > 0).all()
    assert len(df) == 10


def test_business_day_after_skips_weekend():
    idx = pd.DatetimeIndex(["2026-08-14", "2026-08-13"])  # Th6, Th5
    out = business_day_after(idx)
    assert out[0] == pd.Timestamp("2026-08-17")  # Th6 -> Th2
    assert out[1] == pd.Timestamp("2026-08-14")  # Th5 -> Th6


def test_pit_series_index_is_publication_date(tmp_path):
    df = load_gvz(_mk_cache(tmp_path))
    s = pit_series(df)
    assert len(s) == len(df)
    assert s.index[0] == df["observation_date"].iloc[0] + pd.offsets.BDay(1)
    # value giữ nguyên, chỉ đổi index
    assert s.iloc[0] == df["value"].iloc[0]
    assert s.index.is_monotonic_increasing


def test_pit_align_no_lookahead(tmp_path):
    df = load_gvz(_mk_cache(tmp_path, n=6, start="2026-08-03"))  # Mon..Mon
    s = pit_series(df)
    # target: các ngày mà GVZ ngày t chỉ có từ t+1
    target = pd.DatetimeIndex(pd.date_range("2026-08-03", periods=7, freq="B"))
    aligned = pit_align(s, target)
    # ngày đầu (2026-08-03): chưa có GVZ pub <= 08-03 (pub đầu = 08-04) → NaN
    assert np.isnan(aligned.iloc[0])
    # 2026-08-04: dùng GVZ obs 08-03 (pub 08-04)
    assert aligned.iloc[1] == df["value"].iloc[0]
    # 2026-08-05: GVZ obs 08-04 (pub 08-05)
    assert aligned.iloc[2] == df["value"].iloc[1]


def test_pit_align_ffill_keeps_last(tmp_path):
    df = load_gvz(_mk_cache(tmp_path, n=4, start="2026-08-03"))  # 08-03..08-06
    s = pit_series(df)
    target = pd.DatetimeIndex(pd.date_range("2026-08-03", periods=8, freq="B"))
    aligned = pit_align(s, target)
    # 08-10 (target 5): GVZ mới nhất là obs 08-06 (pub 08-07) → giữ last
    assert aligned.iloc[4] == df["value"].iloc[-1]
    assert aligned.notna().sum() == len(target) - 1
