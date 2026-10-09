"""Trend Engine — Position sizing (§6 FROZEN).

Nguồn: spec §6, nguyên văn.

    alloc(Macro) + alloc(Trend) + alloc(Track2) ≤ 100% NAV
    Max Trend allocation = 20% NAV
    Base size: 5% NAV / vị thế · tối đa 4 vị thế đồng thời
    Correlation cap: tổng beta-weighted exposure ≤ 25% NAV
    Position Discount Multiplier:
        POLARITY_MISMATCH                    → × 0.5
        Health == LEVERAGED_GROWTH (≠ DISTRESSED) → × 0.8

Fail-closed: khi KHÔNG đủ dư địa (hết slot, chạm trần Trend, chạm cap beta) hàm
trả về size 0 KÈM LÝ DO, không âm thầm cắt bớt rồi vẫn vào lệnh — "vào nhỏ hơn
kế hoạch" là một quyết định khác với "không vào", và phải được thấy rõ.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Ngưỡng ĐÃ KHÓA (spec §6) ──
BASE_SIZE_PCT = 5.0
MAX_POSITIONS = 4
MAX_TREND_PCT = 20.0
BETA_CAP_PCT = 25.0
DISCOUNT_POLARITY = 0.5
DISCOUNT_LEVERAGED_GROWTH = 0.8
LEVERAGED_GROWTH = "LEVERAGED_GROWTH"


@dataclass(frozen=True)
class SizeDecision:
    """target_pct = % NAV của vị thế; 0 nghĩa là KHÔNG vào, và reasons luôn giải thích."""

    target_pct: float
    reasons: list[str] = field(default_factory=list)
    discount: float = 1.0


def discount_multiplier(*, polarity_mismatch: bool = False, archetype: str | None = None) -> tuple[float, list[str]]:
    """Hệ số chiết khấu theo §6. Nhân dồn nếu cả hai điều kiện cùng đúng."""
    mult = 1.0
    notes: list[str] = []
    if polarity_mismatch:
        mult *= DISCOUNT_POLARITY
        notes.append(f"POLARITY_MISMATCH → ×{DISCOUNT_POLARITY}")
    if archetype == LEVERAGED_GROWTH:
        mult *= DISCOUNT_LEVERAGED_GROWTH
        notes.append(f"Health == LEVERAGED_GROWTH → ×{DISCOUNT_LEVERAGED_GROWTH}")
    return mult, notes


def position_size(
    *,
    nav: float,
    in_trend_pct: float,
    open_positions: int,
    polarity_mismatch: bool = False,
    archetype: str | None = None,
    beta: float = 1.0,
    beta_weighted_pct: float = 0.0,
) -> SizeDecision:
    """Kích thước mục tiêu cho MỘT vị thế mới, đã tính mọi trần của §6.

    nav                     : NAV hiện tại (đơn vị tiền)
    in_trend_pct            : % NAV đang nằm trong Trend sleeve
    open_positions          : số vị thế Trend đang mở
    beta / beta_weighted_pct: beta của mã và tổng beta-weighted exposure hiện có (% NAV)
    """
    if nav <= 0:
        raise ValueError(f"NAV phải > 0, nhận {nav}")

    reasons: list[str] = []
    if open_positions >= MAX_POSITIONS:
        return SizeDecision(0.0, [f"đã đủ {MAX_POSITIONS} vị thế đồng thời (§6)"], 1.0)

    mult, notes = discount_multiplier(polarity_mismatch=polarity_mismatch, archetype=archetype)
    size = BASE_SIZE_PCT * mult

    # Trần 1: tổng Trend ≤ 20% NAV
    room = MAX_TREND_PCT - in_trend_pct
    if room <= 0:
        return SizeDecision(0.0, [f"Trend đã chạm trần {MAX_TREND_PCT}% NAV (§6)", *notes], mult)
    if size > room:
        reasons.append(f"cắt theo trần Trend: còn {room:.2f}% (base {size:.2f}% → {room:.2f}%)")
        size = room

    # Trần 2: tổng beta-weighted exposure ≤ 25% NAV
    beta_room_pct = (BETA_CAP_PCT - beta_weighted_pct) / beta if beta > 0 else 0.0
    if beta_room_pct <= 0:
        return SizeDecision(0.0, [f"beta-weighted đã chạm cap {BETA_CAP_PCT}% NAV (§6)", *notes], mult)
    if size * beta > BETA_CAP_PCT - beta_weighted_pct:
        allow = beta_room_pct
        reasons.append(f"cắt theo cap beta: còn {BETA_CAP_PCT - beta_weighted_pct:.2f}% ÷ beta {beta:.2f} = {allow:.2f}%")
        size = min(size, allow)

    return SizeDecision(round(size, 6), [*notes, *reasons], mult)
