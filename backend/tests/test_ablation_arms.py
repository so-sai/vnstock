"""Guard cho Ablation Study — §7 của docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md.

Bốn bất biến phải giữ, nếu không thì study mất giá trị:

  1. **V4 = mặc định**: ô kiểm toàn vẹn harness phải TRÙNG hành vi mặc định của `trend_loop`.
     Nếu `ArmSwitches()` khác V4 thì "chạy baseline" và "chạy V4" là hai thứ khác nhau, và
     phép so V4-với-baseline (R1) mất nghĩa.
  2. **Bốn ô = bốn tổ hợp công tắc**: nếu hai ô trùng công tắc thì factorial 2×2 sụp còn 3 ô.
  3. **Không sao chép ngưỡng** (§6 R5): harness phải KHÔNG chứa bản sao của bất kỳ ngưỡng
     giao dịch nào. Bản sao ngưỡng là đường trôi tham số: sửa module niêm phong, harness vẫn
     chạy theo số cũ, và không ai phát hiện — kết quả "ablation" khi đó là của một chiến lược
     khác mà tên vẫn là baseline.
  4. **Công tắc phải là bool**: `ArmSwitches(organ=0.9)` là dấu hiệu một ngưỡng đã lọt vào.

KHÔNG kiểm R1 (V4 trùng baseline tới 12 chữ số) ở đây: nó cần chạy thật 2 lần × 5 năm (~3
phút) — thuộc `backend/scripts/run_ablation.py`, nơi R1 chạy TRƯỚC khi tính tác nhân và tự
tuyên bố study vô hiệu nếu lệch. Test này kiểm các bất biến rẻ và chạy được mọi lần commit.
"""

from __future__ import annotations

import ast
import sys
from itertools import product
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest.ablation_arms import ARMS, HARNESS_CONTROL_ARM, ArmSwitches  # noqa: E402

HARNESS_FILES = (
    PROJECT_ROOT / "backend" / "src" / "backtest" / "ablation_arms.py",
    PROJECT_ROOT / "backend" / "scripts" / "run_ablation.py",
)

#: Ngưỡng giao dịch của spec đã niêm phong — cấm xuất hiện như HẰNG SỐ trong harness.
SEALED_THRESHOLD_NAMES = frozenset(
    {
        "RS_MIN",
        "ADV20_MIN",
        "VOLUME_MULT",
        "BREAKOUT_LOOKBACK",
        "BASE_SIZE_PCT",
        "MAX_POSITIONS",
        "MAX_TREND_PCT",
        "BETA_CAP_PCT",
        "DISCOUNT_POLARITY",
        "DISCOUNT_LEVERAGED_GROWTH",
        "TRAILING_FULL_PCT",
        "TRAILING_PARTIAL_PCT",
        "VNINDEX_DROP_PCT",
        "RS_EXIT_LEVEL",
        "ATR_SPIKE_MULT",
        "REENTRY_COOLDOWN",
    }
)


def test_v4_la_hanh_vi_mac_dinh():
    """§7: mặc định `ArmSwitches()` PHẢI là V4, nếu không R1 vô nghĩa."""
    assert ARMS[HARNESS_CONTROL_ARM] == ArmSwitches()
    assert ARMS[HARNESS_CONTROL_ARM].organ is True
    assert ARMS[HARNESS_CONTROL_ARM].regime is True


def test_bon_o_la_bon_to_hop_cong_tac_khac_nhau():
    """§2: factorial 2×2 ⇒ đúng 4 tổ hợp, không ô nào trùng."""
    combos = {(a.organ, a.regime) for a in ARMS.values()}
    assert len(ARMS) == 4
    assert combos == set(product((False, True), repeat=2))
    # V1 phải là ô "tắt hết" — đó là đối chứng nền của study
    assert ARMS["V1_pure_momentum"] == ArmSwitches(organ=False, regime=False)


def test_khong_co_nguong_sao_chep_trong_harness():
    """§6 R5: harness không được định nghĩa lại ngưỡng giao dịch nào."""
    offenders: list[str] = []
    for path in HARNESS_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    name = getattr(tgt, "id", None)
                    if name and name.upper() in SEALED_THRESHOLD_NAMES:
                        offenders.append(f"{path.name}:{node.lineno} định nghĩa lại {name}")
    assert not offenders, (
        "harness sao chép ngưỡng đã niêm phong (trôi tham số §6 R5):\n  " + "\n  ".join(offenders)
    )


def test_cong_tac_phai_la_bool():
    """Công tắc nhận số/chuỗi là dấu hiệu ngưỡng đã lọt vào harness."""
    for bad in (1, 0, "true", 0.9, None):
        with pytest.raises(TypeError):
            ArmSwitches(organ=bad)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ArmSwitches(regime=bad)  # type: ignore[arg-type]


def test_mo_ta_cong_tac_dung_chieu():
    assert ArmSwitches(True, False).describe() == "organ=ON · regime=OFF"
    assert ArmSwitches(False, True).describe() == "organ=OFF · regime=ON"
