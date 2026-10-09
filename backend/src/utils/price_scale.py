"""price_scale.py — chuẩn hoá thang giá provider ⇄ store (VND).

BỐI CẢNH (vì sao có file này)
-----------------------------
`vci.history()` trả giá theo **NGHÌN đồng** (20.15), còn `daily_ohlcv` lưu theo
**ĐỒNG** (20,150). So sánh trực tiếp ⇒ lệch 1000× GIẢ, không phải lỗi dữ liệu.

Lỗi này đã lặp **2 lần** (phiên 05/10 và 08/10) trong script kiểm tra viết tay,
mỗi lần đều bị bắt bằng mắt. Từ nay mọi so sánh giá phải đi qua ĐÚNG MỘT hàm ở đây,
thay vì `ref * 1000` rải rác trong từng script.

HỢP ĐỒNG
--------
- `unit` là THAM SỐ TƯỜNG MINH; suy đoán theo ngưỡng chỉ là fallback.
- Ngưỡng `< 1000 ⇒ nghìn đồng` có thể SAI với cổ phiếu penny thật (giá 900đ).
  Khi biết đơn vị, PHẢI truyền `unit` — đừng để heuristics quyết định.
"""

from __future__ import annotations

VND_PER_THOUSAND = 1000.0
# Giá dưới ngưỡng này gần như chắc chắn là đơn vị nghìn đồng (mọi mã HOSE/HNX
# niêm yết đều > 1.000đ, trừ penny/UPCOM hiếm).
HEURISTIC_THRESHOLD = 1000.0

UNIT_VND = "vnd"
UNIT_THOUSAND_VND = "thousand_vnd"


def to_vnd(value: float | None, unit: str | None = None) -> float | None:
    """Quy giá về ĐỒNG.

    unit=UNIT_VND / UNIT_THOUSAND_VND ⇒ quy đổi tường minh.
    unit=None ⇒ suy đoán theo ngưỡng (fallback — xem cảnh báo ở docstring module).
    """
    if value is None:
        return None
    v = float(value)
    if unit == UNIT_VND:
        return v
    if unit == UNIT_THOUSAND_VND:
        return v * VND_PER_THOUSAND
    return v * VND_PER_THOUSAND if v < HEURISTIC_THRESHOLD else v


def to_thousand_vnd(value: float | None, unit: str | None = None) -> float | None:
    """Quy giá về NGHÌN đồng (đơn vị của provider)."""
    v = to_vnd(value, unit)
    return None if v is None else v / VND_PER_THOUSAND


def normalize_provider_price(value: float | None, source: str = "vci") -> float | None:
    """Chuẩn hoá giá provider về ĐỒNG.

    `source` chỉ để ghi rõ nguồn; hiện mọi provider đang dùng chung thang nghìn đồng.
    """
    _ = source
    return to_vnd(value, unit=None)


def prices_match(
    a: float | None,
    b: float | None,
    rel_tol: float = 1e-6,
    abs_tol: float = 1.0,
) -> bool:
    """So sánh 2 giá ĐÃ CÙNG ĐƠN VỊ (VND).

    Dung sai: tương đối 1e-6 HOẶC tuyệt đối 1đ (giá VN bước nhảy tối thiểu 10đ
    với giá > 10.000đ, nhưng dữ liệu provider có thể lệch làm tròn 1đ).
    """
    if a is None or b is None:
        return False
    a, b = float(a), float(b)
    return abs(a - b) <= max(abs_tol, abs(b) * rel_tol)


def compare_with_store(
    store_price: float | None,
    provider_price: float | None,
    provider_unit: str | None = None,
) -> tuple[bool, float | None]:
    """So giá store (VND) với giá provider (tự chuẩn hoá).

    Trả về ``(khớp?, giá_provider_đã_quy_về_VND)``.
    """
    ref_vnd = to_vnd(provider_price, unit=provider_unit)
    return prices_match(store_price, ref_vnd), ref_vnd


__all__ = [
    "VND_PER_THOUSAND",
    "HEURISTIC_THRESHOLD",
    "UNIT_VND",
    "UNIT_THOUSAND_VND",
    "to_vnd",
    "to_thousand_vnd",
    "normalize_provider_price",
    "prices_match",
    "compare_with_store",
]
