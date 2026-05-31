"""
IPO MARKET STRUCTURE SIGNAL ENGINE (Động cơ Tín hiệu Cấu trúc IPO)
================================================================================

Lõi tư duy: IPO lớn KHÔNG phải một sự kiện tin tức tách biệt.
Nó là một TÍN HIỆU HẤP THỤ VỐN - thay đổi cấu trúc thanh khoản & chu kỳ thị trường.

Mục đích:
  1. Phát hiện thương vụ IPO quy mô lớn (> 5.000 tỷ VND)
  2. Đo lường áp suất hút tiền lên toàn thị trường
  3. Phân tích hiệu suất aftermarket (tín hiệu của khẩu vị rủi ro)
  4. Xác định rủi ro đảo danh mục VN30 (rotation risk)
  5. Phân loại chu kỳ thị trường (Mở rộng vs Cuối sóng)

Lệnh thực chiến:
  - ipo_intensity: CAO/TRUNG_BINH/THAP
  - muc_do_hut_von: TANG_MANH/ON_DINH/GIAM
  - do_nong_canh_bao: BAT_THUONG/BINH_THUONG/THAP
  - rui_ro_rotation: CAO/TRUNG_BINH/THAP
  - trang_thai_thanh_khoan: DONG_TIEN_MO_RONG/TANG_GIAN/THOAI_LUI

Tín hiệu giao tiếp (Traffic Light):
  🟢 XANH:   IPO mạnh + ngoại mua + breadth khỏe → An toàn, tiếp tục
  🟡 VÀNG:   IPO thành công + thanh khoản toàn sàn tụt → Thận trọng
  🔴 ĐỎ:     IPO cuồng + Midcap/Smallcap chết liquidity → Kích hoạt phòng thủ

================================================================================
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# KHẨU LỆnh ĐỊnh lượng
# ═════════════════════════════════════════════════════════════════════════════


class IpoIntensity(Enum):
    """Cường độ IPO: Mức độ nóng của thị trường IPO"""
    CAO = "CAO"  # > 10.000 tỷ / tháng
    TRUNG_BINH = "TRUNG_BINH"  # 5.000 - 10.000 tỷ / tháng
    THAP = "THAP"  # < 5.000 tỷ / tháng


class CapitalAbsorption(Enum):
    """Mức độ hút vốn: IPO chưa / vừa / đã hút máu thị trường"""
    TANG_MANH = "TANG_MANH"  # Tiền dồn về IPO, liquidity sàn giao dịch bị hút
    ON_DINH = "ON_DINH"  # IPO thành công nhưng tiền xoay vòng bình thường
    GIAM = "GIAM"  # Tiền bắt đầu rút khỏi thị trường chứng khoán


class NarrativeHeat(Enum):
    """Độ nóng theo dõi (Narrative Concentration Risk)"""
    BAT_THUONG = "BAT_THUONG"  # Retail phát cuồng, media đầy tranh luận
    BINH_THUONG = "BINH_THUONG"  # Mức bình thường
    THAP = "THAP"  # Ít ai quan tâm


class RotationRisk(Enum):
    """Rủi ro đảo danh mục (VN30 rebalance impact)"""
    CAO = "CAO"  # Midcap/Smallcap bị ép bán mạnh để mua IPO vào rổ
    TRUNG_BINH = "TRUNG_BINH"  # Áp lực vừa phải
    THAP = "THAP"  # Ít áp lực


class LiquidityRegime(Enum):
    """Trạng thái thanh khoản toàn thị trường"""
    DONG_TIEN_MO_RONG = "DONG_TIEN_MO_RONG"  # Expansion regime: tiền nhiều
    TANG_GIAN = "TANG_GIAN"  # Tightening: tiền sạch dần
    THOAI_LUI = "THOAI_LUI"  # Withdrawal: tiền chảy ra ngoài hệ thống


class TrafficLightSignal(Enum):
    """Tín hiệu giao tiếp thực chiến"""
    XANH = "XANH"  # 🟢 An toàn, tiếp tục
    VANG = "VANG"  # 🟡 Thận trọng, đóng margin
    DO = "DO"  # 🔴 Nguy hiểm, kích hoạt phòng thủ


# ═════════════════════════════════════════════════════════════════════════════
# CẤU TRÚC DỮ LIỆU IPO
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class IpoEvent:
    """Sự kiện IPO - Bản ghi trong lịch sử IPO"""
    symbol: str
    listing_date: datetime
    listing_price: float  # VND
    listing_volume: int  # shares
    market_cap_listing: float  # VND tỷ
    sector: str  # ICB code
    exchange: str  # HOSE / HNX / UPCOM


@dataclass
class IpoMarketSignal:
    """Tín hiệu cấu trúc thị trường từ IPO - Đầu vào cho Sentinel"""
    signal_date: datetime
    
    # Tầng 1: Định giá
    valuation_risk_score: float  # 0-100, cao = peak narrative warning
    
    # Tầng 2: Thanh khoản
    ipo_intensity: IpoIntensity
    capital_absorption_trend: CapitalAbsorption
    secondary_market_pressure: float  # 0-100, cao = liquidity vacuum
    
    # Tầng 3: Luân chuyển
    narrative_heat: NarrativeHeat
    rotation_risk: RotationRisk
    midcap_smallcap_pressure: float  # 0-100, cao = đang bị ép bán
    
    # Tầng 4: Chu kỳ
    liquidity_regime: LiquidityRegime
    regime_confidence: float  # 0-1.0
    
    # Tín hiệu giao tiếp
    traffic_light: TrafficLightSignal
    
    # Dữ liệu chi tiết
    active_ipo_list: List[IpoEvent]
    aftermarket_performance_pct: Dict[str, float]  # {symbol: return_pct}


# ═════════════════════════════════════════════════════════════════════════════
# ENGINE CORE
# ═════════════════════════════════════════════════════════════════════════════


class IpoEngine:
    """
    Động cơ tín hiệu IPO - Phát hiện & mã hóa tác động cấu trúc
    
    Trách nhiệm:
      1. Quét danh sách IPO hiện tại & gần đây
      2. Tính toán áp suất hút tiền từ quy mô IPO
      3. Đo lường hiệu suất aftermarket (khẩu vị rủi ro)
      4. Đánh giá tác động đến Midcap/Smallcap (rotation risk)
      5. Xác định chu kỳ thị trường (expansion vs late-cycle)
      6. Phát hành tín hiệu giao tiếp (xanh/vàng/đỏ)
    """
    
    def __init__(
        self,
        market_cap_total_vnd_billions: float = 4_500_000,  # VN30 ~ 4.5 triệu tỷ
        midcap_ratio: float = 0.20,  # Midcap chiếm 20% tổng vốn hóa
    ):
        """
        Args:
            market_cap_total_vnd_billions: Tổng vốn hóa thị trường (tỷ VND)
            midcap_ratio: Tỷ trọng Midcap để tính áp lực
        """
        self.market_cap_total = market_cap_total_vnd_billions
        self.midcap_ratio = midcap_ratio
        self.ipo_history: List[IpoEvent] = []
    
    def add_ipo_event(self, ipo: IpoEvent) -> None:
        """Ghi nhận một sự kiện IPO vào lịch sử"""
        self.ipo_history.append(ipo)
        logger.info(
            f"[IPO Logged] {ipo.symbol} | {ipo.market_cap_listing:.0f}T | "
            f"{ipo.listing_date.strftime('%Y-%m-%d')}"
        )
    
    def analyze(
        self,
        current_date: datetime,
        active_ipo_symbols: List[str] = None,
        aftermarket_returns: Dict[str, float] = None,
        breadth_score: float = 0.5,  # từ regime_engine (0-1.0)
        secondary_volume_ratio: float = 1.0,  # volume toàn sàn / baseline
    ) -> IpoMarketSignal:
        """
        Phân tích tín hiệu IPO theo 4 tầng & phát hành tín hiệu giao tiếp
        
        Args:
            current_date: Ngày phân tích
            active_ipo_symbols: Danh sách symbol IPO trong vòng 90 ngày gần đây
            aftermarket_returns: {symbol: return_pct} tính từ listing date
            breadth_score: Health của thị trường (0-1.0), cao = khỏe
            secondary_volume_ratio: So sánh khối lượng sàn giao dịch vs cơ bản
        
        Returns:
            IpoMarketSignal: Tín hiệu đầy đủ 4 tầng
        """
        
        active_ipo_symbols = active_ipo_symbols or []
        aftermarket_returns = aftermarket_returns or {}
        
        # ─────────────────────────────────────────────────────────────────────
        # TẦNG 1: ĐỊNH GIÁ (VALUATION RISK)
        # ─────────────────────────────────────────────────────────────────────
        valuation_risk_score = self._calculate_valuation_risk(
            active_ipo_symbols, aftermarket_returns, current_date
        )
        
        # ─────────────────────────────────────────────────────────────────────
        # TẦNG 2: THANH KHOẢN (LIQUIDITY PRESSURE)
        # ─────────────────────────────────────────────────────────────────────
        (
            ipo_intensity,
            capital_absorption,
            secondary_pressure,
        ) = self._calculate_liquidity_impact(current_date, active_ipo_symbols)
        
        # ─────────────────────────────────────────────────────────────────────
        # TẦNG 3: LUÂN CHUYỂN (ROTATION & NARRATIVE)
        # ─────────────────────────────────────────────────────────────────────
        (
            narrative_heat,
            rotation_risk,
            midcap_pressure,
        ) = self._calculate_rotation_impact(
            aftermarket_returns, breadth_score, active_ipo_symbols
        )
        
        # ─────────────────────────────────────────────────────────────────────
        # TẦNG 4: CHU KỲ (REGIME CLASSIFICATION)
        # ─────────────────────────────────────────────────────────────────────
        (
            liquidity_regime,
            regime_confidence,
        ) = self._classify_regime(
            ipo_intensity,
            capital_absorption,
            breadth_score,
            secondary_volume_ratio,
        )
        
        # ─────────────────────────────────────────────────────────────────────
        # TÍN HIỆU GIAO TIẾP (TRAFFIC LIGHT)
        # ─────────────────────────────────────────────────────────────────────
        traffic_light = self._generate_traffic_light(
            valuation_risk_score=valuation_risk_score,
            ipo_intensity=ipo_intensity,
            capital_absorption=capital_absorption,
            secondary_pressure=secondary_pressure,
            rotation_risk=rotation_risk,
            breadth_score=breadth_score,
            aftermarket_returns=aftermarket_returns,
        )
        
        signal = IpoMarketSignal(
            signal_date=current_date,
            valuation_risk_score=valuation_risk_score,
            ipo_intensity=ipo_intensity,
            capital_absorption_trend=capital_absorption,
            secondary_market_pressure=secondary_pressure,
            narrative_heat=narrative_heat,
            rotation_risk=rotation_risk,
            midcap_smallcap_pressure=midcap_pressure,
            liquidity_regime=liquidity_regime,
            regime_confidence=regime_confidence,
            traffic_light=traffic_light,
            active_ipo_list=[
                ipo for ipo in self.ipo_history
                if (current_date - ipo.listing_date).days <= 90
            ],
            aftermarket_performance_pct=aftermarket_returns,
        )
        
        self._log_signal(signal)
        return signal
    
    def _calculate_valuation_risk(
        self,
        active_ipo_symbols: List[str],
        aftermarket_returns: Dict[str, float],
        current_date: datetime,
    ) -> float:
        """
        Tầng 1: Định giá có quá cao không?
        
        Logic:
          - Nếu IPO tăng 50% + trong tuần đầu → peak narrative warning
          - Nếu nhiều IPO cùng mua vào → narrative concentration
          - Nếu consumer brands + retail IPO cùng → late-cycle signal
        
        Returns:
            Điểm 0-100 (cao = cảnh báo định giá)
        """
        
        if not active_ipo_symbols or not aftermarket_returns:
            return 0.0
        
        # Tính % IPO có return > 30% trong tuần đầu
        strong_performers = sum(
            1 for symbol in active_ipo_symbols
            if aftermarket_returns.get(symbol, 0) > 30
        )
        
        concentration_pct = (strong_performers / len(active_ipo_symbols)) * 100 \
            if active_ipo_symbols else 0
        
        # Nếu > 50% IPO tăng mạnh → peak narrative warning
        valuation_risk = min(concentration_pct * 1.5, 100.0)
        
        logger.info(
            f"[Valuation Risk] Strong performers: {strong_performers}/"
            f"{len(active_ipo_symbols)} | Risk Score: {valuation_risk:.1f}"
        )
        
        return valuation_risk
    
    def _calculate_liquidity_impact(
        self,
        current_date: datetime,
        active_ipo_symbols: List[str],
    ) -> Tuple[IpoIntensity, CapitalAbsorption, float]:
        """
        Tầng 2: Thanh khoản - IPO hút bao nhiêu tiền?
        
        Logic:
          - Quy mô IPO > 10.000T → cường độ CAO
          - So sánh khối lượng IPO vs tổng vốn hóa → hút máu
          - Nếu IPO kéo dài qua nhiều ngày → áp suất kéo dài
        
        Returns:
            (intensity, capital_absorption, secondary_market_pressure)
        """
        
        # Tính tổng quy mô IPO trong 30 ngày gần đây
        recent_ipos = [
            ipo for ipo in self.ipo_history
            if (current_date - ipo.listing_date).days <= 30
        ]
        
        total_ipo_capital = sum(ipo.market_cap_listing for ipo in recent_ipos)
        
        # Phân loại cường độ
        if total_ipo_capital > 10_000:
            intensity = IpoIntensity.CAO
        elif total_ipo_capital > 5_000:
            intensity = IpoIntensity.TRUNG_BINH
        else:
            intensity = IpoIntensity.THAP
        
        # Tính % IPO so với tổng vốn hóa
        capital_absorption_pct = (total_ipo_capital / self.market_cap_total) * 100
        
        # Phân loại áp suất hút vốn
        if capital_absorption_pct > 1.0:  # > 1% tổng vốn hóa
            capital_absorption = CapitalAbsorption.TANG_MANH
        elif capital_absorption_pct > 0.3:
            capital_absorption = CapitalAbsorption.ON_DINH
        else:
            capital_absorption = CapitalAbsorption.GIAM
        
        # Tính áp suất lên secondary market
        # Nếu IPO nhiều + quy mô lớn → áp suất cao
        secondary_pressure = min(
            capital_absorption_pct * 50,  # scaled to 0-100
            100.0,
        )
        
        logger.info(
            f"[Liquidity Impact] Total IPO (30d): {total_ipo_capital:.0f}T | "
            f"% Market Cap: {capital_absorption_pct:.2f}% | "
            f"Intensity: {intensity.value} | Pressure: {secondary_pressure:.1f}"
        )
        
        return intensity, capital_absorption, secondary_pressure
    
    def _calculate_rotation_impact(
        self,
        aftermarket_returns: Dict[str, float],
        breadth_score: float,
        active_ipo_symbols: List[str],
    ) -> Tuple[NarrativeHeat, RotationRisk, float]:
        """
        Tầng 3: Luân chuyển & Câu chuyện (Narrative Heat)
        
        Logic:
          - Nếu IPO aftermarket mạnh (return > 20%) + retail mua cuồng
            → narrative_heat = BAT_THUONG
          - Nếu breadth_score giảm trong khi IPO nóng
            → rotation_risk = CAO (Midcap/Smallcap bị ép)
        
        Returns:
            (narrative_heat, rotation_risk, midcap_pressure)
        """
        
        # Tính avg return của IPO
        avg_return = sum(
            aftermarket_returns.get(s, 0) for s in active_ipo_symbols
        ) / len(active_ipo_symbols) if active_ipo_symbols else 0
        
        # Phân loại độ nóng
        if avg_return > 20:
            narrative_heat = NarrativeHeat.BAT_THUONG  # Câu chuyện nóng
        elif avg_return > 10:
            narrative_heat = NarrativeHeat.BINH_THUONG
        else:
            narrative_heat = NarrativeHeat.THAP
        
        # Rotation risk dựa trên breadth + narrative heat
        # Nếu breadth_score thấp (< 0.4) + narrative_heat cao → rotation risk cao
        if breadth_score < 0.4 and narrative_heat == NarrativeHeat.BAT_THUONG:
            rotation_risk = RotationRisk.CAO
            midcap_pressure = min(
                (1 - breadth_score) * 100 + (avg_return * 2),
                100.0,
            )
        elif breadth_score < 0.5:
            rotation_risk = RotationRisk.TRUNG_BINH
            midcap_pressure = (1 - breadth_score) * 80
        else:
            rotation_risk = RotationRisk.THAP
            midcap_pressure = max(0, (0.6 - breadth_score) * 50)
        
        logger.info(
            f"[Rotation Impact] Avg IPO Return: {avg_return:.1f}% | "
            f"Breadth: {breadth_score:.2f} | "
            f"Narrative Heat: {narrative_heat.value} | "
            f"Rotation Risk: {rotation_risk.value} | "
            f"Midcap Pressure: {midcap_pressure:.1f}"
        )
        
        return narrative_heat, rotation_risk, midcap_pressure
    
    def _classify_regime(
        self,
        ipo_intensity: IpoIntensity,
        capital_absorption: CapitalAbsorption,
        breadth_score: float,
        secondary_volume_ratio: float,
    ) -> Tuple[LiquidityRegime, float]:
        """
        Tầng 4: Xác định chu kỳ - Expansion vs Late-Cycle?
        
        Logic:
          - Nếu IPO nhiều + breadth khỏe + volume bình thường
            → EXPANSION (tiền nhiều, thị trường mở rộng thực)
          - Nếu IPO nhiều + breadth yếu + volume tụt
            → TANG_GIAN hay THOAI_LUI (tiền rút, sắp crash)
        
        Returns:
            (liquidity_regime, confidence)
        """
        
        # Đánh giá martialing signal
        expansion_signals = 0
        confidence_factors = []
        
        # Signal 1: IPO intensity
        if ipo_intensity == IpoIntensity.CAO:
            expansion_signals += 1
            confidence_factors.append(0.7)
        else:
            confidence_factors.append(0.4)
        
        # Signal 2: Breadth health
        if breadth_score > 0.5:
            expansion_signals += 1
            confidence_factors.append(0.8)
        else:
            confidence_factors.append(0.3)
        
        # Signal 3: Volume confirmation
        if secondary_volume_ratio >= 1.0:
            expansion_signals += 1
            confidence_factors.append(0.7)
        else:
            confidence_factors.append(0.2)
        
        # Signal 4: Capital absorption pattern
        if capital_absorption == CapitalAbsorption.TANG_MANH:
            expansion_signals += 0.5  # Tích cực nhưng có cảnh báo
            confidence_factors.append(0.6)
        else:
            confidence_factors.append(0.5)
        
        # Phân loại regime
        if expansion_signals >= 2.5:
            regime = LiquidityRegime.DONG_TIEN_MO_RONG
        elif expansion_signals >= 1.5:
            regime = LiquidityRegime.TANG_GIAN
        else:
            regime = LiquidityRegime.THOAI_LUI
        
        # Tính độ tin cậy (0-1.0)
        confidence = sum(confidence_factors) / len(confidence_factors) \
            if confidence_factors else 0.5
        
        logger.info(
            f"[Regime Classification] Signals: {expansion_signals:.1f}/4 | "
            f"Regime: {regime.value} | Confidence: {confidence:.2f}"
        )
        
        return regime, confidence
    
    def _generate_traffic_light(
        self,
        valuation_risk_score: float,
        ipo_intensity: IpoIntensity,
        capital_absorption: CapitalAbsorption,
        secondary_pressure: float,
        rotation_risk: RotationRisk,
        breadth_score: float,
        aftermarket_returns: Dict[str, float],
    ) -> TrafficLightSignal:
        """
        Phát hành tín hiệu giao tiếp 3 màu
        
        🟢 XANH: IPO mạnh + ngoại mua + breadth khỏe → An toàn
        🟡 VÀNG: IPO thành công + thanh khoản sàn tụt → Thận trọng
        🔴 ĐỎ: IPO cuồng + Midcap chết liquidity → Nguy hiểm
        
        Returns:
            TrafficLightSignal
        """
        
        # Đếm điểm "xanh"
        green_score = 0
        red_score = 0
        
        # Đánh giá điểm 1: Hiệu suất aftermarket
        if aftermarket_returns:
            avg_return = sum(aftermarket_returns.values()) / len(aftermarket_returns)
            if avg_return > 15:  # IPO tăng tốt
                green_score += 1
            elif avg_return < -5:  # IPO tụt
                red_score += 2
        
        # Đánh giá điểm 2: Áp lực thanh khoản
        if secondary_pressure < 30:
            green_score += 1
        elif secondary_pressure > 70:
            red_score += 2
        
        # Đánh giá điểm 3: Breadth confirmation
        if breadth_score > 0.55:
            green_score += 1
        elif breadth_score < 0.4:
            red_score += 1
        
        # Đánh giá điểm 4: Rotation risk
        if rotation_risk == RotationRisk.THAP:
            green_score += 1
        elif rotation_risk == RotationRisk.CAO:
            red_score += 2
        
        # Đánh giá điểm 5: Valuation
        if valuation_risk_score < 30:
            green_score += 1
        elif valuation_risk_score > 70:
            red_score += 1
        
        # Quyết định tín hiệu
        if red_score >= 3:
            signal = TrafficLightSignal.DO
        elif red_score >= 1 and green_score < 2:
            signal = TrafficLightSignal.VANG
        else:
            signal = TrafficLightSignal.XANH
        
        logger.info(
            f"[Traffic Light] Green: {green_score} | Red: {red_score} | "
            f"Signal: {signal.value}"
        )
        
        return signal
    
    def _log_signal(self, signal: IpoMarketSignal) -> None:
        """Ghi log tín hiệu chi tiết"""
        logger.info(
            f"\n"
            f"{'='*80}\n"
            f"IPO MARKET STRUCTURE SIGNAL | {signal.signal_date.strftime('%Y-%m-%d')}\n"
            f"{'='*80}\n"
            f"TẦNG 1 (ĐỊNH GIÁ):     Valuation Risk = {signal.valuation_risk_score:.1f}\n"
            f"TẦNG 2 (THANH KHOẢN):  Intensity={signal.ipo_intensity.value} | "
            f"Absorption={signal.capital_absorption_trend.value} | "
            f"Pressure={signal.secondary_market_pressure:.1f}\n"
            f"TẦNG 3 (LUÂN CHUYỂN):  Heat={signal.narrative_heat.value} | "
            f"Rotation Risk={signal.rotation_risk.value} | "
            f"Midcap Pressure={signal.midcap_smallcap_pressure:.1f}\n"
            f"TẦNG 4 (CHU KỲ):       Regime={signal.liquidity_regime.value} | "
            f"Confidence={signal.regime_confidence:.2f}\n"
            f"{'─'*80}\n"
            f"🟢🟡🔴 TÍN HIỆU:        {signal.traffic_light.value}\n"
            f"{'='*80}\n"
        )
