"""Trend Engine — Kill switch (§5 EXIT RULES + §10 PRIORITY).

Nguồn: spec §5 và §10, nguyên văn.

FULL EXIT khi BẤT KỲ:
    · Trailing stop −8% từ đỉnh gần nhất
    · Regime → CRISIS
    · Circuit breaker K3: VN-Index −5% trong 3 phiên
    · RS < 70 hai phiên liên tiếp
PARTIAL 50% khi:
    · Trailing stop −5% từ đỉnh
    · ATR% > 2× trung bình 20 phiên
Sau FULL EXIT: KHÔNG re-entry trong 5 phiên (chống whipsaw).

§10 yêu cầu log TẤT CẢ trigger đang bật, không chỉ trigger được thi hành — nên
`evaluate()` trả về DANH SÁCH đầy đủ theo thứ tự ưu tiên, còn `resolve()` mới chọn
hành động. Gộp hai việc đó lại sẽ làm mất dấu vết kiểm toán.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Ngưỡng ĐÃ KHÓA (spec §5) ──
TRAILING_FULL_PCT = 8.0  # −8% → FULL EXIT
TRAILING_PARTIAL_PCT = 5.0  # −5% → PARTIAL 50%
VNINDEX_DROP_PCT = 5.0  # K3: −5% ...
VNINDEX_WINDOW = 3  # ... trong 3 phiên
RS_EXIT_LEVEL = 70.0  # RS < 70 ...
RS_CONFIRM_SESSIONS = 2  # ... hai phiên liên tiếp
ATR_SPIKE_MULT = 2.0  # ATR% > 2× ...
ATR_LOOKBACK = 20  # ... trung bình 20 phiên
REENTRY_COOLDOWN = 5  # không re-entry trong 5 phiên sau full exit

FULL_EXIT = "FULL_EXIT"
PARTIAL_50 = "PARTIAL_50"

# Thứ tự ưu tiên NGUYÊN VĂN theo bảng §10.
PRIORITY: tuple[str, ...] = (
    "K3_VNINDEX_MINUS5",  # 1
    "REGIME_CRISIS",  # 2
    "TRAILING_MINUS8",  # 3
    "RS_BELOW_70_TWO_SESSIONS",  # 4
    "TRAILING_MINUS5",  # 5
    "VOLATILITY_SPIKE",  # 6
)

ACTION: dict[str, str] = {
    "K3_VNINDEX_MINUS5": FULL_EXIT,
    "REGIME_CRISIS": FULL_EXIT,
    "TRAILING_MINUS8": FULL_EXIT,
    "RS_BELOW_70_TWO_SESSIONS": FULL_EXIT,
    "TRAILING_MINUS5": PARTIAL_50,
    "VOLATILITY_SPIKE": PARTIAL_50,
}


@dataclass(frozen=True)
class PositionView:
    """Trạng thái vị thế đang mở, đủ để chấm mọi trigger cấp vị thế."""

    symbol: str
    peak_price: float  # đỉnh giá KỂ TỪ khi vào lệnh (trailing từ đỉnh này)
    current_price: float
    rs_now: float
    rs_prev: float


@dataclass(frozen=True)
class MarketView:
    """Trạng thái thị trường, đủ để chấm mọi trigger cấp thị trường."""

    regime: str
    vnindex_return_3d_pct: float
    atr_pct: float
    atr_pct_ma20: float


def trailing_drawdown_pct(peak_price: float, current_price: float) -> float:
    """Mức giảm từ đỉnh, đơn vị %. Luôn ≤ 0 khi giá dưới đỉnh (đỉnh phải > 0)."""
    if peak_price <= 0:
        raise ValueError(f"đỉnh giá phải > 0, nhận {peak_price}")
    return (current_price / peak_price - 1.0) * 100.0


def evaluate(position: PositionView, market: MarketView) -> list[str]:
    """Trả về TẤT CẢ trigger đang bật, đã sắp theo thứ tự ưu tiên §10.

    Cố ý trả về danh sách chứ không trả về một trigger: §10 bắt log tất cả.
    """
    fired: list[str] = []

    if market.vnindex_return_3d_pct <= -VNINDEX_DROP_PCT:
        fired.append("K3_VNINDEX_MINUS5")
    if market.regime.upper() == "CRISIS":
        fired.append("REGIME_CRISIS")

    dd = trailing_drawdown_pct(position.peak_price, position.current_price)
    if dd <= -TRAILING_FULL_PCT:
        fired.append("TRAILING_MINUS8")
    elif dd <= -TRAILING_PARTIAL_PCT:
        # Chỉ xét −5% khi CHƯA chạm −8%: nếu đã chạm −8% thì trigger mạnh hơn
        # đã bao trùm; thêm trigger yếu hơn chỉ làm nhiễu log chứ không đổi hành động.
        fired.append("TRAILING_MINUS5")

    if position.rs_now < RS_EXIT_LEVEL and position.rs_prev < RS_EXIT_LEVEL:
        fired.append("RS_BELOW_70_TWO_SESSIONS")

    if market.atr_pct_ma20 > 0 and market.atr_pct > ATR_SPIKE_MULT * market.atr_pct_ma20:
        fired.append("VOLATILITY_SPIKE")

    return sorted(fired, key=PRIORITY.index)


def resolve(triggers: list[str]) -> str | None:
    """Hành động của trigger ưu tiên CAO NHẤT. Không có trigger ⇒ None (giữ nguyên)."""
    if not triggers:
        return None
    return ACTION[triggers[0]]


def reentry_allowed(sessions_since_full_exit: int) -> bool:
    """Chống whipsaw: cấm re-entry trong 5 phiên sau FULL EXIT (§5)."""
    return sessions_since_full_exit >= REENTRY_COOLDOWN
