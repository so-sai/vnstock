"""Test cho lưới vị từ trên đường đọc giá PIT của `valuation_engine.py`.

[WHY]
  Bước 3 (recompute 2026-10-16 21:11) chạy `exit 0`, `791 mã OK · 0 lỗi`, nhưng đối soát trực tiếp cho
  kết quả ÂM: `DDG 2026Q2 / PB = 226,180` với `price = 800.000` và `BII 2024Q2 / PS = 25.583,871` với
  `price = 900.000` — đúng những dòng ĐÃ bị cách ly (`is_stale = 1`, nhãn
  `quarantine:unit_mix_phantom_1000x`). Cách ly đúng nhưng consumer không đọc cờ.

[ROOT CAUSE]
  Bốn truy vấn `daily_ohlcv` trong `valuation_engine.py` (dòng 204, 211, 215, 230 bản cũ) là raw query
  KHÔNG có vị từ: `SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?` ⇒ `is_stale` và
  `VALID_ROW_SQL` đều bị bỏ qua. Luật B của `AGENTS.md` đòi chế độ Live/PIT phải là
  `is_stale = 0 AND {VALID_ROW_SQL}`; "lưới cách ly chỉ mạnh bằng đường đọc yếu nhất".

[INVARIANT]
  1. Giá PIT chỉ được lấy từ bar `is_stale = 0` VÀ qua `VALID_ROW_SQL`; không có bar ⇒ trả `(None, cờ)`,
     TUYỆT ĐỐI không rơi về dòng bị cách ly (Missing > Fake).
  2. Hệ quả có thể đo: `DDG 2026Q2` không có bar sống nào ⇒ không được sinh hàng ratio (PB 226 biến mất);
     `BII 2024Q2` có đúng 1 bar sống 2024-06-14 = 1.000đ ⇒ ratio phải tính trên 1.000đ.
  3. `_get_price_history` cũng phải sạch: không dòng `close = 0`, không dòng bị cách ly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.financial.valuation_engine import ValuationEngine


@pytest.fixture(scope="module")
def engine() -> ValuationEngine:
    return ValuationEngine()


# [WHY] Ca chỉ điểm 1: DDG 2026Q2 chỉ có bar rác 800.000đ (is_stale=1) trong cửa sổ 31 ngày và 10 dòng
# close=0 ở ngay trước đó. Hàm PHẢI trả None kèm cờ lý do — nếu trả 800.000 thì PB=226 lại quay về.
def test_ddg_2026q2_khong_duoc_lay_gia_bi_cach_ly(engine):
    price, flag = engine._get_price_with_flag("DDG", "2026-06-30")
    assert price is None, f"lấy giá bị cách ly: {price}"
    assert flag in {"PRICE_FALLBACK_FUTURE", "PRICE_STALE_ONLY", "PRICE_MISSING"}, flag


# [WHY] Ca chỉ điểm 2: BII 2024Q2 có 61 dòng, đúng MỘT bar sống (2024-06-14 = 1.000đ) giữa hàng loạt
# 900.000đ đã cách ly ⇒ phải trả 1.000đ. Đây là ca chứng minh vá đúng chỗ mà KHÔNG vá quá tay.
def test_bii_2024q2_dung_bar_song_1000d(engine):
    price, flag = engine._get_price_with_flag("BII", "2024-06-30")
    assert price == 1000.0, f"giá phải là 1.000đ của bar sống, nhận {price}"
    assert flag is None, flag


# [WHY] Chặn đường vòng: `_get_price_history` (dùng cho thống kê trailing) cũng nuốt mọi dòng nếu không
# có vị từ ⇒ 900.000đ và các dòng close=0 sẽ chảy vào mọi phép tính z-score/percentile.
def test_price_history_khong_chua_dong_cach_ly_va_close_0(engine):
    h = engine._get_price_history("BII")
    assert 900_000.0 not in h.values(), "còn giá bị cách ly trong lịch sử"
    assert all(v and v > 0 for v in h.values()), "còn close = 0 trong lịch sử"
    assert h.get("2024-06-14") == 1000.0, h.get("2024-06-14")


# [WHY] Bất biến hình dạng: mọi dòng lịch sử trả về phải qua `VALID_ROW_SQL` — nếu không, một dòng rác
# hình dạng (low > close) vẫn lọt vào thống kê và làm méo z-score của chính mã đó.
def test_price_history_chi_chua_dong_qua_vi_tu(engine):
    h = engine._get_price_history("DDG")
    assert 800_000.0 not in h.values(), "còn giá bị cách ly trong lịch sử"
    assert all(v and v > 0 for v in h.values()), "còn close = 0 trong lịch sử"
