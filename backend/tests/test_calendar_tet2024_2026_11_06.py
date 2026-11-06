# -*- coding: utf-8 -*-
"""Lịch VN: 2024-02-15 và 2024-02-16 là PHIÊN, không phải nghỉ Tết (NQ-18 §II.2).

[WHY]
    Cả `backend/src/daily_updater.py:VN_HOLIDAYS` và `src/main.rs:832-833` đều xếp
    `2024-02-15` + `2024-02-16` vào nghỉ Tết Giáp Thìn. Nhưng đo trên `screener_cache.db`
    (06/11/2026) cho thấy hai ngày đó có dữ liệu giao dịch ĐẦY ĐỦ và BÌNH THƯỜNG:
        2024-02-15 : 1.092 bar, volume 1.995.796.205
        2024-02-16 : 1.139 bar, volume 1.819.204.976
    so với phiên thường ngay trước/sau: 2024-02-07 = 1.093 bar/1.555.833.512,
    2024-02-19 = 1.114 bar/2.325.529.617. Còn 08/02–14/02 = **đúng 0 bar** ở cả ba bảng
    (`daily_ohlcv`, `market_wide_breadth_daily`, `regime_history`).

[ROOT CAUSE]
    Kỳ nghỉ Tết Giáp Thìn 2024 là 08/02–14/02; HOSE giao dịch lại từ thứ Năm 15/02/2024.
    Bản lịch bị kéo dài thêm 2 ngày sang 15–16/02 — lỗi "over-inclusive calendar".

[INVARIANT]
    (a) 15/02 và 16/02/2024 LÀ phiên ⇒ `previous_session("2024-02-19") == "2024-02-16"`
        (nếu sai, mọi phép "biến động 1 phiên" quanh Tết 2024 gộp 3 phiên thành một —
        đúng bẫy NQ-17 §I.1);
    (b) 08/02–14/02/2024 VẪN là nghỉ — cấm nới lỏng cả kỳ nghỉ Tết để "sửa cho khớp";
    (c) test đọc thẳng CSDL THẬT để khẳng định lịch khớp dữ liệu, không khớp niềm tin.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_DB = BACKEND_DIR / "data" / "screener_cache.db"


def test_tet_2024_resumed_on_15_february():
    """[WHY] Đây là khẳng định trung tâm: hai ngày 15–16/02/2024 phải là PHIÊN.

    Nếu sai, `previous_session("2024-02-19")` trả `2024-02-07` — nhảy qua 3 phiên thật
    (15, 16, 19/02) — và mọi cửa sổ 1 phiên / 5 phiên / 20 phiên quanh Tết 2024 bị lệch.
    """
    from src.database.session_continuity import is_session_day, previous_session

    assert is_session_day("2024-02-15") is True, "15/02/2024 HOSE đã giao dịch lại"
    assert is_session_day("2024-02-16") is True, "16/02/2024 là thứ Sáu, phiên bình thường"
    assert previous_session("2024-02-19") == "2024-02-16"


def test_actual_tet_2024_days_stay_holidays():
    """[WHY] Chống vá quá tay theo hướng nguy hiểm nhất: nếu ai đó "sửa" bằng cách xoá cả
    kỳ nghỉ Tết (hoặc bỏ luôn `VN_HOLIDAYS` 2024), bộ dò khuyết phiên sẽ đòi dữ liệu của
    7 ngày sàn đóng cửa và báo động giả suốt kỳ nghỉ — đúng thảm hoạ 31/08/2026 lặp lại
    ở quy mô một tuần.

    Bằng chứng: 08/02–14/02/2024 = 0 bar ở cả ba bảng độc lập.
    """
    from src.database.session_continuity import is_session_day, previous_session

    for d in (
        "2024-02-08",
        "2024-02-09",
        "2024-02-12",
        "2024-02-13",
        "2024-02-14",
    ):
        assert is_session_day(d) is False, f"{d}/2024 là nghỉ Tết"
    assert previous_session("2024-02-15") == "2024-02-07", "phiên trước Tết là 07/02 (thứ Tư)"


def test_live_data_confirms_15_and_16_february_had_real_trading():
    """[WHY] Test này ghim BẰNG CHỨNG vào CSDL thật: nếu một ngày nào đó dữ liệu 15–16/02 bị
    xoá (ví dụ backfill sai), test đỏ ngay thay vì để lịch "đúng" trên giấy trong khi dữ
    liệu đã biến mất (AGENTS §4A: không biết ≠ 0).
    """
    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        def bars(d: str) -> int:
            return con.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE date=? AND COALESCE(is_stale,0)=0",
                (d,),
            ).fetchone()[0]

        def rows(table: str, d: str) -> int:
            return con.execute(f"SELECT COUNT(*) FROM {table} WHERE date=?", (d,)).fetchone()[0]

        assert bars("2024-02-15") >= 100, "15/02/2024 phải có >=100 bar (đo được 1.092)"
        assert bars("2024-02-16") >= 100, "16/02/2024 phải có >=100 bar (đo được 1.139)"
        for d in ("2024-02-08", "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14"):
            assert bars(d) == 0, f"{d}/2024 phải 0 bar"
            assert rows("market_wide_breadth_daily", d) == 0
            assert rows("regime_history", d) == 0
    finally:
        con.close()
