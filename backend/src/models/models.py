from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime

# Sentinel v2.1 (Anchor Fix)
# (Added automatically if needed, but since this is a model file we keep it clean or add if it's executed)

# Cấu hình chung: Tự động hiểu camelCase từ Frontend gửi lên 
# và trả về camelCase cho Frontend dễ đọc.
class AlphaBaseModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=lambda s: "".join(
            word.capitalize() if i > 0 else word 
            for i, word in enumerate(s.split("_"))
        )
    )

# 1. Tầng Vĩ mô (The Macro Nexus)
class MacroStatus(AlphaBaseModel):
    usd_cnh: float = Field(..., description="Tỷ giá Nhân dân tệ hải ngoại")
    usd_cny: float = Field(..., description="Tỷ giá Nhân dân tệ trên bờ")
    copper_price: float = Field(..., description="Giá đồng (LME/COMEX)")
    dxy_index: float = Field(..., description="Chỉ số sức mạnh đồng USD")
    interbank_rate: Optional[float] = Field(None, description="Lãi suất liên ngân hàng O/N (NO_DATA nếu chưa seed)")
    sbv_action: str = Field(..., description="Trạng thái SBV (UNKNOWN nếu chưa seed)")
    risk_level: str = Field(..., description="Mức độ rủi ro (Emerald/Amber/Red)")
    adx: Optional[float] = Field(None, description="Chỉ số ADX")
    atr_ratio: Optional[float] = Field(None, description="Tỷ số ATR")
    vgb10y: Optional[float] = Field(None, description="Lợi suất TPCP VN 10 năm (ESTIMATED nếu từ US10Y)")
    vgb10y_data_quality: Optional[str] = Field(None, description="REAL / ESTIMATED / NO_DATA")
    vgb10y_bps_change: Optional[str] = Field(None, description="Độ thay đổi bps của VGB10Y (None nếu NO_DATA)")
    vgb10y_status_label: Optional[str] = Field(None, description="Nhãn trạng thái VGB10Y (NO_DATA/ESTIMATED)")
    vgb10y_raw_bps: Optional[int] = Field(None, description="Độ thay đổi bps thô")
    us2y_yield: Optional[float] = Field(None, description="Lợi suất TPCP Mỹ kỳ hạn 2 năm")
    us5y_yield: Optional[float] = Field(None, description="Lợi suất TPCP Mỹ kỳ hạn 5 năm")
    us30y_yield: Optional[float] = Field(None, description="Lợi suất TPCP Mỹ kỳ hạn 30 năm")
    spread_10y2y: Optional[float] = Field(None, description="Chênh lệch 10Y-2Y")
    spread_30y10y: Optional[float] = Field(None, description="Chênh lệch 30Y-10Y")
    yield_curve_inversion: Optional[str] = Field(None, description="Trạng thái đường cong (NORMAL/FLAT/INVERTED/NO_DATA)")
    tip_price: Optional[float] = Field(None, description="Giá TIP ETF (iShares TIPS Bond)")
    us_real_yield: Optional[float] = Field(None, description="Lợi suất thực US 10Y (TIPS trailing dividend yield)")
    breakeven_inflation: Optional[float] = Field(None, description="Lạm phát kỳ vọng 10Y (US10Y - US_REAL_YIELD)")

# 2. Tầng Độ rộng thị trường (Market Breadth)
class MarketBreadth(AlphaBaseModel):
    health_score_ma20: float = Field(..., description="% Diamond > MA20")
    health_score_ma50: float = Field(..., description="% Diamond > MA50")
    trend_status: str = Field(..., description="Bullish/Bearish/Neutral")
    updated_at: datetime

# 3. Tầng Thực thi (The Diamond Sniper)
class DiamondCandidate(AlphaBaseModel):
    symbol: str
    price: float
    change_p: float = Field(..., alias="changePercent")
    return_6m: float = Field(..., description="$Return_{6M}$")
    signal_v1: str = Field(..., description="Breakout/VolSpike/None")
    volume_ratio: float = Field(..., description="Vol/MA20 ratio")

# 4. Giao diện Phản hồi Tổng lực (Command Center Dashboard)
class DashboardResponse(AlphaBaseModel):
    macro: MacroStatus
    breadth: MarketBreadth
    top_leaders: List[DiamondCandidate]
    shadow_cash_percent: float = Field(default=100.0)
    system_message: str
