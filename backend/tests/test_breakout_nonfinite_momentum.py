"""Regression test — Ca 1b: `int(mom_5d)` ném OverflowError khi momentum = ±inf.

TRUY GỐC: `mom_5d = df_stock["return_5d"].iloc[-1] * 100` với
`return_5d = close.pct_change(5)` ⇒ **inf sinh ra khi giá đóng cửa 5 phiên trước bằng 0**
⇒ `int(inf)` ⇒ `OverflowError` ⇒ chết cả hàm bối cảnh breakout.

⇒ Bug này KHÔNG chỉ là lỗi ép kiểu: nó là TRIỆU CHỨNG của một phiên có `close = 0` trong
   `daily_ohlcv` — đúng loại bẩn dữ liệu mà `data_validity.VALID_ROW_SQL` sinh ra để chặn.
   Bản vá dưới đây chặn hậu quả (không chết hàm) và GHI LOG để còn truy nguồn phiên bẩn.

CA 2 CỦA TEST (NaN): `pd.isna` đã chặn từ trước — bảo vệ để bản vá không phá hành vi cũ.
"""

from __future__ import annotations

import math

import pandas as pd

import src.engine.breakout_continuation as bc  # type: ignore[import-not-found]

N = 60


def _df(closes: list[float]) -> pd.DataFrame:
    """Khung dữ liệu tối thiểu đúng các cột `compute_breakout_score` cần."""
    return pd.DataFrame(
        {
            "symbol": ["ZZZ"] * len(closes),
            "date": pd.date_range("2026-01-01", periods=len(closes), freq="D").strftime("%Y-%m-%d"),
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "high": [c * 1.01 if c == c else c for c in closes],
            "low": [c * 0.99 if c == c else c for c in closes],
        }
    )


def test_zero_close_five_sessions_back_does_not_crash():
    """ĐÚNG ĐƯỜNG SẢN XUẤT: close[i-5] = 0 ⇒ pct_change(5) = inf ⇒ KHÔNG được ném lỗi."""
    closes = [10.0] * N
    closes[-(5 + 1)] = 0.0  # 5 phiên trước giá đóng cửa = 0 ⇒ pct_change(5) = +inf

    res = bc.compute_breakout_score("ZZZ", _df(closes))

    assert res["mom_5d_pct"] == 0, f"momentum không hữu hạn phải quy về 0, nhận {res['mom_5d_pct']!r}"
    assert math.isfinite(res["breakout_score"]), "điểm phải hữu hạn"


def test_nan_close_does_not_crash():
    """NaN ở phiên cuối: hành vi cũ (`pd.isna`) phải được giữ nguyên."""
    closes = [10.0] * (N - 1) + [float("nan")]
    res = bc.compute_breakout_score("ZZZ", _df(closes))
    assert res["mom_5d_pct"] == 0
    assert math.isfinite(res["breakout_score"])


def test_normal_momentum_still_scored():
    """CHỐNG VÁ QUÁ TAY: momentum bình thường vẫn phải được tính như cũ."""
    closes = [10.0] * (N - 5) + [10.0, 10.2, 10.4, 10.6, 11.0]  # +10 % trong 5 phiên
    res = bc.compute_breakout_score("ZZZ", _df(closes))
    assert res["mom_5d_pct"] > 0, "momentum dương bình thường không được bị quy về 0"
