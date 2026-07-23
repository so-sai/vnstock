from typing import Literal
from pydantic import BaseModel, Field

# ───────────────────────────────
#  Canonical color palette (VN market semantics 2026)
# ───────────────────────────────

HEX_GREEN = "#16C784"
HEX_YELLOW = "#F5C542"
HEX_ORANGE = "#F2994A"
HEX_RED = "#EB5757"
HEX_GRAY = "#4F4F4F"
HEX_WHITE = "#FFFFFF"

ColorToken = Literal["GREEN", "YELLOW", "ORANGE", "RED", "GRAY", "WHITE"]

COLOR_MAP: dict[ColorToken, str] = {
    "GREEN": HEX_GREEN,
    "YELLOW": HEX_YELLOW,
    "ORANGE": HEX_ORANGE,
    "RED": HEX_RED,
    "GRAY": HEX_GRAY,
    "WHITE": HEX_WHITE,
}

COLOR_LABELS_VI: dict[ColorToken, str] = {
    "GREEN": "Dòng tiền vào mạnh – có thể tăng tỷ trọng",
    "YELLOW": "Thị trường đang tích lũy – quan sát",
    "ORANGE": "Tín hiệu mâu thuẫn – giảm kích thước vị thế",
    "RED": "Ưu tiên bảo toàn vốn",
    "GRAY": "Chưa đủ tín hiệu",
    "WHITE": "",
}


class ColorDecision(BaseModel):
    token: ColorToken
    hex: str
    label_vi: str
    reason: str = Field(description="Short explanation of why this color was chosen")


def resolve_color_from_decision(
    entropy_state: str = "HỘI_TỤ",
    confidence: float = 0.5,
    risk_level: str = "THẤP",
    recommended_posture: str = "GIỮ_VỊ_THẾ",
    signal_alignment: float = 0.5,
) -> ColorDecision:
    if entropy_state == "MẤT_ỔN_ĐỊNH":
        return ColorDecision(token="RED", hex=HEX_RED, label_vi=COLOR_LABELS_VI["RED"],
                             reason="Mất ổn định nhận thức → ưu tiên bảo toàn vốn")
    if confidence < 0.4:
        return ColorDecision(token="GRAY", hex=HEX_GRAY, label_vi=COLOR_LABELS_VI["GRAY"],
                             reason=f"Confidence={confidence:.2f} dưới ngưỡng 0.4 → chưa đủ tín hiệu")
    if risk_level == "CAO":
        return ColorDecision(token="RED", hex=HEX_RED, label_vi=COLOR_LABELS_VI["RED"],
                             reason="Rủi ro cao → phòng thủ")
    if recommended_posture == "TĂNG_TỶ_TRỌNG":
        return ColorDecision(token="GREEN", hex=HEX_GREEN, label_vi=COLOR_LABELS_VI["GREEN"],
                             reason="Posture tăng tỷ trọng → dòng tiền mở rộng an toàn")
    if signal_alignment < 0.4:
        return ColorDecision(token="ORANGE", hex=HEX_ORANGE, label_vi=COLOR_LABELS_VI["ORANGE"],
                             reason=f"Signal alignment={signal_alignment:.2f} < 0.4 → mất đồng thuận")

    return ColorDecision(token="YELLOW", hex=HEX_YELLOW, label_vi=COLOR_LABELS_VI["YELLOW"],
                         reason="Trạng thái trung tính → tích lũy / quan sát")
