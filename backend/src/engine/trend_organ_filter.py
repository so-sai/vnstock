"""Trend Engine — Lọc cơ bản (Health + 3 organ) theo spec §3, §4.

Điều kiện vượt cổng (spec §4):
    Health ≠ DISTRESSED  ∧  BS ≥ 0.30  ∧  Eff ≥ 0.60  ∧  Moat ≥ 0.50

NGUYÊN TẮC PIT (point-in-time): organ của ngày T phải là organ tính từ báo cáo
ĐÃ CÔNG BỐ tính tới T. `CompanyHealthV2.analyze(symbol, target_date)` tự ánh xạ
target_date → quý (`_quarter_at`) nên không nhìn thấy tương lai. Backtest gọi
hàm này với ngày của từng phiên, KHÔNG dùng bảng organ "mới nhất".

MISSING > FAKE: mã không có dữ liệu tài chính ⇒ LOẠI kèm lý do, tuyệt đối không
coi organ vắng mặt là 0 (0 sẽ bị đọc thành "yếu" và loại nhầm vì lý do sai).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.financial.company_health_v2 import CompanyHealthV2, _quarter_at

# ── Ngưỡng ĐÃ KHÓA trong spec §4 — không được sửa (test guard canh) ──
BS_MIN = 0.30
EFF_MIN = 0.60
MOAT_MIN = 0.50
DISTRESSED = "DISTRESSED"

# Thứ tự vector organ do company_health_v2 phát ra: [Profit, Cash, BS, Eff, Moat].
# PIN lại thứ tự này: sai index ⇒ lọc nhầm organ mà KHÔNG báo lỗi (im lặng).
ORGAN_VECTOR_ORDER = ("Profit", "Cash", "BS", "Eff", "Moat")
_ORGAN_INDEX = {name: i for i, name in enumerate(ORGAN_VECTOR_ORDER)}


@dataclass(frozen=True)
class OrganVerdict:
    """Kết quả lọc + LÝ DO. reasons luôn có nội dung khi passed=False (auditability)."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    organs: dict[str, float] | None = None
    archetype: str | None = None
    archetype_confidence: float | None = None

    def __bool__(self) -> bool:
        return self.passed


def organ_dict(vector: list[float] | tuple[float, ...] | None) -> dict[str, float]:
    """Ánh xạ vector organ → dict có tên. Vector sai kích thước ⇒ raise (fail-closed)."""
    if vector is None:
        raise ValueError("vector organ rỗng — không được suy diễn, phải raise")
    if len(vector) != len(ORGAN_VECTOR_ORDER):
        raise ValueError(
            f"vector organ có {len(vector)} phần tử, kỳ vọng {len(ORGAN_VECTOR_ORDER)} "
            f"theo thứ tự {ORGAN_VECTOR_ORDER} — schema organ đã đổi, dừng lại thay vì đoán"
        )
    return {name: float(vector[_ORGAN_INDEX[name]]) for name in ORGAN_VECTOR_ORDER}


def evaluate_state(state: Any) -> OrganVerdict:
    """Chấm một HealthLatentState (hoặc None) theo ngưỡng spec §4."""
    if state is None:
        return OrganVerdict(False, ["KHÔNG có dữ liệu tài chính → loại (missing > fake)"])

    organs = organ_dict(getattr(state.organs, "vector", None))
    reasons: list[str] = []

    if state.archetype == DISTRESSED:
        reasons.append(f"Health = DISTRESSED (conf {state.archetype_confidence:.2f})")
    for organ, floor in (("BS", BS_MIN), ("Eff", EFF_MIN), ("Moat", MOAT_MIN)):
        if organs[organ] < floor:
            reasons.append(f"{organ} {organs[organ]:.3f} < {floor}")

    return OrganVerdict(
        passed=not reasons,
        reasons=reasons,
        organs=organs,
        archetype=state.archetype,
        archetype_confidence=state.archetype_confidence,
    )


class OrganFilter:
    """Lọc organ có cache theo (symbol, quý) — tránh tính lại 740 mã × 20 quý mỗi phiên.

    Cache theo QUÝ chứ không theo ngày: organ chỉ đổi khi có báo cáo mới, mà
    `_quarter_at` đã gom ngày về quý. Điều này KHÔNG phá PIT vì mọi ngày trong
    cùng một quý cho cùng kết quả từ cùng một bộ báo cáo.
    """

    def __init__(self, engine: CompanyHealthV2 | None = None) -> None:
        self._engine = engine or CompanyHealthV2()
        self._cache: dict[tuple[str, str], Any] = {}
        self.hits = 0
        self.misses = 0

    def state(self, symbol: str, target_date: str) -> Any:
        key = (symbol.upper(), _quarter_at(target_date))
        if key in self._cache:
            self.hits += 1
        else:
            self.misses += 1
            self._cache[key] = self._engine.analyze(symbol, target_date)
        return self._cache[key]

    def evaluate(self, symbol: str, target_date: str) -> OrganVerdict:
        return evaluate_state(self.state(symbol, target_date))
