"""pit_quarter.py — quý dùng được theo nguyên tắc POINT-IN-TIME (không nhìn trước BCTC).

VẤN ĐỀ ĐANG SỬA
---------------
`company_health_v2._quarter_at(date)` trả về **quý CHỨA ngày đó**, và `analyze()` chọn
`period <= quý đó`. Ở VN, BCTC quý chỉ được công bố SAU khi quý kết thúc (hạn 30 ngày), nên
một phiên ngày 15/02 dùng báo cáo Q1 (công bố ~tháng 4) là **nhìn trước ~2 tháng**.

Bảng `health_ratios` **không có cột ngày công bố** (`period` là khoá thời gian duy nhất), nên
không thể tra chính xác ngày ra báo cáo — buộc phải dùng **luật lag an toàn** (chậm còn hơn
nhìn trước: dùng báo cáo cũ là mất thông tin, dùng báo cáo chưa công bố là tạo alpha giả).

HẠN NỘP THEO THÔNG TƯ 96/2020/TT-BTC
------------------------------------
    Q1, Q3 (quý)            : 30 ngày sau khi quý kết thúc
    Q2 (bán niên soát xét)  : 45 ngày
    Q4 (năm, kiểm toán)     : 90 ngày sau khi năm tài chính kết thúc

LUẬT LAG ÁP DỤNG (đã cộng buffer an toàn trước hạn nộp)
-------------------------------------------------------
    Q1, Q2, Q3 : 60 ngày   (2× hạn 30 ngày)
    Q4         : 120 ngày  (90 ngày hạn + 30 ngày buffer)

Nguồn buffer: hạn nộp là hạn CUỐI; nhiều doanh nghiệp nộp sát hạn hoặc muộn hơn, và ngày
công bố thực tế không có trong dữ liệu để kiểm. Thà chậm một nhịp còn hơn nhìn trước một nhịp.
"""

from __future__ import annotations

from datetime import date

#: Số ngày tối thiểu từ lúc QUÝ KẾT THÚC tới lúc được phép dùng báo cáo của quý đó.
LAG_DAYS_BY_QUARTER: dict[int, int] = {1: 60, 2: 60, 3: 60, 4: 120}

#: Số quý tối đa lùi lại khi tìm (8 quý = 2 năm; quá số này là dữ liệu hổng, không đoán).
MAX_QUARTERS_BACK = 8


def quarter_end(year: int, quarter: int) -> date:
    """Ngày kết thúc của quý."""
    if quarter == 1:
        return date(year, 3, 31)
    if quarter == 2:
        return date(year, 6, 30)
    if quarter == 3:
        return date(year, 9, 30)
    return date(year, 12, 31)


def quarter_label(year: int, quarter: int) -> str:
    """Khoá quý đúng định dạng của `health_ratios.period` (vd '2021Q3')."""
    return f"{year}Q{quarter}"


def _parse(target_date: str) -> date | None:
    try:
        y, m, d = str(target_date)[:10].split("-")
        return date(int(y), int(m), int(d))
    except TypeError, ValueError, AttributeError:
        return None


def _iter_quarters_back(day: date, max_back: int):
    """Sinh (year, quarter) lùi dần từ quý chứa `day`."""
    y, q = day.year, (day.month - 1) // 3 + 1
    for _ in range(max_back):
        yield y, q
        q -= 1
        if q == 0:
            q, y = 4, y - 1


def get_pit_quarter(target_date: str, *, max_back: int = MAX_QUARTERS_BACK) -> str:
    """Quý GẦN NHẤT đã chắc chắn được công bố tại `target_date`.

    Trả về khoá dạng 'YYYYQn' (khớp `health_ratios.period`), hoặc '' nếu không tìm được
    trong `max_back` quý (dữ liệu quá cũ/hổng — KHÔNG đoán, để caller quyết định).

    >>> get_pit_quarter("2021-02-15")   # Q4/2020 chưa công bố (hạn 31/03/2021)
    '2020Q3'
    >>> get_pit_quarter("2022-05-15")   # Q4/2021 đã công bố (hạn 31/03/2022)
    '2021Q4'
    """
    day = _parse(target_date)
    if day is None:
        return ""
    for y, q in _iter_quarters_back(day, max_back):
        elapsed = (day - quarter_end(y, q)).days
        if elapsed >= LAG_DAYS_BY_QUARTER[q]:
            return quarter_label(y, q)
    return ""


__all__ = [
    "LAG_DAYS_BY_QUARTER",
    "MAX_QUARTERS_BACK",
    "get_pit_quarter",
    "quarter_end",
    "quarter_label",
]
