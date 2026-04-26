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
    copper_price: float = Field(..., description="Giá đồng (LME/COMEX)")
    dxy_index: float = Field(..., description="Chỉ số sức mạnh đồng USD")
    interbank_rate: float = Field(..., description="Lãi suất liên ngân hàng O/N")
    sbv_action: str = Field(..., description="Trạng thái OMO (Bơm/Hút)")
    risk_level: str = Field(..., description="Mức độ rủi ro (Emerald/Amber/Red)")

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
