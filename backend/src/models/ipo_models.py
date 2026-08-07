"""
IPO SIGNAL MODELS - Định nghĩa Pydantic (Mô hình dữ liệu IPO)
================================================================================

Các data class cho tích hợp IPO signal vào API Frontend & Decision Engine.
Tuân theo naming convention camelCase (alias generator) của AlphaBaseModel.
"""

from datetime import datetime
from enum import Enum

# Giả định import từ models.py hiện tại
# (Thực tế cần thêm vào backend/src/models/models.py)


class IpoSignalEnum(str, Enum):
    """Enum cho tín hiệu IPO"""

    XANH = "XANH"
    VANG = "VANG"
    DO = "DO"


class IpoSignalResponse:
    """
    Tín hiệu IPO gửi về frontend

    Cấu trúc giản lược (Actionable Intelligence):
      - traffic_light: 🟢/🟡/🔴
      - ipo_intensity: Cường độ IPO (CAO/TRUNG_BINH/THAP)
      - capital_absorption: Áp lực hút tiền (TANG_MANH/ON_DINH/GIAM)
      - midcap_pressure: Áp lực lên Midcap (0-100)
      - regime: Chu kỳ thị trường (DONG_TIEN_MO_RONG/TANG_GIAN/THOAI_LUI)
      - active_ipos: Danh sách IPO đang "nóng"
    """

    signal_date: datetime
    traffic_light: IpoSignalEnum  # 🟢/🟡/🔴

    ipo_intensity: str  # CAO / TRUNG_BINH / THAP
    capital_absorption_trend: str  # TANG_MANH / ON_DINH / GIAM
    secondary_market_pressure: float  # 0-100

    narrative_heat: str  # BAT_THUONG / BINH_THUONG / THAP
    rotation_risk: str  # CAO / TRUNG_BINH / THAP
    midcap_smallcap_pressure: float  # 0-100

    liquidity_regime: str  # DONG_TIEN_MO_RONG / TANG_GIAN / THOAI_LUI
    regime_confidence: float  # 0-1.0

    active_ipos: list[dict[str, str]]  # [{symbol, sector, days_listed}]

    interpretation: str  # Giải thích bằng tiếng Việt cho người dùng


class IpoCalendarEntry:
    """Mục lục IPO - Dùng cho danh sách các IPO sắp tới / gần đây"""

    symbol: str
    listing_date: datetime
    listing_price: float  # VND
    listing_volume: int  # shares
    market_cap_listing: float  # VND tỷ
    sector: str  # ICB
    exchange: str  # HOSE / HNX / UPCOM
    aftermarket_return_pct: float | None  # Nếu đã lên sàn
    days_listed: int  # Số ngày từ listing date đến hôm nay


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────


def generate_ipo_interpretation(
    traffic_light: IpoSignalEnum,
    ipo_intensity: str,
    capital_absorption: str,
    rotation_risk: str,
    regime: str,
) -> str:
    """
    Tạo câu giải thích bằng tiếng Việt cho người dùng hành động

    Returns:
        str: Khẩu lệnh thực chiến (1-2 câu, rõ ràng, ngắn gọn)
    """

    if traffic_light == IpoSignalEnum.DO:
        if rotation_risk == "CAO":
            return (
                "🔴 CẢNH BÁO: IPO nóng + Midcap/Smallcap bị ép bán mạnh. "
                "Kích hoạt phòng thủ, tránh nhóm chứng chỉ nhỏ vốn hóa."
            )
        elif capital_absorption == "TANG_MANH":
            return "🔴 CẢNH BÁO: Tiền bị hút mạnh để nộp IPO. Thanh khoản sàn bị ảnh hưởng, khéo thận trọng."
        else:
            return "🔴 CẢNH BÁO: Các dấu hiệu tiêu cực kết hợp. Chuyển sang phòng thủ, hạ vị thế."

    elif traffic_light == IpoSignalEnum.VANG:
        if regime == "TANG_GIAN":
            return "🟡 THẬN TRỌNG: IPO thành công nhưng tiền bắt đầu khó kiếm. Đóng margin, tránh speculative trades."
        else:
            return "🟡 THẬN TRỌNG: Áp lực vừa phải từ IPO. Tiếp tục đánh nhưng giám sát sát sao."

    else:  # XANH
        if ipo_intensity == "CAO" and regime == "DONG_TIEN_MO_RONG":
            return "🟢 AN TOÀN: IPO mạnh + breadth khỏe + ngoại mua. Thị trường mở rộng thực, tiếp tục phát huy lợi thế."
        else:
            return "🟢 BÌNH THƯỜNG: Không có cảnh báo đặc biệt từ IPO. Tiếp tục theo kế hoạch."


__all__ = [
    "IpoSignalEnum",
    "IpoSignalResponse",
    "IpoCalendarEntry",
    "generate_ipo_interpretation",
]
