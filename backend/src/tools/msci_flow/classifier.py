"""Phân loại áp lực thanh khoản (DTL) và các cờ cảnh báo vi cấu trúc/nội tại.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Con số dòng tiền thuần (VND) vô nghĩa nếu không đặt cạnh thanh khoản 20 phiên
  (`DTL`), nút thắt room ngoại HOSE/HNX (`FOL`/`foreign_room`), hoặc bẫy sức khỏe
  nội tại (`CompanyHealthV2` / lợi nhuận âm).
ROOT CAUSE:
  Trộn lẫn cổ phiếu bị `DTL > 2` (bóp méo phiên ATC), cổ phiếu hết room ngoại
  (lực cầu ATC = 0), và cổ phiếu suy yếu (dòng tiền thụ động làm thanh khoản thoát
  hàng) sẽ dẫn đến kết luận sai lệch khi kiểm toán dòng tiền cơ cấu.
INVARIANT:
  1. Hàm thuần túy (pure function), không đọc/ghi DB hay mạng.
  2. Biên phân loại DTL chuẩn: `< 0.5 -> ABSORBED`, `0.5 <= DTL <= 2.0 -> ELEVATED`,
     `> 2.0 -> DISTORTED`.
"""

from __future__ import annotations

from .models import SymbolMarketSnapshot, SymbolSpec

# [WHY]: Lượng giao dịch ròng dưới 0.5 ngày ADV20 thường được hấp thụ trong biên độ ATC bình thường;
# sửa sẽ báo nhầm mã thanh khoản tốt thành áp lực cao.
DTL_ABSORBED_MAX = 0.5

# [WHY]: Từ 0.5 đến 2.0 ngày ADV20 tạo áp lực cung/cầu rõ rệt trong phiên cơ cấu, còn > 2.0 ngày có nguy cơ bóp méo;
# xóa sẽ mất tầng cảnh báo trung gian.
DTL_ELEVATED_MAX = 2.0

# [WHY]: Ngưỡng số học 0.0 cho kiểm tra âm/bằng 0 của dòng tiền, room ngoại, FOL và lợi nhuận;
# dùng hằng số rõ nghĩa tránh magic literal.
ZERO_VALUE = 0.0


def classify_dtl(dtl: float | None) -> str | None:
    """Phân loại Days-to-Liquidate (`DTL = |Estimated_Shares| / ADV20`).

    - `< 0.5` -> `"ABSORBED"`
    - `0.5 <= dtl <= 2.0` -> `"ELEVATED"`
    - `> 2.0` -> `"DISTORTED"`
    """
    if dtl is None:
        return None
    if dtl < DTL_ABSORBED_MAX:
        return "ABSORBED"
    if dtl <= DTL_ELEVATED_MAX:
        return "ELEVATED"
    return "DISTORTED"


def compute_on_exchange_buyable_shares(
    spec: SymbolSpec,
    estimated_shares: float | None,
) -> float | None:
    """Tính số cổ phiếu quỹ ngoại thực sự mua được trên sàn khớp lệnh (fail-closed).

    - Nếu `estimated_shares is None` hoặc `estimated_shares <= 0` (lực bán/không cầu)
      -> không áp dụng trần room mua -> trả `None` (hoặc `0.0` nếu bị kẹt room chiều mua).
    - Nếu `fol == 0.0` hoặc `foreign_room_pct == 0.0` hoặc `available_room_shares == 0.0`
      khi đang có nhu cầu mua (`estimated_shares > 0`) -> trả `0.0`.
    - Nếu `available_room_shares` hiện hữu và `0 < available_room_shares < estimated_shares`
      -> trả `available_room_shares`.
    - Nếu không biết room ngoại (`available_room_shares is None` và `foreign_room_pct is None`)
      -> `None` (Missing > Fake).
    """
    if estimated_shares is None or estimated_shares <= ZERO_VALUE:
        return None
    if spec.fol is not None and spec.fol <= ZERO_VALUE:
        return ZERO_VALUE
    if spec.foreign_room_pct is not None and spec.foreign_room_pct <= ZERO_VALUE:
        return ZERO_VALUE
    if spec.available_room_shares is not None:
        return float(min(estimated_shares, max(ZERO_VALUE, spec.available_room_shares)))
    return None


def evaluate_flags(
    *,
    spec: SymbolSpec,
    snapshot: SymbolMarketSnapshot,
    net_flow_vnd: float | None,
    estimated_shares: float | None,
    has_smallcap_gap: bool = False,
    has_no_tracking_fund: bool = False,
) -> tuple[str, ...]:
    """Sinh bộ cờ phân loại rủi ro thanh khoản, room ngoại và sức khỏe nội tại."""
    flags: list[str] = []

    has_buy_demand = (
        spec.bucket in {"ADD_STD", "ADD_SC"}
        or (spec.bucket == "DOWNGRADE_STD_TO_SC" and spec.delta_weight_sc_pct is not None and spec.delta_weight_sc_pct > 0)
        or (estimated_shares is not None and estimated_shares > ZERO_VALUE)
    )

    if spec.fol is not None and spec.fol <= ZERO_VALUE:
        flags.append("FOL_CONSTRAINED")
        if has_buy_demand:
            flags.append("ATC_DEMAND_ZERO")

    room_zero = (spec.available_room_shares is not None and spec.available_room_shares <= ZERO_VALUE) or (
        spec.foreign_room_pct is not None and spec.foreign_room_pct <= ZERO_VALUE
    )
    if room_zero:
        if "FOL_CONSTRAINED" not in flags:
            flags.append("FOL_CONSTRAINED")
        flags.append("ROOM_CONSTRAINED")
        if has_buy_demand and "ATC_DEMAND_ZERO" not in flags:
            flags.append("ATC_DEMAND_ZERO")
    elif (
        spec.available_room_shares is not None
        and estimated_shares is not None
        and estimated_shares > ZERO_VALUE
        and spec.available_room_shares < estimated_shares
    ):
        flags.append("ROOM_CONSTRAINED")

    if spec.available_room_shares is None and spec.foreign_room_pct is None:
        flags.append("MISSING_FOREIGN_ROOM")

    if has_no_tracking_fund:
        flags.append("NO_TRACKING_FUND")
    if has_smallcap_gap:
        flags.append("UNVERIFIED_SMALLCAP_AUM")

    if snapshot.health_archetype is None and snapshot.net_income_vnd is None:
        flags.append("MISSING_FUNDAMENTAL_HEALTH")

    is_weak_fundamental = snapshot.health_archetype == "DISTRESSED" or (
        snapshot.net_income_vnd is not None and snapshot.net_income_vnd < ZERO_VALUE
    )
    if is_weak_fundamental and net_flow_vnd is not None and net_flow_vnd > ZERO_VALUE:
        flags.append("PASSIVE_EXIT_LIQUIDITY_TRAP")

    return tuple(flags)
