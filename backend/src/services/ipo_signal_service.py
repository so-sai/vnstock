"""
IPO SIGNAL SERVICE - Tích hợp IPO Engine vào Decision Layer
================================================================================

Trách nhiệm:
  1. Quét danh sách IPO từ database / datasource
  2. Gọi IpoEngine.analyze() để phát hành tín hiệu
  3. Chuyển đổi sang IpoSignalResponse (camelCase API)
  4. Cung cấp điểm điều chỉnh cho Decision Engine (regime modulation)
  5. Tạo khẩu lệnh hành động cho frontend

Lưu ý:
  - Phải tương thích với decision_engine.py
  - Phải cung cấp regime_adjustment để decide_engine điều chỉnh tính cách
  - Phải cung cấp khẩu lệnh hành động ngắn gọn
"""

import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


class IpoSignalService:
    """
    Service Layer để tích hợp IPO signals vào Decision Engine
    
    Hình mẫu sử dụng:
      service = IpoSignalService(db_connection, ipo_engine)
      signal = service.get_daily_ipo_signal(datetime.now())
      
      # Điều chỉnh decision engine
      regime_adjusted = decision_engine.decide(
          regime_score=regime_score,
          regime_modifier=signal.regime_modifier,  # 0.8-1.2
      )
    """

    def __init__(self, ipo_engine):
        """
        Args:
            ipo_engine: Instance của IpoEngine từ engine/ipo_engine.py
        """
        self.ipo_engine = ipo_engine

    def get_daily_ipo_signal(
        self,
        current_date: datetime,
        active_ipo_symbols: List[str] = None,
        aftermarket_returns: Dict[str, float] = None,
        breadth_score: float = 0.5,
        secondary_volume_ratio: float = 1.0,
    ) -> "IpoSignalPackage":
        """
        Tính toán tín hiệu IPO hàng ngày
        
        Returns:
            IpoSignalPackage: Gói dữ liệu cho decision engine & frontend
        """

        # Gọi IPO engine để phân tích
        raw_signal = self.ipo_engine.analyze(
            current_date=current_date,
            active_ipo_symbols=active_ipo_symbols or [],
            aftermarket_returns=aftermarket_returns or {},
            breadth_score=breadth_score,
            secondary_volume_ratio=secondary_volume_ratio,
        )

        # Chuyển đổi sang format API (camelCase)
        api_response = self._convert_to_api_response(raw_signal)

        # Tính regime modifier cho decision engine
        regime_modifier = self._calculate_regime_modifier(raw_signal)

        # Tạo khẩu lệnh hành động
        action_command = self._generate_action_command(raw_signal)

        # Đóng gói toàn bộ
        package = IpoSignalPackage(
            signal_date=current_date,
            raw_signal=raw_signal,
            api_response=api_response,
            regime_modifier=regime_modifier,
            action_command=action_command,
        )

        logger.info(
            f"[IpoSignalService] Generated signal | "
            f"Traffic Light: {package.raw_signal.traffic_light.value} | "
            f"Regime Modifier: {regime_modifier:.2f}"
        )

        return package

    def _convert_to_api_response(self, raw_signal) -> Dict:
        """
        Chuyển đổi tín hiệu thô thành format API (camelCase)
        
        Dùng cho frontend & actionable_intelligence_service
        """

        return {
            "signalDate": raw_signal.signal_date.isoformat(),
            "trafficLight": raw_signal.traffic_light.value,

            "ipoIntensity": raw_signal.ipo_intensity.value,
            "capitalAbsorptionTrend": raw_signal.capital_absorption_trend.value,
            "secondaryMarketPressure": round(raw_signal.secondary_market_pressure, 1),

            "narrativeHeat": raw_signal.narrative_heat.value,
            "rotationRisk": raw_signal.rotation_risk.value,
            "midcapSmallcapPressure": round(raw_signal.midcap_smallcap_pressure, 1),

            "liquidityRegime": raw_signal.liquidity_regime.value,
            "regimeConfidence": round(raw_signal.regime_confidence, 2),

            "activeIpos": [
                {
                    "symbol": ipo.symbol,
                    "listingDate": ipo.listing_date.isoformat(),
                    "sector": ipo.sector,
                    "marketCapListing": ipo.market_cap_listing,
                }
                for ipo in raw_signal.active_ipo_list
            ],

            "aftermarketPerformance": {
                symbol: round(ret, 2)
                for symbol, ret in raw_signal.aftermarket_performance_pct.items()
            },
        }

    def _calculate_regime_modifier(self, raw_signal) -> float:
        """
        Tính hệ số điều chỉnh regime_score cho decision_engine
        
        Logic:
          - 🟢 XANH → 1.0-1.1 (tăng cơn khích thích)
          - 🟡 VÀNG → 0.9-1.0 (bình thường)
          - 🔴 ĐỎ → 0.7-0.9 (hạ chuẩn độ)
        
        Returns:
            float (0.7-1.2): Hệ số nhân cho regime_score
        """

        base_modifier = 1.0

        # Điều chỉnh theo traffic light
        if raw_signal.traffic_light.value == "DO":
            base_modifier = 0.8
        elif raw_signal.traffic_light.value == "VANG":
            base_modifier = 0.95
        else:  # XANH
            base_modifier = 1.05

        # Điều chỉnh thêm theo regime confidence
        confidence_factor = 0.9 + (raw_signal.regime_confidence * 0.2)

        final_modifier = base_modifier * confidence_factor

        # Clip vào khoảng [0.7, 1.2]
        final_modifier = max(0.7, min(1.2, final_modifier))

        logger.debug(
            f"[Regime Modifier] Base={base_modifier:.2f} | "
            f"Confidence Factor={confidence_factor:.2f} | "
            f"Final={final_modifier:.2f}"
        )

        return final_modifier

    def _generate_action_command(self, raw_signal) -> Dict:
        """
        Tạo khẩu lệnh hành động cho điều hành (HUD)
        
        Trả về:
          - primary_action: Hành động chính (BUY/HOLD/SELL)
          - risk_level: CAO/TRUNG_BINH/THAP
          - margin_setting: 1.0-2.0 (Normal-Double leverage)
          - sector_watch: Nhóm cần theo dõi (VD: "Midcap, Smallcap, Retail")
          - interpretation: Lý do bằng tiếng Việt
        """

        # Xác định hành động chính
        if raw_signal.traffic_light.value == "DO":
            primary_action = "SELL / REDUCE"
            risk_level = "CAO"
            margin_setting = 0.5  # Giảm leverage
        elif raw_signal.traffic_light.value == "VANG":
            primary_action = "HOLD"
            risk_level = "TRUNG_BINH"
            margin_setting = 1.0  # Normal
        else:  # XANH
            primary_action = "BUY / HOLD"
            risk_level = "THAP"
            margin_setting = 1.2  # Tăng nhẹ leverage

        # Xác định nhóm cần theo dõi
        sector_watch = []
        if raw_signal.rotation_risk.value == "CAO":
            sector_watch.append("Midcap")
            sector_watch.append("Smallcap")
        if raw_signal.narrative_heat.value == "BAT_THUONG":
            sector_watch.append("Retail / Consumer")
        if raw_signal.capital_absorption_trend.value == "TANG_MANH":
            sector_watch.append("Secondary Market Volume")

        # Lý do chi tiết
        interpretation = self._generate_detailed_interpretation(raw_signal)

        return {
            "primaryAction": primary_action,
            "riskLevel": risk_level,
            "marginSetting": margin_setting,
            "sectorWatch": sector_watch,
            "interpretation": interpretation,
            "updatedAt": datetime.now().isoformat(),
        }

    def _generate_detailed_interpretation(self, raw_signal) -> str:
        """
        Tạo lý do chi tiết (3-5 câu) bằng tiếng Việt cho người dùng
        """

        lines = []

        # Dòng 1: Tóm tắt tình hình IPO
        if raw_signal.ipo_intensity.value == "CAO":
            lines.append(
                "📊 Thị trường IPO NÓNG: Nhiều thương vụ lớn lên sàn, "
                "tiền bị hút mạnh."
            )
        elif raw_signal.ipo_intensity.value == "THAP":
            lines.append(
                "📊 Thị trường IPO TĨNH: Ít thương vụ lớn, tiền còn dồi dào."
            )

        # Dòng 2: Tác động đến breadth
        if raw_signal.rotation_risk.value == "CAO":
            lines.append(
                f"⚠️ RỦI RO ROTATION: Midcap/Smallcap bị ép bán ({raw_signal.midcap_smallcap_pressure:.0f}%), "
                "cẩn thận với chứng chỉ nhỏ vốn hóa."
            )

        # Dòng 3: Trạng thái chu kỳ
        if raw_signal.liquidity_regime.value == "THOAI_LUI":
            lines.append(
                "📉 CHU KỲ: Tiền bắt đầu rút khỏi thị trường, sắp lao dốc."
            )
        elif raw_signal.liquidity_regime.value == "DONG_TIEN_MO_RONG":
            lines.append(
                "📈 CHU KỲ: Thị trường mở rộng thực, tiền còn nhiều."
            )

        # Dòng 4: Khuyến cáo
        if raw_signal.traffic_light.value == "DO":
            lines.append(
                "🔴 HỢP ĐỀ: Kích hoạt phòng thủ, hạ vị thế, tránh speculative."
            )
        elif raw_signal.traffic_light.value == "VANG":
            lines.append(
                "🟡 HỢP ĐỀ: Thận trọng, đóng margin, giám sát sát sao."
            )
        else:
            lines.append(
                "🟢 HỢP ĐỀ: An toàn, tiếp tục phát huy lợi thế."
            )

        return " ".join(lines)


class IpoSignalPackage:
    """
    Gói dữ liệu IPO Signal - Chứa tất cả thông tin cần thiết
    
    Dùng để:
      1. Tính toán regime_modifier → decision_engine
      2. Tạo IpoSignalResponse → frontend
      3. Logging & audit trail
    """

    def __init__(
        self,
        signal_date: datetime,
        raw_signal,  # IpoMarketSignal from ipo_engine.py
        api_response: Dict,
        regime_modifier: float,
        action_command: Dict,
    ):
        self.signal_date = signal_date
        self.raw_signal = raw_signal
        self.api_response = api_response
        self.regime_modifier = regime_modifier
        self.action_command = action_command

    def to_frontend_json(self) -> Dict:
        """Trả về JSON cho frontend (chỉ API response + action command)"""
        return {
            **self.api_response,
            "actionCommand": self.action_command,
        }

    def to_decision_engine_input(self) -> Dict:
        """Trả về input cho decision_engine"""
        return {
            "ipo_regime_modifier": self.regime_modifier,
            "ipo_traffic_light": self.raw_signal.traffic_light.value,
            "ipo_rotation_risk": self.raw_signal.rotation_risk.value,
        }


__all__ = ["IpoSignalService", "IpoSignalPackage"]
