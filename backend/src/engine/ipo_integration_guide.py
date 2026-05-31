"""
IPO INTEGRATION GUIDE - Hướng dẫn Tích hợp IPO Engine vào Sentinel
================================================================================

PHẦN 1: TÍCH HỢP VÀO DECISION_ENGINE.PY
PHẦN 2: VÍ DỤ CỤ THỂ - DMX IPO 14.360 TỶ
PHẦN 3: CÁCH CHẠY HÀNG NGÀY (daily_updater.py)
================================================================================
"""

# ═════════════════════════════════════════════════════════════════════════════
# PHẦN 1: TÍCH HỢP VÀO DECISION_ENGINE.PY
# ═════════════════════════════════════════════════════════════════════════════

"""
Các thay đổi cần làm trong backend/src/engine/decision_engine.py:

1. Thêm import:
   ────────────────────────────────────────────────────────────────────
   from engine.ipo_engine import IpoEngine
   from services.ipo_signal_service import IpoSignalService
   
2. Trong hàm __init__ của DecisionEngine:
   ────────────────────────────────────────────────────────────────────
   self.ipo_engine = IpoEngine(
       market_cap_total_vnd_billions=4_500_000,
       midcap_ratio=0.20
   )
   self.ipo_service = IpoSignalService(self.ipo_engine)
   
3. Trong hàm decide() - Thêm đoạn này TRƯỚC khi gọi Model A/B:
   ────────────────────────────────────────────────────────────────────
   # Lấy tín hiệu IPO hàng ngày
   ipo_signal = self.ipo_service.get_daily_ipo_signal(
       current_date=decision_date,
       active_ipo_symbols=kwargs.get('active_ipo_symbols', []),
       aftermarket_returns=kwargs.get('aftermarket_returns', {}),
       breadth_score=breadth_score,
       secondary_volume_ratio=kwargs.get('secondary_volume_ratio', 1.0)
   )
   
   # Điều chỉnh regime_score nếu có tín hiệu IPO
   regime_score_adjusted = regime_score * ipo_signal.regime_modifier
   logger.info(
       f"[Decision Engine] IPO Regime Modifier: {ipo_signal.regime_modifier:.2f} | "
       f"Original: {regime_score:.2f} → Adjusted: {regime_score_adjusted:.2f}"
   )
   
4. Thay thế regime_score bằng regime_score_adjusted trong tính toán tiếp theo
   
5. Thêm vào output verdict:
   ────────────────────────────────────────────────────────────────────
   "ipo_signal": ipo_signal.action_command,
   "ipo_traffic_light": ipo_signal.raw_signal.traffic_light.value,
   
Kết quả: decision_engine sẽ tự động điều chỉnh phương pháp tiếp cận dựa
         trên tình hình IPO - Không cần can thiệp thủ công!
"""


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN 2: VÍ DỤ CỤTHỂ - DMX IPO 14.360 TỶ (Đưa vào hệ thống)
# ═════════════════════════════════════════════════════════════════════════════

def example_dmx_ipo_analysis():
    """
    Ví dụ: Phân tích DMX IPO 14.360 tỷ theo cách Sentinel xử lý
    
    Kịch bản:
      - DMX lên sàn: 2026-05-25 (hôm nay)
      - Quy mô: 14.360 tỷ VND → vào VN30 chắc chắn
      - Dự kiến: Retail phát cuồng, ngoại mua 30-40%
      - Aftermarket: +25% sau tuần đầu (peak narrative)
      - Tác động: Midcap/Smallcap bị ép bán ~ 8%
    """
    
    from datetime import datetime
    from engine.ipo_engine import IpoEngine, IpoEvent
    from services.ipo_signal_service import IpoSignalService
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 1: Khởi tạo engine
    # ─────────────────────────────────────────────────────────────────
    ipo_engine = IpoEngine(
        market_cap_total_vnd_billions=4_500_000,
        midcap_ratio=0.20
    )
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 2: Ghi nhận sự kiện DMX IPO
    # ─────────────────────────────────────────────────────────────────
    dmx_ipo = IpoEvent(
        symbol="DMX",
        listing_date=datetime(2026, 5, 25),
        listing_price=95_000,  # VND
        listing_volume=151_140_000,  # shares
        market_cap_listing=14_360,  # tỷ VND
        sector="DMX",  # ICB: Retail/Electronics
        exchange="HOSE",
    )
    ipo_engine.add_ipo_event(dmx_ipo)
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 3: Khởi tạo service
    # ─────────────────────────────────────────────────────────────────
    ipo_service = IpoSignalService(ipo_engine)
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 4: Giả lập dữ liệu thị trường ngày DMX lên sàn
    # ─────────────────────────────────────────────────────────────────
    
    current_date = datetime(2026, 5, 25)
    
    # Giả lập: Aftermarket DMX +25% trong tuần đầu
    aftermarket_returns = {
        "DMX": 25.0,  # IPO tăng 25%
    }
    
    # Giả lập: Breadth score giảm do hút tiền
    breadth_score = 0.38  # Giảm từ 0.50 xuống 0.38 (warning!)
    
    # Giả lập: Secondary market volume giảm 15%
    secondary_volume_ratio = 0.85
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 5: Phân tích & lấy tín hiệu
    # ─────────────────────────────────────────────────────────────────
    ipo_signal = ipo_service.get_daily_ipo_signal(
        current_date=current_date,
        active_ipo_symbols=["DMX"],
        aftermarket_returns=aftermarket_returns,
        breadth_score=breadth_score,
        secondary_volume_ratio=secondary_volume_ratio,
    )
    
    # ─────────────────────────────────────────────────────────────────
    # BƯỚC 6: In kết quả
    # ─────────────────────────────────────────────────────────────────
    print("\n" + "="*80)
    print("DMX IPO - TÍN HIỆU CẤU TRÚC THỊ TRƯỜNG")
    print("="*80 + "\n")
    
    # Tín hiệu thô
    raw = ipo_signal.raw_signal
    print(f"TẦNG 1 (ĐỊNH GIÁ):")
    print(f"  Valuation Risk Score: {raw.valuation_risk_score:.1f}")
    print(f"  → Giải thích: IPO +25% trong tuần → peak narrative warning!\n")
    
    print(f"TẦNG 2 (THANH KHOẢN):")
    print(f"  IPO Intensity: {raw.ipo_intensity.value}")
    print(f"  Capital Absorption: {raw.capital_absorption_trend.value}")
    print(f"  Secondary Market Pressure: {raw.secondary_market_pressure:.1f}%")
    print(f"  → Giải thích: 14.360T = 0.32% tổng vốn hóa, nhưng tiền bị hút\n")
    
    print(f"TẦNG 3 (LUÂN CHUYỂN):")
    print(f"  Narrative Heat: {raw.narrative_heat.value}")
    print(f"  Rotation Risk: {raw.rotation_risk.value}")
    print(f"  Midcap/Smallcap Pressure: {raw.midcap_smallcap_pressure:.1f}%")
    print(f"  → Giải thích: Retail phát cuồng, Midcap/Smallcap bị ép bán\n")
    
    print(f"TẦNG 4 (CHU KỲ):")
    print(f"  Liquidity Regime: {raw.liquidity_regime.value}")
    print(f"  Regime Confidence: {raw.regime_confidence:.2f}")
    print(f"  → Giải thích: Breadth yếu dần → tightening regime\n")
    
    print(f"{'─'*80}")
    print(f"🟢🟡🔴 TÍN HIỆU GIAO TIẾP: {raw.traffic_light.value}")
    print(f"{'─'*80}\n")
    
    # API Response (camelCase cho frontend)
    api = ipo_signal.api_response
    print("API RESPONSE (cho Frontend):")
    print(f"  trafficLight: {api['trafficLight']}")
    print(f"  ipoIntensity: {api['ipoIntensity']}")
    print(f"  liquidityRegime: {api['liquidityRegime']}\n")
    
    # Action Command (khẩu lệnh hành động)
    action = ipo_signal.action_command
    print("KHẨU LỆnh HỲA ĐỘNG (cho điều hành):")
    print(f"  Primary Action: {action['primaryAction']}")
    print(f"  Risk Level: {action['riskLevel']}")
    print(f"  Margin Setting: {action['marginSetting']}")
    print(f"  Sector Watch: {', '.join(action['sectorWatch'])}")
    print(f"  Interpretation:\n    {action['interpretation']}\n")
    
    # Regime Modifier (cho decision engine)
    print("REGIME MODIFIER (cho Decision Engine):")
    print(f"  Điều chỉnh: {ipo_signal.regime_modifier:.2f}x")
    print(f"  Nếu regime_score = 0.50 → điều chỉnh thành = {0.50 * ipo_signal.regime_modifier:.2f}\n")
    
    print("="*80)
    print("KẾT LUẬN:")
    print("="*80)
    
    if raw.traffic_light.value == "DO":
        print("🔴 ĐỲ: Kích hoạt PHÒNG THỦ!")
        print("   - Hạ vị thế hiện tại")
        print("   - Tránh Midcap/Smallcap")
        print("   - Giám sát sát sao secondary market")
    elif raw.traffic_light.value == "VANG":
        print("🟡 THẬN TRỌNG: Tiếp tục nhưng giám sát!")
        print("   - Đóng margin")
        print("   - Tránh speculative trades")
    else:
        print("🟢 AN TOÀN: Tiếp tục theo kế hoạch!")
    
    print("\n")


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN 3: TÍCH HỢP VÀO DAILY_UPDATER.PY
# ═════════════════════════════════════════════════════════════════════════════

"""
Để chạy hàng ngày trong daily_updater.py, thêm đoạn này:

─────────────────────────────────────────────────────────────────────────────

# Trong hàm main() hoặc schedule.every().day.at("16:30"):

from engine.ipo_engine import IpoEngine
from services.ipo_signal_service import IpoSignalService
from database import get_active_ipos, get_aftermarket_returns

def update_ipo_signals():
    '''Cập nhật tín hiệu IPO hàng ngày (chạy lúc 16:30 EOD)'''
    
    # Khởi tạo
    ipo_engine = IpoEngine()
    
    # Load IPO lịch sử từ database
    historical_ipos = get_active_ipos()  # Truy vấn database
    for ipo in historical_ipos:
        ipo_engine.add_ipo_event(ipo)
    
    # Khởi tạo service
    ipo_service = IpoSignalService(ipo_engine)
    
    # Lấy thông tin thị trường hiện tại
    from engine.regime_engine import RegimeEngine
    regime_engine = RegimeEngine()
    regime_score, breadth_score = regime_engine.calculate()
    
    # Tính aftermarket returns cho các IPO đang hot
    aftermarket_returns = get_aftermarket_returns(days_back=90)
    
    # Phân tích
    ipo_signal = ipo_service.get_daily_ipo_signal(
        current_date=datetime.now(),
        active_ipo_symbols=list(aftermarket_returns.keys()),
        aftermarket_returns=aftermarket_returns,
        breadth_score=breadth_score,
    )
    
    # Lưu vào database / JSON
    save_ipo_signal_to_db(ipo_signal)
    
    logger.info(
        f"[Daily Update] IPO Signal: {ipo_signal.raw_signal.traffic_light.value}"
    )

─────────────────────────────────────────────────────────────────────────────
"""


# ═════════════════════════════════════════════════════════════════════════════
# CHẠY VÍ DỤ
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    example_dmx_ipo_analysis()
