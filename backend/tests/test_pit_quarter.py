"""Test cho `pit_quarter.get_pit_quarter` — khóa bất biến chống nhìn trước BCTC.

BỐI CẢNH: `health_ratios` không có ngày công bố; luật lag trong `pit_quarter` là cách duy
nhất để không dùng báo cáo chưa ra. 4 case dưới đây là 4 case CHỦ HỆ THỐNG đưa ra để nghiệm
thu (Bước 2 của lộ trình PIT); 3 test sau là bất biến tổng quát, vì một vài case rời không
đủ để chặn cả lớp lỗi (một luật sai vẫn có thể qua vài case đúng).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.engine.pit_quarter import (  # noqa: E402
    LAG_DAYS_BY_QUARTER,
    get_pit_quarter,
    quarter_end,
)


# ───────────────────────── 4 case nghiệm thu theo yêu cầu ─────────────────────────


def test_pit_q1_2021():
    """15/02/2021 — Q1/2021 chưa kết thúc; Q4/2020 chưa công bố (hạn 31/03/2021)."""
    assert get_pit_quarter("2021-02-15") == "2020Q3"


def test_pit_q1_2021_after_filing():
    """05/05/2021 — Q1/2021 ĐÃ công bố theo hạn luật (30/04), NHƯNG buffer 60 ngày chưa hết.

    Kỳ vọng gốc của đề bài là '2021Q1' (đọc theo hạn nộp đúng luật). Chủ hệ thống đã chốt
    phương án A′ ngày 2026-10-13: GIỮ buffer 60 ngày và sửa kỳ vọng về '2020Q4', vì đây mới
    là kết quả NHẤT QUÁN với chính case 4 (15/05/2022 → 2021Q4): hai ngày cùng nằm ~5 tuần
    sau khi Q1 kết thúc thì phải cùng lùi về Q4 năm trước.
    """
    assert get_pit_quarter("2021-05-05") == "2020Q4"


def test_pit_q1_2021_mo_khoa_sau_60_ngay():
    """Mốc mở khoá Q1/2021: Q1 kết thúc 31/03 + 60 ngày = 30/05/2021 ⇒ 01/06 dùng được."""
    assert get_pit_quarter("2021-05-29") == "2020Q4"  # 59 ngày: CHƯA mở
    assert get_pit_quarter("2021-05-30") == "2021Q1"  # đúng 60 ngày: mở (biên là >=)
    assert get_pit_quarter("2021-06-01") == "2021Q1"  # sau mốc 60 ngày


def test_pit_q4_annual_lag():
    """28/02/2022 — Q4/2021 chưa công bố (hạn kiểm toán 31/03/2022) ⇒ phải dùng Q3/2021.

    Đây là case dễ sai nhất: luật "lùi một quý" ngây thơ sẽ trả về 2021Q4.
    """
    assert get_pit_quarter("2022-02-28") == "2021Q3"


def test_pit_q4_annual_after_120d():
    """15/05/2022 — Q4/2021 đã công bố (hạn 31/03/2022) ⇒ dùng Q4/2021."""
    assert get_pit_quarter("2022-05-15") == "2021Q4"


# ───────────────────────── bất biến tổng quát ─────────────────────────


def test_bat_bien_khong_bao_gio_dung_bao_cao_chua_cong_bo():
    """BẤT BIẾN GỐC: với MỌI ngày, quý được chọn phải đã qua đủ lag.

    Đây mới là điều cần đúng — 4 case rời có thể qua trong khi luật vẫn sai ở ngày khác.
    """
    day = date(2020, 1, 1)
    checked = 0
    while day <= date(2026, 12, 31):
        got = get_pit_quarter(day.isoformat())
        if got:
            y, q = int(got[:4]), int(got[5:])
            elapsed = (day - quarter_end(y, q)).days
            assert elapsed >= LAG_DAYS_BY_QUARTER[q], (
                f"{day}: dùng {got} nhưng mới {elapsed} ngày kể từ khi quý kết thúc "
                f"(cần {LAG_DAYS_BY_QUARTER[q]}) — NHÌN TRƯỚC"
            )
            checked += 1
        day += timedelta(days=1)
    assert checked > 2500, "phải kiểm gần hết khoảng ngày, không được thoát sớm"


def test_ket_qua_don_dieu_theo_thoi_gian():
    """Quý được chọn không được ĐI LÙI khi thời gian tiến (dữ liệu chỉ nên lớn lên)."""
    prev = ""
    day = date(2021, 1, 1)
    while day <= date(2025, 12, 31):
        cur = get_pit_quarter(day.isoformat())
        if cur:
            assert cur >= prev, f"{day}: {cur} đi lùi so với {prev}"
            prev = cur
        day += timedelta(days=1)


def test_q4_khat_khe_hon_q1():
    """Lag quý 4 (kiểm toán) phải CHẶT hơn quý thường — nếu không thì cấu hình đã bị đảo."""
    assert LAG_DAYS_BY_QUARTER[4] > LAG_DAYS_BY_QUARTER[1]
    assert LAG_DAYS_BY_QUARTER[4] >= 90, "hạn kiểm toán năm là 90 ngày, không được thấp hơn"


def test_ngay_khong_hop_le_tra_rong_chu_khong_doan():
    """Ngày rác ⇒ '' (để caller quyết định), tuyệt đối không trả một quý đoán bừa."""
    for bad in ("", "khong-phai-ngay", "2021-13-45", None):
        assert get_pit_quarter(bad) == ""  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
