"""ablation_arms.py — hai công tắc của Ablation Study.

Spec: `docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md` (v1.0, pre-registered, DIAGNOSTIC ONLY).

BA ĐIỀU MODULE NÀY CỐ Ý KHÔNG LÀM
---------------------------------
  1. **Không chứa ngưỡng nào.** Mọi con số (RS_MIN, ADV20_MIN, TRAILING_FULL…) import từ
     module đã niêm phong. Bản sao ngưỡng nằm trong harness là đường trôi tham số: sửa một
     chỗ, chỗ kia vẫn chạy như cũ, và không ai phát hiện. (§6 R5)
  2. **Không promote.** Không hàm nào ở đây được gọi từ `final-decision`/live. (§6 R4)
  3. **Không mở rộng phạm vi.** Chỉ hai công tắc. Thêm công tắc thứ ba = đổi thiết kế
     study ⇒ phải sửa spec + hash trước, không phải thêm vào code.

Ô kiểm chứng harness là **V4**: khác baseline đúng 0 công tắc, nên phải tái lập baseline
tới 12 chữ số. Lệch ⇒ study vô hiệu, không diễn giải ô nào (§6 R1).
"""

from __future__ import annotations

from dataclasses import dataclass

ORGAN_MODE_FULL = "full"
ORGAN_MODE_DISTRESSED_ONLY = "distressed_only"
VALID_ORGAN_MODES = frozenset({ORGAN_MODE_FULL, ORGAN_MODE_DISTRESSED_ONLY})


@dataclass(frozen=True)
class ArmSwitches:
    """Hai công tắc của factorial 2×2, cộng (v1.1) chế độ của cổng organ.

    Momentum LUÔN bật (không phải công tắc).
    """

    organ: bool = True
    regime: bool = True
    #: `full` = nguyên cổng organ đã niêm phong (BS/Eff/Moat + DISTRESSED).
    #: `distressed_only` = chỉ `archetype != "DISTRESSED"` (V5, §2b của spec v1.1).
    organ_mode: str = ORGAN_MODE_FULL

    def __post_init__(self) -> None:
        for name, val in (("organ", self.organ), ("regime", self.regime)):
            if not isinstance(val, bool):
                raise TypeError(
                    f"công tắc `{name}` phải là bool, nhận {type(val).__name__}. "
                    "Giá trị không-bool ở đây là dấu hiệu một ngưỡng đã lọt vào harness (§6 R5)."
                )
        if self.organ_mode not in VALID_ORGAN_MODES:
            raise ValueError(f"organ_mode phải thuộc {sorted(VALID_ORGAN_MODES)}, nhận {self.organ_mode!r}")
        if not self.organ and self.organ_mode != ORGAN_MODE_FULL:
            raise ValueError(
                "organ=False mà organ_mode != 'full' là cấu hình tự mâu thuẫn: "
                "đã tắt cổng thì không còn 'chế độ' nào để nói tới."
            )

    def describe(self) -> str:
        base = f"organ={'ON' if self.organ else 'OFF'} · regime={'ON' if self.regime else 'OFF'}"
        if self.organ and self.organ_mode != ORGAN_MODE_FULL:
            base += f" · organ_mode={self.organ_mode}"
        return base


ARMS: dict[str, ArmSwitches] = {
    "V1_pure_momentum": ArmSwitches(organ=False, regime=False),
    "V2_momentum_regime": ArmSwitches(organ=False, regime=True),
    "V3_momentum_organ": ArmSwitches(organ=True, regime=False),
    "V4_full_baseline": ArmSwitches(organ=True, regime=True),
    # v1.1 — KHÔNG thuộc factorial 2×2; thay cổng organ khắt khe bằng "chỉ chặn DISTRESSED".
    "V5_distressed_only": ArmSwitches(organ=True, regime=True, organ_mode=ORGAN_MODE_DISTRESSED_ONLY),
}

#: Ô dùng để kiểm toàn vẹn harness — PHẢI trùng baseline tới 12 chữ số (§6 R1).
HARNESS_CONTROL_ARM = "V4_full_baseline"

#: Bốn ô của factorial 2×2 (dùng cho test "4 ô = 4 tổ hợp công tắc").
FACTORIAL_ARMS = ("V1_pure_momentum", "V2_momentum_regime", "V3_momentum_organ", "V4_full_baseline")

__all__ = [
    "ARMS",
    "FACTORIAL_ARMS",
    "HARNESS_CONTROL_ARM",
    "ORGAN_MODE_DISTRESSED_ONLY",
    "ORGAN_MODE_FULL",
    "VALID_ORGAN_MODES",
    "ArmSwitches",
]
