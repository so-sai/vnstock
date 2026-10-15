"""Regression test — Ca 1: `get_breakout_market_context` phải FAIL-CLOSED khi thiếu regime.

LỖI THẬT (bộ quét `scan_timeseries_hazards.py` phát hiện, `breakout_continuation.py:178`):

    regime_status = regime.get("status", "RANGING")

Thiếu dữ liệu regime ⇒ gán nhãn **"RANGING"** (đường thông hè thoáng) ⇒ tầng trên tưởng
thị trường bình thường và **cho qua tín hiệu breakout giữa lúc CRISIS**.

ĐÂY CHÍNH LÀ CÙNG MỘT LỚP LỖI đã lật ngược kết quả OOS 2026 (`C1 +0,8956` → `−0,7839`):
`regime.get(T, "RANGING")` biến "KHÔNG BIẾT" thành "ĐƯỜNG THOÁNG". Khác chỗ lần này nó nằm
trên **engine sống**, không phải backtest.

Signature thật của hàm: `get_breakout_market_context() -> dict` (KHÔNG tham số).
Hình dạng trả về khi thiếu dữ liệu universe đã có tiền lệ trong chính hàm:
    {"regime": regime_status, "status": "NO_DATA"}
⇒ bản vá theo đúng quy ước đó, chỉ khác: KHÔNG bịa ra một trạng thái thị trường lành.
"""

from __future__ import annotations

import src.engine.breakout_continuation as bc  # type: ignore[import-not-found]

BENIGN_LOOKING = {"RANGING", "TRENDING", "BULL", "NORMAL"}


def _assert_no_benign_regime(result: dict) -> None:
    regime = result.get("regime")
    assert regime not in BENIGN_LOOKING, (
        f"FAIL-OPEN: thiếu regime nhưng trả về trạng thái lành {regime!r} ⇒ tầng trên cho qua "
        f"tín hiệu breakout. Kết quả: {result!r}"
    )


def test_missing_regime_does_not_default_to_ranging(monkeypatch):
    """ĐIỀU KIỆN CHÍNH: `detect_regime()` trả {} ⇒ không được trả 'RANGING'."""
    monkeypatch.setattr(bc, "detect_regime", lambda: {})
    _assert_no_benign_regime(bc.get_breakout_market_context())


def test_regime_none_does_not_default_to_ranging(monkeypatch):
    """`detect_regime()` trả None ⇒ cũng không được mặc định là lành."""
    monkeypatch.setattr(bc, "detect_regime", lambda: None)
    _assert_no_benign_regime(bc.get_breakout_market_context())


def test_regime_status_none_does_not_default_to_ranging(monkeypatch):
    """Có dict nhưng `status` là None ⇒ vẫn là 'không biết', không phải 'lành'."""
    monkeypatch.setattr(bc, "detect_regime", lambda: {"status": None})
    _assert_no_benign_regime(bc.get_breakout_market_context())


def test_valid_regime_is_passed_through(monkeypatch):
    """CHỐNG VÁ QUÁ TAY: regime HỢP LỆ phải đi qua nguyên vẹn (không chặn oan).

    Chặn `_load_universe` để test CHỈ đo phần regime, không đi vào vòng chấm điểm — vòng đó
    có bug riêng (int(inf) ⇒ OverflowError tại dòng 125, xem Ca 1b).
    """
    import pandas as pd

    monkeypatch.setattr(bc, "detect_regime", lambda: {"status": "CRISIS"})
    monkeypatch.setattr(bc, "_load_universe", lambda: pd.DataFrame())
    assert bc.get_breakout_market_context()["regime"] == "CRISIS"
