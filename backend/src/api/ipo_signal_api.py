"""
IPO SIGNAL API ENDPOINT — Cung cấp dữ liệu cho Frontend HUD Widget
================================================================================

Endpoint: GET /api/intelligence/ipo-signal/

Trả về: JSON với 3 chỉ báo chính + dữ liệu phần biên
  - Tín hiệu tổng (🟢🟡🔴)
  - Áp suất rút vốn (LDI: 0-100)
  - Thời gian nhiễm độc (countdown theo phiên)

Tích hợp:
  - Gọi IpoEngine.analyze() từ backend
  - Tính Time Decay dựa trên ngày listing
  - Trả về format API (camelCase)
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from pydantic import BaseModel
import math

# Giả định import từ hệ thống hiện tại
# from database import get_db
# from services.ipo_signal_service import IpoSignalService
# from engine.ipo_engine import IpoEngine
# from models import AlphaBaseModel

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class IPOHUDResponse(BaseModel):
    """API Response cho IPO HUD Widget"""
    
    ipo_signal: Dict
    updated_at: datetime
    
    class Config:
        json_schema_extra = {
            "example": {
                "ipo_signal": {
                    "symbol": "DMX",
                    "listing_date": "2026-05-25",
                    "market_cap_billion": 14360,
                    "traffic_light": "DO",
                    "valuation_risk_score": 65.5,
                    "capital_absorption_trend": "TANG_MANH",
                    "secondary_market_pressure": 72.0,
                    "rotation_risk": "CAO",
                    "midcap_smallcap_pressure": 65.0,
                    "liquidity_regime": "TANG_GIAN",
                    "regime_modifier": 0.82,
                    "days_until_decay": 4,
                    "action_command": {
                        "primaryAction": "SELL / REDUCE",
                        "riskLevel": "CAO",
                        "marginSetting": 0.5,
                        "sectorWatch": ["Midcap", "Smallcap"],
                        "interpretation": "🔴 IPO nóng + Midcap/Smallcap chết liquidity. Kích hoạt phòng thủ."
                    }
                },
                "updated_at": "2026-05-25T16:30:00Z"
            }
        }


def calculate_days_until_decay(listing_date_str: str) -> int:
    """
    Tính số phiên từ ngày listing cho đến khi IPO áp lực hết
    
    Logic:
      - Mỗi phiên = 1 ngày giao dịch
      - IPO mạnh nhất trong 3-5 phiên đầu
      - Sau 10 phiên, áp lực chỉ còn 15%, coi như hết
      - Công thức suy hao: factor = exp(-t / 3.0)
    
    Args:
        listing_date_str: "2026-05-25" format
    
    Returns:
        int: Số phiên còn lại trước khi áp lực hết (max 15)
    """
    try:
        listing_date = datetime.fromisoformat(listing_date_str)
    except:
        return 0
    
    current_date = datetime.now()
    days_since_listing = (current_date - listing_date).days
    
    # Nếu IPO hôm nay
    if days_since_listing <= 0:
        return 5  # Áp lực cao nhất, kéo dài 5 phiên
    
    # Nếu IPO đã qua 10 phiên
    if days_since_listing >= 10:
        return 0  # Hết áp lực
    
    # Tính toán: ngược lại
    # days_since_listing = 0 → days_until_decay = 5
    # days_since_listing = 5 → days_until_decay = 0
    days_until_decay = max(0, 5 - days_since_listing)
    return int(days_until_decay)


def transform_ipo_signal_for_hud(
    raw_ipo_signal,  # từ IpoMarketSignal (ipo_engine.py)
    ipo_service_package,  # từ IpoSignalPackage (ipo_signal_service.py)
) -> Dict:
    """
    Chuyển đổi tín hiệu thô thành format API cho HUD Widget
    
    Input: Raw IpoMarketSignal + IpoSignalPackage
    Output: Dict clean, camelCase, có cộng Time Decay
    """
    
    # Tính Time Decay
    if raw_ipo_signal.active_ipo_list:
        latest_ipo = raw_ipo_signal.active_ipo_list[0]
        days_until_decay = calculate_days_until_decay(
            latest_ipo.listing_date.isoformat()
        )
    else:
        days_until_decay = 0
    
    # Chuyển đổi tín hiệu
    hud_data = {
        "symbol": (
            raw_ipo_signal.active_ipo_list[0].symbol
            if raw_ipo_signal.active_ipo_list
            else "N/A"
        ),
        "listing_date": (
            raw_ipo_signal.active_ipo_list[0].listing_date.isoformat()
            if raw_ipo_signal.active_ipo_list
            else None
        ),
        "market_cap_billion": (
            raw_ipo_signal.active_ipo_list[0].market_cap_listing
            if raw_ipo_signal.active_ipo_list
            else 0
        ),
        "traffic_light": raw_ipo_signal.traffic_light.value,  # "XANH" | "VANG" | "DO"
        "valuation_risk_score": round(raw_ipo_signal.valuation_risk_score, 1),
        "capital_absorption_trend": raw_ipo_signal.capital_absorption_trend.value,
        "secondary_market_pressure": round(raw_ipo_signal.secondary_market_pressure, 1),
        "rotation_risk": raw_ipo_signal.rotation_risk.value,
        "midcap_smallcap_pressure": round(raw_ipo_signal.midcap_smallcap_pressure, 1),
        "liquidity_regime": raw_ipo_signal.liquidity_regime.value,
        "regime_modifier": round(ipo_service_package.regime_modifier, 2),
        "days_until_decay": days_until_decay,
        "action_command": ipo_service_package.action_command,
    }
    
    return hud_data


@router.get("/ipo-signal/", response_model=IPOHUDResponse)
async def get_ipo_hud_signal(
    # Giả định: db: Session = Depends(get_db)
):
    """
    API Endpoint: Lấy tín hiệu IPO cho HUD Widget
    
    Returns:
        IPOHUDResponse: 3 Khối chỉ báo + Action command
    
    Responses:
        200: IPO signal found
        404: No active IPO
    """
    
    try:
        # 1. Khởi tạo IPO Service
        from src.engine.ipo_engine import IpoEngine
        from src.services.ipo_signal_service import IpoSignalService
        from src.database.timeline_manager import get_active_ipos, get_aftermarket_returns
        
        ipo_engine = IpoEngine()
        ipo_service = IpoSignalService(ipo_engine)
        
        # 2. Load IPO lịch sử từ DB
        active_ipos = get_active_ipos(days_back=90)  # IPO trong 90 ngày gần đây
        for ipo in active_ipos:
            ipo_engine.add_ipo_event(ipo)
        
        # 3. Lấy thông tin thị trường hiện tại
        from src.engine.regime_engine import RegimeEngine
        regime_engine = RegimeEngine()
        regime_score, breadth_score, _ = regime_engine.calculate()
        
        # 4. Tính aftermarket returns
        aftermarket_returns = get_aftermarket_returns(days_back=90)
        
        # 5. Phân tích IPO signal
        ipo_signal = ipo_service.get_daily_ipo_signal(
            current_date=datetime.now(),
            active_ipo_symbols=list(aftermarket_returns.keys()),
            aftermarket_returns=aftermarket_returns,
            breadth_score=breadth_score,
            secondary_volume_ratio=1.0,  # TODO: Lấy từ daily market data
        )
        
        # 6. Chuyển đổi cho HUD
        hud_data = transform_ipo_signal_for_hud(
            ipo_signal.raw_signal,
            ipo_signal,
        )
        
        response = IPOHUDResponse(
            ipo_signal=hud_data,
            updated_at=datetime.now(),
        )
        
        return response
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"IPO Signal Error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")


@router.get("/ipo-signal/history/")
async def get_ipo_signal_history(
    days: int = 30,
):
    """
    API Endpoint: Lấy lịch sử tín hiệu IPO (30 ngày gần đây)
    
    Dùng cho charting / trend analysis
    """
    try:
        # Hiện tại chưa có lịch sử IPO lưu trữ, trả về danh sách rỗng để frontend xử lý mềm dẻo.
        return []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test endpoint
    import uvicorn
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    # python -m uvicorn ipo_signal_api:app --reload
    uvicorn.run(app, host="0.0.0.0", port=8000)
