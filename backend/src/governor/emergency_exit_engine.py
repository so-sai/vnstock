"""emergency_exit_engine.py — Cơ chế Thanh lý Khẩn cấp (Emergency Exit Engine).

v2: ADV Liquidity-Aware Execution (2026-08-06)

Vai trò:
  Khi LRI < 0.30 (DEFENSIVE), kích hoạt quy trình thanh lý có quản trị:
  1. Khóa lệnh mua (BUY LOCK)
  2. Thắt chặt Trailing Stop (-5.0% → -2.0%)
  3. Thứ tự thanh lý ưu tiên (High Beta / Low MoS → bán trước)
  4. **ADV-aware order slicing** — chia nhỏ lệnh khi vị thế > 15% ADV_20D

Nguyên tắc:
  - Không bán tháo hỗn loạn (no naive market dumping)
  - Ưu tiên thanh lý cổ phiếu rủi ro cao trước (high beta, low MoS)
  - Giữ lại cổ phiếu thanh khoản cao / bluechip đến cuối
  - Kích hoạt TWAP slicing khi vị thế vượt 15% ADV (tránh slippage)
"""

import logging
import math
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ExitPriority(str, Enum):
    """Mức độ ưu tiên thanh lý."""

    CRITICAL = "CRITICAL"  # Bán ngay lập tức (High beta + Low MoS)
    HIGH = "HIGH"  # Bán sớm trong phiên
    MEDIUM = "MEDIUM"  # Bán trong 1-2 phiên
    LOW = "LOW"  # Giữ lại nếu có thể (Bluechip, thanh khoản cao)


@dataclass
class ExitOrder:
    """Lệnh thanh lý khẩn cấp cho một cổ phiếu."""

    symbol: str
    action: str = "EMERGENCY_SELL"
    priority: ExitPriority = ExitPriority.MEDIUM
    shares: int = 0
    tightened_stop_pct: float = -0.02  # Siết từ -5% xuống -2%
    reason: str = ""
    beta: float = 1.0
    mos_pct: float = 0.0
    # ADV liquidity-aware fields
    volume_avg_20d: float = 0.0
    is_sliced: bool = False
    max_order_shares: int = 0
    estimated_days: int = 1
    position_pct_of_adv: float = 0.0


@dataclass
class EmergencyExitResult:
    """Kết quả của quy trình thanh lý khẩn cấp."""

    is_defensive: bool
    lri_score: float
    buy_locked: bool
    exit_orders: list[ExitOrder] = field(default_factory=list)
    tightened_stops: dict = field(default_factory=dict)  # symbol → new_stop_pct
    total_positions: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    sliced_count: int = 0  # Number of orders requiring TWAP slicing


class EmergencyExitEngine:
    """Cơ chế Thanh lý Khẩn cấp khi LRI < 0.30.

    Third layer of defense after:
      - LRI Regime (DEFENSIVE → Max Allocation = 0%)
      - Sector Gate (50% cap per sector)

    This engine handles the EXECUTION side:
      - When to exit (DEFENSIVE trigger)
      - What to exit first (Beta/MoS ranking)
      - How to exit (tightened stops, ADV-aware slicing)
    """

    DEFENSIVE_THRESHOLD = 0.30
    NORMAL_STOP_PCT = -0.05  # Normal trailing stop: -5%
    TIGHTENED_STOP_PCT = -0.02  # Emergency trailing stop: -2%

    # ADV thresholds
    ADV_SLICE_THRESHOLD = 0.15  # Position > 15% ADV → activate TWAP slicing
    ADV_DAILY_LIMIT_PCT = 0.10  # Sell max 10% ADV per day when slicing

    def evaluate(
        self,
        lri_score: float,
        open_positions: dict | None = None,
    ) -> EmergencyExitResult:
        """Đánh giá danh mục và tạo kế hoạch thanh lý khẩn cấp.

        Args:
            lri_score: Điểm LRI hiện tại
            open_positions: Dict[symbol → {shares, entry_price, beta, mos_pct, volume_avg_20d, ...}]

        Returns:
            EmergencyExitResult với exit orders và tightened stops
        """
        is_defensive = lri_score < self.DEFENSIVE_THRESHOLD
        buy_locked = is_defensive

        result = EmergencyExitResult(
            is_defensive=is_defensive,
            lri_score=lri_score,
            buy_locked=buy_locked,
        )

        if not is_defensive or not open_positions:
            return result

        logger.warning(
            "[EMERGENCY_EXIT] LRI=%.4f < %.2f DEFENSIVE — initiating managed exit protocol",
            lri_score,
            self.DEFENSIVE_THRESHOLD,
        )

        # Rank positions by exit priority (High Beta + Low MoS = sell first)
        ranked = self._rank_positions(open_positions)
        result.total_positions = len(ranked)

        for symbol, pos_data, priority in ranked:
            # Tighten stop loss
            new_stop = self.TIGHTENED_STOP_PCT
            result.tightened_stops[symbol] = new_stop

            # Compute ADV-aware execution
            shares = pos_data.get("shares", 0)
            adv_20d = pos_data.get("volume_avg_20d", 0.0)
            exec_plan = self.compute_exit_execution(shares, adv_20d)

            order = ExitOrder(
                symbol=symbol,
                priority=priority,
                shares=shares,
                tightened_stop_pct=new_stop,
                reason=self._exit_reason(priority, pos_data, exec_plan),
                beta=pos_data.get("beta", 1.0),
                mos_pct=pos_data.get("mos_pct", 0.0),
                volume_avg_20d=adv_20d,
                is_sliced=exec_plan["is_sliced"],
                max_order_shares=exec_plan["max_order_shares"],
                estimated_days=exec_plan["estimated_days"],
                position_pct_of_adv=exec_plan["position_pct_of_adv"],
            )
            result.exit_orders.append(order)

            if exec_plan["is_sliced"]:
                result.sliced_count += 1

            # Count by priority
            if priority == ExitPriority.CRITICAL:
                result.critical_count += 1
            elif priority == ExitPriority.HIGH:
                result.high_count += 1
            elif priority == ExitPriority.MEDIUM:
                result.medium_count += 1
            else:
                result.low_count += 1

        logger.warning(
            "[EMERGENCY_EXIT] %d orders: %d CRITICAL, %d HIGH, %d MEDIUM, %d LOW (%d sliced)",
            len(result.exit_orders),
            result.critical_count,
            result.high_count,
            result.medium_count,
            result.low_count,
            result.sliced_count,
        )

        return result

    def compute_exit_execution(self, position_shares: int, adv_20d: float) -> dict:
        """Tính toán quy mô xả lệnh an toàn dựa trên ADV 20 phiên.

        Args:
            position_shares: Số cổ phiếu đang nắm giữ
            adv_20d: Khối lượng giao dịch trung bình 20 phiên

        Returns:
            dict with: max_order_shares, is_sliced, estimated_days, position_pct_of_adv
        """
        if adv_20d <= 0 or position_shares <= 0:
            return {
                "max_order_shares": position_shares,
                "is_sliced": False,
                "estimated_days": 1,
                "position_pct_of_adv": 0.0,
            }

        position_pct_of_adv = position_shares / adv_20d

        # If position > 15% ADV → activate TWAP slicing
        if position_pct_of_adv > self.ADV_SLICE_THRESHOLD:
            safe_daily_limit = max(1, int(adv_20d * self.ADV_DAILY_LIMIT_PCT))
            estimated_days = math.ceil(position_shares / safe_daily_limit)
            return {
                "max_order_shares": min(position_shares, safe_daily_limit),
                "is_sliced": True,
                "estimated_days": estimated_days,
                "position_pct_of_adv": round(position_pct_of_adv, 4),
            }

        return {
            "max_order_shares": position_shares,
            "is_sliced": False,
            "estimated_days": 1,
            "position_pct_of_adv": round(position_pct_of_adv, 4),
        }

    def _rank_positions(self, positions: dict) -> list[tuple[str, dict, ExitPriority]]:
        """Xếp hạng vị thế theo thứ tự thanh lý ưu tiên.

        Ranking logic (sell first = highest priority):
          1. Beta > 1.5 AND MoS < 20% → CRITICAL
          2. Beta > 1.2 OR MoS < 30%  → HIGH
          3. Beta > 0.8 AND MoS < 50% → MEDIUM
          4. Everything else            → LOW (bluechip, hold if possible)
        """
        ranked = []
        for symbol, pos_data in positions.items():
            beta = pos_data.get("beta", 1.0)
            mos = pos_data.get("mos_pct", 0.0)

            if beta > 1.5 and mos < 20:
                priority = ExitPriority.CRITICAL
            elif beta > 1.2 or mos < 30:
                priority = ExitPriority.HIGH
            elif beta > 0.8 and mos < 50:
                priority = ExitPriority.MEDIUM
            else:
                priority = ExitPriority.LOW

            ranked.append((symbol, pos_data, priority))

        # Sort: CRITICAL first, then HIGH, MEDIUM, LOW
        priority_order = {ExitPriority.CRITICAL: 0, ExitPriority.HIGH: 1, ExitPriority.MEDIUM: 2, ExitPriority.LOW: 3}
        ranked.sort(key=lambda x: priority_order[x[2]])

        return ranked

    def _exit_reason(self, priority: ExitPriority, pos_data: dict, exec_plan: dict) -> str:
        """Tạo lý do thanh lý cho mỗi lệnh."""
        beta = pos_data.get("beta", 1.0)
        mos = pos_data.get("mos_pct", 0.0)
        adv = pos_data.get("volume_avg_20d", 0.0)

        base = ""
        if priority == ExitPriority.CRITICAL:
            base = f"HIGH RISK: Beta={beta:.2f}, MoS={mos:.1f}%"
        elif priority == ExitPriority.HIGH:
            base = f"ELEVATED RISK: Beta={beta:.2f} or MoS={mos:.1f}%"
        elif priority == ExitPriority.MEDIUM:
            base = f"MODERATE RISK: Beta={beta:.2f}, MoS={mos:.1f}%"
        else:
            base = f"LOW RISK: Beta={beta:.2f}, MoS={mos:.1f}%"

        if exec_plan["is_sliced"]:
            pct = exec_plan["position_pct_of_adv"]
            days = exec_plan["estimated_days"]
            daily = exec_plan["max_order_shares"]
            return f"{base} — TWAP SLICING: {daily}/day ({days}d), Pos/{adv:.0f}ADV={pct:.1%}"
        return f"{base} — full exit"
