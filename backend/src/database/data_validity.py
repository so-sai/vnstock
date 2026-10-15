"""MỘT nguồn sự thật cho TÍNH HỢP LỆ của một dòng OHLC.

Cổng độ tươi hiện tại chỉ đếm SỐ DÒNG (`is_stale = 0`), mà đếm dòng không chứng minh
được giá trị đúng: một lần backfill lỗi ghi `close = 0` cho 1.008 mã vẫn thoả mãn phép
đếm, và vì momentum chia cho giá gần 0 nên các mã rác đó nổi lên ĐẦU bảng xếp hạng RS
chứ không phải đáy — nơi mà kiểm tra nhanh vài mã vốn liếng sẽ không bao giờ thấy.

Định nghĩa hợp lệ (chủ hệ thống chốt 2026-10-09):

    hợp_lệ  ⟺  (close > 0) ∧ (low ≤ close ≤ high) ∧ (volume ≥ 0)

Mọi giá trị NULL được coi là KHÔNG hợp lệ (missing > fake): một dòng không có giá thì
không được phép mang `is_stale = 0`.

Module này cố ý chỉ chứa VỊ TỪ, không chứa quyết định: nhờ vậy mọi tầng (ingest, độ
tươi, cách ly, backtest) dùng đúng một định nghĩa thay vì mỗi nơi tự viết một biến thể.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Vị từ SQL (dùng cho WHERE) ──
VALID_ROW_SQL = (
    "(close IS NOT NULL AND close > 0 "
    "AND low IS NOT NULL AND low <= close "
    "AND high IS NOT NULL AND close <= high "
    "AND volume IS NOT NULL AND volume >= 0)"
)


# ── Chỉ số ≠ cổ phiếu: mọi phép toán "toàn thị trường" phải loại chúng ra ──
# WHY: volume của VNINDEX ngày 2026-08-28 = 643.935.725 cp — lớn hơn CẢ rổ cổ phiếu cộng lại,
# nên một `SUM(volume)` quên lọc chỉ số sẽ sai gần gấp đôi (1.283.355.625 vs 639.419.900 đo thật).
# Ngoài ra breadth đếm chỉ số như một "cổ phiếu" ⇒ thêm ~4 vào mẫu số và 4 phiên tăng/giảm giả mỗi ngày.
INDEX_SYMBOLS: tuple[str, ...] = ("VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX", "UPINDEX")

# Vị từ SQL cho "chỉ cổ phiếu" — dùng kèm tham số hoá tên bảng/bí danh khi cần.
STOCK_ONLY_SQL = "symbol NOT IN (" + ", ".join(f"'{s}'" for s in INDEX_SYMBOLS) + ")"


def is_stock(symbol: str) -> bool:
    """False nếu `symbol` là chỉ số (không phải cổ phiếu)."""
    return symbol.upper() not in INDEX_SYMBOLS


INVALID_ROW_SQL = f"NOT {VALID_ROW_SQL}"

# ── Nhãn cách ly theo loại vi phạm — phân biệt được nguyên nhân khi truy vết ──
TAG_ZERO_PRICE = "poisoned:kbs_zero"
TAG_INVALID_OHLC = "poisoned:invalid_ohlc"
TAG_EMPTY_ROW = "poisoned:empty_row"


@dataclass(frozen=True)
class RowValidity:
    valid: bool
    reason: str | None = None
    tag: str | None = None


def check_row(*, close: float | None, low: float | None, high: float | None, volume: float | None) -> RowValidity:
    """Kiểm một dòng trong Python (dùng cho ingest / test), cùng định nghĩa với SQL."""
    if close is None or low is None or high is None or volume is None:
        return RowValidity(False, "thiếu trường bắt buộc (NULL)", TAG_INVALID_OHLC)
    if close <= 0:
        return RowValidity(False, f"close = {close} ≤ 0", TAG_ZERO_PRICE)
    if low > close:
        return RowValidity(False, f"low {low} > close {close}", TAG_INVALID_OHLC)
    if close > high:
        return RowValidity(False, f"close {close} > high {high}", TAG_INVALID_OHLC)
    if volume < 0:
        return RowValidity(False, f"volume {volume} < 0", TAG_INVALID_OHLC)
    return RowValidity(True)


def quarantine_condition(expr: str) -> str:
    """Điều kiện cách ly cho một biểu thức SQL (vd 'close <= 0').

    Chỉ nhắm dòng đang được coi là SẠCH — không đụng dòng đã cách ly, để chạy lại
    nhiều lần không nhân đôi nhãn (idempotent).
    """
    return f"({expr}) AND (is_stale = 0 OR is_stale IS NULL)"
