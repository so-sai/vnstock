# -*- coding: utf-8 -*-
"""Lịch phiên VN: Python phải khớp Rust về kỳ nghỉ Quốc khánh 2026 (NQ-17, 06/11/2026).

[WHY]
    `src/main.rs:839` khai báo `VN_HOLIDAYS` có **2026-08-31** và **2026-09-01**, kèm unit
    test `missing_trading_days_between("2026-08-31", "2026-09-03") == []`. Python
    (`daily_updater.VN_HOLIDAYS`) chỉ có `2026-09-02`; `2026-09-01` lọt qua nhờ
    `NON_TRADING_SESSIONS` (phantom), còn `2026-08-31` **không được xử lý ở đâu cả**.

[ROOT CAUSE]
    Kỳ nghỉ Quốc khánh 2026 là 31/08–02/09 (thứ Hai → thứ Tư). Python được điền lịch theo
    từng ngày lễ lẻ (02/09) thay vì cả kỳ nghỉ ⇒ thiếu 31/08. Đây là cùng một lớp lỗi
    "The Seam" của Mục 26: hai ngôn ngữ giữ hai bản lịch khác nhau.

[INVARIANT]
    (a) 31/08, 01/09, 02/09/2026 đều KHÔNG phải phiên;
    (b) phiên liền trước 03/09/2026 là 28/08/2026 (thứ Sáu);
    (c) mọi thứ Hai bình thường khác vẫn là phiên — cấm biến 31/08 thành cái cớ nới lỏng
        `is_session_day` cho toàn bộ thứ Hai.
"""

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


def test_national_day_2026_closure_is_three_days():
    """[WHY] Ba bảng độc lập (`daily_ohlcv`, `market_wide_breadth_daily`, `regime_history`)
    đều có 0 dòng cho 31/08 và 01/09 (đo 06/11/2026). Nếu Python coi 31/08 là phiên, mọi bộ
    dò khuyết phiên sẽ báo động giả mỗi ngày, và `daily-update` sẽ cố cào một phiên không có.
    """
    from src.database.session_continuity import is_session_day

    assert is_session_day("2026-08-31") is False, "31/08/2026 là nghỉ Quốc khánh"
    assert is_session_day("2026-09-01") is False, "01/09/2026 là nghỉ Quốc khánh"
    assert is_session_day("2026-09-02") is False, "02/09/2026 là nghỉ Quốc khánh"


def test_previous_session_skips_whole_national_day_break():
    """[WHY] `previous_session("2026-09-03")` phải nhảy qua CẢ BA ngày nghỉ về 28/08 — nếu
    trả 31/08 thì Mục 26 sẽ đòi backfill đúng ngày không tồn tại và chặn EOD vô cớ.
    """
    from src.database.session_continuity import previous_session

    assert previous_session("2026-09-03") == "2026-08-28"
    assert previous_session("2026-09-04") == "2026-09-03"


def test_holiday_fix_does_not_relax_normal_mondays():
    """[WHY] Chống vá quá tay: nếu ai đó "sửa" bằng cách coi mọi thứ Hai là nghỉ, bộ dò
    khuyết phiên sẽ mù đúng vào ngày khuyết phổ biến nhất (cron miss cuối tuần → thứ Hai).
    """
    from src.database.session_continuity import is_session_day, previous_session

    assert is_session_day("2026-08-24") is True, "thứ Hai thường"
    assert is_session_day("2026-11-02") is True, "thứ Hai 02/11/2026 có bar thật"
    assert previous_session("2026-11-02") == "2026-10-30"
