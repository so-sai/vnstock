"""Test canary của `migrate_shares_history.py` — dung sai TƯƠNG ĐỐI có tuyên bố.

[WHY]
  Bước [C] của `recompute_valuation_scores_pit.py` bị chặn bởi canary đòi ĐÚNG TUYỆT ĐỐI (`abs(got-exp)<1`):
  `HPG 2018Q1 CHARTER_PIT` kỳ vọng 1.517.079.000 nhưng dữ liệu cho 1.517.100.000 (+21.000 cp = +0,0014%);
  `HPG 2025Q2` +49.800 cp trên 6,4 tỷ = +0,0008%. Nguyên nhân: `CHARTER_CAPITAL` được ingestion làm mới
  thành số tròn theo BCTC (15.171 tỷ / 63.963 tỷ) ⇒ phép suy `CHARTER_CAPITAL/10.000` dịch ~0,001%.

[ROOT CAUSE]
  Canary dùng so sánh tuyệt đối `< 1` cổ phiếu cho các đại lượng cỡ tỷ ⇒ biến **nhiễu làm tròn** thành
  **lỗi chặn đường ống** (brittle test), trong khi mục đích thật của nó là bắt lỗi **bậc độ lớn/đơn vị**
  (10×, 1.000×) — vốn lệch 900–1150%.

[INVARIANT]
  1. Dung sai TƯƠNG ĐỐI ≤ 0,5% (`CANARY_TOL`) + sàn tuyệt đối 1 cp cho các giá trị nhỏ.
  2. Lệch ≥10× (hoặc bất kỳ lệch > 0,5%) vẫn bị CHẶN — không nới lỏng khả năng phát hiện lỗi đơn vị.
  3. Thiếu khoá trong dữ liệu ⇒ LỖI (không được coi là đạt).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate_shares_history",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_shares_history.py",
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate_shares_history"] = mod
_SPEC.loader.exec_module(mod)


# [WHY] Ca thật đã làm chặn đường ống: lệch 21.000 cp trên 1,5 tỷ cp = 0,0014% PHẢI được chấp nhận.
# Nếu test này đỏ thì mọi lần ingestion làm tròn số BCTC đều chặn recompute.
def test_lech_lam_tron_0001pct_duoc_chap_nhan():
    assert mod._canary_ok(1_517_100_000.0, 1_517_079_000.0, mod.CANARY_TOL) is True
    assert mod._canary_ok(6_396_300_000.0, 6_396_250_200.0, mod.CANARY_TOL) is True


# [WHY] Không được nới quá tay: lỗi ĐƠN VỊ (10×, 1.000×) là lớp lỗi nguy hiểm nhất của dự án
# (`rescaled_vci_x1000`, phantom 800.000đ) nên canary PHẢI còn bắt được chúng.
@pytest.mark.parametrize("got,exp", [(1_517_079_000.0 * 10, 1_517_079_000.0), (1_517_079.0, 1_517_079_000.0)])
def test_loi_don_vi_10x_va_1000x_van_bi_chan(got, exp):
    assert mod._canary_ok(got, exp, mod.CANARY_TOL) is False


# [WHY] Lệch vừa phải (1%) vẫn phải chặn — ranh giới 0,5% là quyết định có tuyên bố, không phải tuỳ ý.
def test_lech_1pct_bi_chan():
    assert mod._canary_ok(1_532_249_790.0, 1_517_079_000.0, mod.CANARY_TOL) is False


# [WHY] Sàn tuyệt đối: với giá trị nhỏ (1.000 cp), dung sai 0,5% chỉ là 5 cp — phép so sánh vẫn phải
# đúng, không được dùng `max(..., 1)` để biến 999 thành đạt ở mọi quy mô.
def test_san_tuyet_doi_1cp_khong_lam_mu_gia_tri_nho():
    assert mod._canary_ok(1_006.0, 1_000.0, mod.CANARY_TOL) is False  # 0,6% > 0,5%
    assert mod._canary_ok(1_004.0, 1_000.0, mod.CANARY_TOL) is True  # 0,4%


# [WHY] Thiếu khoá canary (mã/kỳ/basis không có trong dữ liệu) là LỖI, không phải "không kiểm tra được
# nên bỏ qua" — fail-closed.
def test_thieu_khoa_la_loi():
    key = {("HPG", "2018Q1", mod.BASIS_CHARTER): 1_517_100_000.0}
    lines = mod.check_canary(key, mod.CANARY, mod.CANARY_TOL)
    errs = [bad for _line, bad in lines if bad]
    assert len(errs) == len(mod.CANARY) - 1, errs
    assert any("HPG" in e and "2018Q1" not in e for e in errs) or errs
