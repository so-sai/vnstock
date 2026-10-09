"""Trend Engine — Tín hiệu vào lệnh (§4 ENTRY RULES FROZEN) + điều phối ra lệnh.

Nguồn: spec §4, nguyên văn:

    TREND_BUY ⟺
        RS ≥ 90                          # momentum
      ∧ ADV20 ≥ 500.000                  # thanh khoản
      ∧ Breakout nền 20 phiên            # cấu trúc
      ∧ Volume ≥ 1.3 × ADV20             # xác nhận
      ∧ Health ≠ DISTRESSED              # cơ bản
      ∧ BS  ≥ 0.30 / Eff ≥ 0.60 / Moat ≥ 0.50
      ∧ nguồn_chặn ∈ {WORLD, MACRO}      # KHÔNG phải suy thoái nội tại

Breakout (spec §4): `Close > max(High[1:20])` AND `Close > MA20`.
Diễn giải `High[1:20]`: đỉnh của 20 phiên TRƯỚC phiên hiện tại. Bao gồm cả phiên
hiện tại sẽ làm điều kiện breakout thành vô nghĩa (close không bao giờ > high của
chính nó) — đây là chỗ dễ sai nhất và được pin bằng test.

Module này THUẦN: nhận `TrendFeatures` đã dựng sẵn, không đọc DB. Nhờ vậy kiểm
được bằng unit test và không thể âm thầm nhìn vào tương lai.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.engine.trend_organ_filter import OrganVerdict

# ── Ngưỡng ĐÃ KHÓA (spec §4) ──
RS_MIN = 90.0
ADV20_MIN = 500_000.0  # cổ phiếu
VOLUME_MULT = 1.3
BREAKOUT_LOOKBACK = 20
ALLOWED_BLOCK_SOURCES = frozenset({"NONE", "WORLD", "MACRO"})

UPPER_BOUND_ONLY = "903"  # cận trên lý thuyết về giá — KHÔNG phải universe (§3.1 + ERRATUM 1)


@dataclass(frozen=True)
class TrendFeatures:
    """Đặc trưng của MỘT mã tại MỘT phiên, đã tính PIT ở tầng dữ liệu."""

    symbol: str
    date: str
    close: float
    high_20: float  # max(High) của 20 phiên TRƯỚC phiên này
    ma20: float
    volume: float
    adv20: float
    rs: float
    block_source: str = "NONE"  # NONE | WORLD | MACRO | INTERNAL
    organ: OrganVerdict | None = None


@dataclass(frozen=True)
class Gate:
    """Kết quả một cổng. reasons LUÔN có nội dung khi passed=False."""

    passed: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


def entry_verdict(f: TrendFeatures) -> Gate:
    """Chấm ĐỦ 9 điều kiện §4. Trả về mọi lý do trượt, không dừng ở lý do đầu."""
    reasons: list[str] = []

    if f.rs < RS_MIN:
        reasons.append(f"RS {f.rs:.1f} < {RS_MIN:.0f}")
    if f.adv20 < ADV20_MIN:
        reasons.append(f"ADV20 {f.adv20:,.0f} < {ADV20_MIN:,.0f} cp")
    if not (f.close > f.high_20):
        reasons.append(f"chưa breakout: close {f.close:,.2f} ≤ đỉnh 20 phiên {f.high_20:,.2f}")
    if not (f.close > f.ma20):
        reasons.append(f"close {f.close:,.2f} ≤ MA20 {f.ma20:,.2f}")
    if not volume_confirms(f.volume, f.adv20):
        reasons.append(f"volume {f.volume:,.0f} < {VOLUME_MULT}×ADV20 ({VOLUME_MULT * f.adv20:,.0f})")

    if f.organ is None:
        # Không có verdict organ ⇒ KHÔNG được coi là đạt. missing > fake.
        reasons.append("thiếu verdict organ → loại (missing > fake)")
    elif not f.organ.passed:
        reasons.extend(f"organ: {r}" for r in f.organ.reasons)

    if f.block_source not in ALLOWED_BLOCK_SOURCES:
        reasons.append(f"nguồn_chặn = {f.block_source} ∉ {{WORLD, MACRO}} — bị chặn vì suy thoái nội tại")

    return Gate(passed=not reasons, reasons=reasons)


def volume_confirms(volume: float, adv20: float) -> bool:
    """Tách riêng để test được: Volume ≥ 1.3 × ADV20."""
    return volume >= VOLUME_MULT * adv20


def is_breakout(close: float, high_20: float, ma20: float) -> bool:
    """`Close > max(High[1:20])` AND `Close > MA20` (spec §4)."""
    return close > high_20 and close > ma20
