"""fixture hiệu chuẩn cho `scan_timeseries_hazards.py` — 10 ca CÓ NHÃN BIẾT TRƯỚC.

VÌ SAO CẦN FILE NÀY
-------------------
5 lỗi thật của phiên 2026-10 đều **đã được vá** nên không còn trong cây code — chạy bộ quét
lên code hiện tại sẽ **không** đo được gì về 5 ca đó. Muốn biết bộ quét bắt trúng bao nhiêu
(Recall) và báo nhầm bao nhiêu (False Positive), phải có **mẫu có nhãn** để đối chiếu.

Bài học đã trả giá: bộ guard rescale vci đời đầu báo 45.042 dòng, hoá ra TOÀN BỘ đều đúng
đơn vị. Một bộ quét chưa đo độ chính xác thì chưa phải bằng chứng.

10 CA (5 DƯƠNG THẬT + 5 ÂM THẬT) — chủ đích viết để MÁY đọc, người không chạy file này.
"""

from __future__ import annotations

from typing import Any

# ─────────────── 5 CA DƯƠNG THẬT (bộ quét PHẢI bắt) ───────────────


def pos_1_negative_index_no_guard(bench: dict[str, float], days: list[str], i: int) -> float:
    """P1: `days[i - 3]` không có guard ⇒ i<3 sẽ lấy 3 phần tử CUỐI mảng (tương lai)."""
    a = bench.get(days[i])
    b = bench.get(days[i - 3])
    return (a or 0.0) / (b or 1.0)


def pos_2_negative_index_offset_two(series: list[float], idx: int) -> float:
    """P2: cùng lỗi, độ lệch 2, và guard nằm ở HÀM KHÁC nên không che được hàm này."""
    return series[idx - 2]


def pos_3_fail_open_regime(regime: dict[str, str], target_date: str) -> str:
    """P3: thiếu dòng regime ⇒ trả 'RANGING' = đường thoáng ⇒ phanh CRISIS không kích."""
    return regime.get(target_date, "RANGING")


def pos_4_fail_open_price(price_map: dict[str, float], symbol: str) -> float:
    """P4: thiếu giá ⇒ trả 0.0 ⇒ đọc thành 'giá bằng không' thay vì 'không biết'."""
    return price_map.get(symbol, 0.0)


def pos_5_fail_open_foreign_flow(flow: dict[str, float], d: str) -> float:
    """P5: thiếu dòng tiền ngoại ⇒ trả 0.0 ⇒ đọc thành 'không có dòng tiền'."""
    return flow.get(d, 0.0)


# ─────────────── 5 CA ÂM THẬT (bộ quét KHÔNG được bắt) ───────────────


def neg_1_negative_index_with_guard(series: list[float], i: int) -> float:
    """N1: CÙNG `series[i-3]` nhưng CÓ guard ngay trong hàm ⇒ an toàn."""
    if i < 3:
        return 0.0
    return series[i - 3]


def neg_2_negative_index_with_guard_le(series: list[float], idx: int) -> float:
    """N2: guard dạng `<=` — vẫn là kiểm biên hợp lệ."""
    if idx <= 2:
        return 0.0
    return series[idx - 2]


def neg_3_config_default_is_not_market_data(cfg: dict[str, Any]) -> float:
    """N3: `.get` trên THAM SỐ cấu hình ⇒ mặc định là hợp lệ, không phải fail-open."""
    return cfg.get("lookback_sessions", 20)


def neg_4_required_get_without_default(close: dict[str, float], symbol: str) -> float:
    """N4: `.get` MỘT tham số ⇒ thiếu khoá là KeyError = fail-CLOSED, hợp lệ."""
    return close.get(symbol)  # type: ignore[return-value]


def neg_5_explicit_unknown_handling(price_map: dict[str, float], symbol: str) -> float | None:
    """N5: không mặc định — trả None và để caller xử lý 'không biết' một cách tường minh."""
    return price_map.get(symbol)
