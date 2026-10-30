"""Test — trần/sàn `canonical/registry.py` cho kim loại quý (XAGUSD, GOLD_XAU).

[WHY] Ngày 25/10/2026, khi backfill full history `XAGUSD` (NQ-10 nhiệm vụ 1), phát
hiện `macro_history` có 2721 dòng nhưng `macro_history_v2` chỉ 2716: 5 ngày
2026-01-23..2026-01-29 (bạc 100,925 → 115,080) bị `Normalizer` từ chối vì trần
`XAGUSD` đặt 100. Cùng cơ chế, vàng `GOLD_XAU` trần 5.000 từ chối 52 dòng
(2026-01-26..2026-03-17, max 5.318,40) và v2 mất 25 dòng.

[ROOT CAUSE] Trần/sàn được đặt như "giá trị hợp lý" chứ không phải "vành đai chống
lỗi thang đơn vị", và không ai so lại khi thị trường vượt mức. Hậu quả không nổ ra
như một lỗi: dữ liệu vẫn nằm ở v1, chỉ lặng lẽ vắng mặt ở v2 — nơi
`uncertainty_layer`, `gold_h2_series`, `commodity_hit_rate` đọc. Mẫu số bị hụt
ĐÚNG đoạn biến động mạnh nhất, nên mọi thống kê GSR/beta giai đoạn đó bị lệch mà
không có cảnh báo nào.

[INVARIANT]
(1) Giá trị ĐÃ ĐO THẬT phải qua được cổng (bạc ≤ 115,080; vàng ≤ 5.318,40) —
    không được có chuyện "dữ liệu thật bị coi là rác".
(2) Cổng vẫn phải chặn lỗi thang đơn vị: bạc 5.294 (×~46 lần) / 404.000, vàng
    53.184 (×10) — đây là lý do trần KHÔNG được đặt thành "bỏ trần".
(3) Trần/sàn phải là số hữu hạn hiện hành, không None: test chống vá quá tay
    (`test_tran_san_khong_duoc_bo_trong`) khoá lại điều này.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "libs"))

from canonical import CanonicalAssetRegistry, Normalizer  # noqa: E402
from canonical.validator import ValidationError  # noqa: E402


@pytest.fixture()
def norm() -> Normalizer:
    """Normalizer mới (registry là singleton nhưng `get` chỉ đọc spec)."""
    return Normalizer()


def test_bac_nhan_gia_tri_da_do_thuc(norm):
    """[WHY] Ba mốc đo thật trên chuỗi SI=F 2016-2026: đáy 11,735 (2020-03),
    trung gian 51,460 (2026-10-29), đỉnh 115,080 (2026-01-26). Nếu bất kỳ mốc
    nào bị từ chối thì chuỗi v2 lại hụt dòng và phép GSR lại đọc mẫu cụt."""
    for value in (11.735, 51.46, 115.08):
        rec = norm.normalize("XAGUSD", "2026-01-26", value, "yahoo")
        assert rec.value == pytest.approx(value)


def test_vang_nhan_gia_tri_da_do_thuc(norm):
    """[WHY] 5.318,40 là đóng cửa cao nhất của GC=F (2026-01-26) — đúng phiên
    blow-off top mà trần cũ 5.000 từ chối, làm 52 dòng v1 không sang được v2."""
    for value in (1500.0, 4040.0, 5318.4):
        rec = norm.normalize("GOLD_XAU", "2026-01-26", value, "yahoo")
        assert rec.value == pytest.approx(value)


@pytest.mark.parametrize(
    "variable,bad_value",
    [
        ("XAGUSD", 5294.0),  # đơn vị VND/oz hoặc ×~46 — không thể là USD/oz
        ("XAGUSD", 404000.0),  # lỗi thang thô
        ("GOLD_XAU", 53184.0),  # ×10 (giá theo 10 oz thay vì 1 oz)
        ("GOLD_XAU", 404000.0),
    ],
)
def test_loi_thang_don_vi_van_bi_chan(norm, variable, bad_value):
    """[WHY] Chống vá quá tay: nới trần để nhận dữ liệu thật KHÔNG được biến cổng
    thành no-op. Lỗi thang đơn vị (×10, ×46) từng là bệnh thật của dự án (E19:
    DSN lệch 4,342% do scale-switch) — phải còn bắt được."""
    with pytest.raises(ValidationError) as exc:
        norm.normalize(variable, "2026-01-26", bad_value, "yahoo")
    assert "max" in str(exc.value)


def test_tran_san_khong_duoc_bo_trong():
    """[WHY] Một "cách sửa" bằng `max_value=None` cũng làm test trên xanh nhưng
    phá bất biến fail-closed. Khoá lại: cả hai biến phải có trần hữu hạn, và trần
    phải lớn hơn mức đo thật (không phải chỉnh xuống cho vừa bài test)."""
    reg = CanonicalAssetRegistry()
    for variable, observed_max in (("XAGUSD", 115.08), ("GOLD_XAU", 5318.4)):
        spec = reg.get(variable)
        assert spec.max_value is not None, f"{variable} mất trần ⇒ cổng thành no-op"
        assert spec.max_value > observed_max
