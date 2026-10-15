"""Regression test — Ca 5: truy vấn OHLCV trong `breakout_continuation` phải đi qua lưới hợp lệ.

[WHY - BỐI CẢNH & TÁC HẠI]
    Chuỗi sự kiện của phiên 2026-10-15: `get_breakout_market_context()` chết vì
    `OverflowError: cannot convert float infinity to integer`. Truy ngược thấy `mom_5d = inf`,
    và `inf` chỉ xuất hiện khi `close` của phiên i-5 bằng 0.
    Ca 4 xác nhận: 36.971 dòng `close <= 0` trong `daily_ohlcv` — và **100 % đã bị cách ly**
    (`is_stale=1`), **0 dòng mang nhãn sạch**. Vậy `inf` không thể đến từ dữ liệu sạch.
    Nguyên nhân thật: hai truy vấn của module gõ raw SQL `FROM daily_ohlcv` mà KHÔNG có
    `is_stale`/`VALID_ROW_SQL` ⇒ tự tay nuốt trọn dòng đã cách ly vào engine.

[ROOT CAUSE - NGUYÊN NHÂN GỐC]
    Lưới cách ly (quarantine) chỉ có tác dụng nếu MỌI đường đọc đều kiểm nó. Một truy vấn
    quên vị từ hợp lệ sẽ biến `is_stale=1` thành vô nghĩa — dữ liệu bị cách ly vẫn vào thẳng
    bộ nhớ. Sửa triệu chứng (`int(inf)`) không sửa được đường đọc.

[INVARIANT - BẤT BIẾN BẢO VỆ]
    Mọi truy vấn OHLCV phải mang `is_stale = 0 AND VALID_ROW_SQL` (quy ước đã có ở
    `src/backtest/trend_features.py:131` và `src/analysis/market_wide_breadth.py:75`).
    Dòng bị cách ly KHÔNG BAO GIỜ được xuất hiện trong kết quả.
"""

from __future__ import annotations

import contextlib
import math
import sqlite3

import pandas as pd

import src.engine.breakout_continuation as bc  # type: ignore[import-not-found]

N = 60
_HOME = "2026-10-14"  # neo sát hôm nay: truy vấn có mệnh đề `date >= date('now','-200 days')`


def _make_db(with_quarantined_zero: bool) -> sqlite3.Connection:
    """DB tạm có đúng cột truy vấn dùng; dòng rác mang `is_stale = 1`."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL, "
        "high REAL, low REAL, is_stale INTEGER)"
    )
    dates = pd.date_range(end=_HOME, periods=N, freq="D").strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(dates):
        if with_quarantined_zero and i == N - 6:
            # close = 0 tại phiên i-5 — ĐÚNG con số đã sinh ra `inf` trong sản xuất,
            # nhưng ở đây nó ĐÃ BỊ CÁCH LY (is_stale = 1).
            rows.append(("ZZZ", d, 0.0, 1_000_000.0, 0.0, 0.0, 1))
        else:
            px = 10.0 + i * 0.01
            rows.append(("ZZZ", d, px, 1_000_000.0, px * 1.01, px * 0.99, 0))
    conn.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def _score_with(monkeypatch, conn: sqlite3.Connection) -> dict:
    monkeypatch.setattr(bc, "get_connection", lambda: contextlib.nullcontext(conn))
    return bc.compute_breakout_score("ZZZ")  # không truyền df ⇒ đi qua ĐƯỜNG SQL


def test_quarantined_zero_close_never_reaches_the_engine(monkeypatch):
    """ĐIỀU KIỆN CHÍNH: dòng `close=0` đã cách ly ⇒ không được lọt vào ⇒ không có `inf`."""
    res = _score_with(monkeypatch, _make_db(with_quarantined_zero=True))
    assert math.isfinite(res["mom_5d_pct"]), (
        f"RÒ RỈ QUARANTINE: mom_5d_pct={res['mom_5d_pct']!r} — dòng `is_stale=1` đã lọt vào "
        "truy vấn. Đây chính là nguồn sinh `inf` và làm chết engine."
    )
    assert math.isfinite(res["breakout_score"])


def test_clean_series_still_scores(monkeypatch):
    """CHỐNG VÁ QUÁ TAY: chuỗi sạch vẫn phải được chấm điểm bình thường."""
    res = _score_with(monkeypatch, _make_db(with_quarantined_zero=False))
    assert res.get("status") != "INSUFFICIENT_DATA"
    assert math.isfinite(res["mom_5d_pct"])
