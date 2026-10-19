"""Test tầng tính điểm của `valuation_engine`: winsorize [p2,p98] + Luật A (n_min=5, std≤0) + biên ROE.

[WHY]
  Hạng mục E ký duyệt 2026-10-16 (chủ hệ thống): (1) `ratio_value` là SỰ THẬT SỔ SÁCH nên KHÔNG được
  ghi đè — chỉ kẹp biên ở TẦNG TÍNH TOÁN (z/percentile) và gắn `OUTLIER_WINSORED`; (2) `n < 5` hoặc
  `std ≤ 0` ⇒ `z_score = NULL` + `INSUFFICIENT_HISTORY` (chấm dứt việc bịa `0.0`); (3) `|ROE| > 1,5`
  ⇒ `EXTREME_ROE_ANOMALY` + `is_clean_metric = 0`.

[ROOT CAUSE]
  Đo trên bảng 56.055 dòng: 3.444 dòng có `std = 0`, trong đó **3.433 dòng `z_score = 0.0`** — "trạng
  thái trung tính" ngụy tạo (Luật A: *không biết ≠ 0*). Và phép thử bác bỏ cho thấy đuôi cực trị làm
  lạm phát phương sai PE 8,60× · PB 4,50× · PS 16,13× · ROE 2,28× ⇒ phải kẹp biên (chỉ ở tầng điểm).

[INVARIANT]
  1. `z = None` khi `count < MIN_HISTORY` HOẶC `std <= 0`; KHÔNG BAO GIỜ trả 0.0 thay cho "không biết".
  2. Winsorize chỉ ảnh hưởng ĐẦU VÀO của z/percentile; giá trị thô (tham số `val`) không bị sửa.
  3. Kẹp biên chỉ xảy ra khi cửa sổ đủ dài (≥ 5 kỳ); cửa sổ ngắn ⇒ không kẹp (p2/p98 vô nghĩa).
  4. Cờ `EXTREME_ROE_ANOMALY` có phạm vi ROE và CHẶN xếp hạng (`is_clean_metric = 0`).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.financial.valuation_engine import ValuationEngine  # noqa: E402


@pytest.fixture(scope="module")
def engine() -> ValuationEngine:
    return ValuationEngine()


# [WHY] Luật A — ca thật đã đo: 3.433 dòng bịa z=0.0 khi std=0. Chuỗi toàn giá trị bằng nhau (mẫu số
# z bằng 0) KHÔNG được coi là "trung tính": phải là NULL + cờ.
def test_std_bang_0_tra_none_va_co_insufficient(engine):
    window = [5.0] * 10
    z, pct, zone, winsorized, insufficient = engine._score_point("PE", window, 5.0)
    assert z is None and pct is None and zone is None
    assert insufficient is True
    assert winsorized is False


# [WHY] n_min = 5: dưới 5 kỳ thì z không có ý nghĩa thống kê — thà thiếu (NULL) còn hơn một con số z
# dựng trên 2–3 quý ngẫu nhiên.
@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_duoi_5_ky_tra_none(engine, n):
    window = [float(i + 1) for i in range(n)]
    z, _pct, _zone, _w, insufficient = engine._score_point("PE", window, window[-1])
    assert z is None and insufficient is True


# [WHY] Winsorize phải KẸP đầu vào nhưng KHÔNG sửa giá trị thô: z tính trên giá trị đã kẹp về p98, và
# cờ `winsorized` bật; giá trị truyền vào (đại diện `ratio_value`) không bị hàm làm thay đổi.
def test_winsorize_kep_dau_vao_khong_sua_gia_tri_tho(engine):
    window = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    val = 1000.0
    z, pct, zone, winsorized, insufficient = engine._score_point("PE", window + [val], val)
    assert winsorized is True and insufficient is False
    assert z is not None and z < 100  # kẹp về p98 (~10) chứ không phải 1000
    assert pct is not None and pct >= 90
    assert val == 1000.0, "giá trị thô bị sửa ⇒ vi phạm 'ratio_value là sự thật sổ sách'"


# [WHY] Không kẹp ở cửa sổ ngắn: p2/p98 của 3–4 điểm là vô nghĩa, kẹp sẽ tạo cảm giác đã xử lý ngoại lai
# trong khi thực chất chỉ là nhiễu.
def test_cua_so_ngan_khong_kep(engine):
    window = [1.0, 2.0, 3.0, 4.0]
    _z, _p, _zone, winsorized, insufficient = engine._score_point("PE", window, 3.0)
    assert winsorized is False and insufficient is True


# [WHY] Cận phải là p2/p98 thật của cửa sổ (nội suy), không phải min/max — nếu dùng min/max thì "kẹp"
# chẳng loại được ngoại lai nào ở cửa sổ lớn.
def test_winsor_bounds_la_p2_p98_khong_phai_min_max(engine):
    window = [float(i) for i in range(1, 101)]  # 1..100
    lo, hi = engine._winsor_bounds(window)
    assert 2.0 < lo < 4.0, lo  # p2 ≈ 2,98
    assert 97.0 < hi < 99.0, hi  # p98 ≈ 98,02
    assert engine._winsor_bounds([1.0, 2.0]) == (None, None)


# [WHY] Biên ROE là quyết định ĐÃ KÝ: |ROE| > 1,5 là mất vốn chủ/near-zero equity, không phải hiệu quả
# siêu việt ⇒ phải có cờ + loại khỏi xếp hạng. Cờ phải có PHẠM VI ROE để không tước tư cách của PE/PB.
def test_co_bien_roe_ton_tai_va_dung_pham_vi():
    assert "EXTREME_ROE_ANOMALY" in ValuationEngine.FLAG_SCOPE
    assert ValuationEngine.FLAG_SCOPE["EXTREME_ROE_ANOMALY"] == {"ROE"}
    assert ValuationEngine.flags_for_ratio(["EXTREME_ROE_ANOMALY"], "ROE") == ["EXTREME_ROE_ANOMALY"]
    assert ValuationEngine.flags_for_ratio(["EXTREME_ROE_ANOMALY"], "PB") == []
    assert ValuationEngine.is_blocking(["EXTREME_ROE_ANOMALY"]) is True


# [WHY] `INSUFFICIENT_HISTORY` là cờ THÔNG TIN: thiếu lịch sử không làm cho GIÁ TRỊ ratio sai — nó chỉ
# làm z không tính được (đã NULL). Nếu cho nó chặn thì mét `is_clean_metric` (đo độ tin của giá trị)
# bị trộn với "không xếp hạng được".
def test_insufficient_history_la_co_thong_tin():
    assert ValuationEngine.is_blocking(["INSUFFICIENT_HISTORY"]) is False


# [WHY] Chống tái diễn: nhánh `else 0.0` chính là thứ đã đẻ ra 3.433 dòng z giả — nó phải biến mất khỏi
# nguồn của `compute_valuation`.
def test_khong_con_nhanh_bia_z_bang_0(engine):
    src = inspect.getsource(ValuationEngine.compute_valuation)
    assert "if std > 0 else 0.0" not in src, "nhánh bịa z = 0.0 đã quay lại"


# [WHY] ROE phải được kiểm biên ngay tại nơi tính ratio (compute_ratios_for_period) để cờ nằm cùng hàng
# điểm với `is_clean_metric`, không phải suy ra ở consumer.
def test_roe_bound_duoc_kiem_tai_noi_tinh_ratio():
    src = inspect.getsource(ValuationEngine.compute_ratios_for_period)
    assert "EXTREME_ROE_ANOMALY" in src
    assert "1.5" in src or "1,5" in src
