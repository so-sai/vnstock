"""
HoldingsView — models for portfolio cognition.
Hoàn toàn orthogonal với OpportunityView.
OpportunityView trả lời "cái gì đáng mua".
HoldingsView trả lời "danh mục hiện tại nguy hiểm ở đâu".
"""

from typing import Literal

from pydantic import BaseModel, Field


class HoldingPosition(BaseModel):
    symbol: str
    quantity: float
    avg_price: float
    market_price: float
    market_value: float
    portfolio_weight: float
    sector: str
    pnl_pct: float
    liquidity_score: float | None = None


class SectorExposure(BaseModel):
    sector: str
    weight: float
    position_count: int
    liquidity_quality: float


class HoldingsView(BaseModel):
    portfolio_health_vi: str = Field(description="Overall portfolio health in Vietnamese")
    stress_level: Literal["THẤP", "TRUNG_BÌNH", "CAO", "NGUY_HIỂM"] = Field(default="THẤP")
    dominant_exposure_vi: str = Field(description="Primary risk exposure description")
    hidden_concentration_vi: str | None = Field(default=None, description="Hidden correlation risk not visible to the user")
    liquidity_fragility_vi: str = Field(description="How easy is it to exit positions under stress")
    regime_alignment_vi: str = Field(description="How aligned the portfolio is with current market regime")
    suggested_adjustment_vi: str = Field(description="Directional adjustment hint (NOT a buy/sell recommendation)")
    confidence_vi: str = Field(description="Confidence level in this assessment")
