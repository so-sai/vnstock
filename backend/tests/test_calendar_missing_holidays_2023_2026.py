# -*- coding: utf-8 -*-
"""Lịch VN: 14 phiên bị thiếu trong `VN_HOLIDAYS` (2023–2026) — NQ-18 §II.2.

[WHY]
    `daily_updater.VN_HOLIDAYS` chỉ liệt kê từ 2024 trở đi và vẫn thiếu nhiều ngày sàn đóng
    cửa. Đo trên `screener_cache.db` (06/11/2026), quét toàn dải 2023-01-01..2026-11-05:
    có **14 ngày** được lịch coi là phiên nhưng **cả ba bảng độc lập đều trống** và hai bên
    đều có ~1.000 bar:
        Tết Dương lịch nghỉ bù : 2023-01-02
        Tết Quý Mão            : 2023-01-20, 01-23, 01-24, 01-25, 01-26
        Lễ 30/04 – 01/05       : 2023-05-01, 05-02, 05-03
        Quốc khánh 2023        : 2023-09-01, 09-04
        Giỗ Tổ + 30/04 (2024)  : 2024-04-29
        Quốc khánh 2025        : 2025-09-01
        Tết Dương lịch 2026    : 2026-01-02

[ROOT CAUSE]
    Lịch được điền tay theo từng năm, mỗi năm chỉ ghi vài ngày lễ chính; không có bước đối
    chiếu với dữ liệu. Ba bảng (`daily_ohlcv`, `market_wide_breadth_daily`, `regime_history`)
    do ba writer độc lập ghi, nên "cả ba cùng trống" loại trừ khả năng một crawler chết —
    đó là sàn đóng cửa (AGENTS §2: phân biệt "không giao dịch" với "thiếu dữ liệu").

[INVARIANT]
    (a) 14 ngày trên KHÔNG phải phiên;
    (b) mỗi kỳ nghỉ phải LIỀN MẠCH: `previous_session` của ngày cuối kỳ nghỉ phải nhảy về
        phiên cuối trước kỳ nghỉ và phiên đó phải có >= 100 bar — nếu lịch chỉ sửa một nửa,
        `previous_session` sẽ dừng ở một ngày 0 bar và bất biến này đỏ;
    (c) ngày đi làm lại sau mỗi kỳ nghỉ VẪN là phiên (chống vá quá tay: cấm biến cả tuần
        thành nghỉ để "cho khớp").
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

# (ngay_nghi, ngay_giao_dich_lai, phien_cuoi_truoc_ky_nghi)
CLOSURES: list[tuple[str, str, str]] = [
    ("2023-01-02", "2023-01-03", "2022-12-30"),
    ("2023-01-20", "2023-01-27", "2023-01-19"),
    ("2023-01-23", "2023-01-27", "2023-01-19"),
    ("2023-01-24", "2023-01-27", "2023-01-19"),
    ("2023-01-25", "2023-01-27", "2023-01-19"),
    ("2023-01-26", "2023-01-27", "2023-01-19"),
    ("2023-05-01", "2023-05-04", "2023-04-28"),
    ("2023-05-02", "2023-05-04", "2023-04-28"),
    ("2023-05-03", "2023-05-04", "2023-04-28"),
    ("2023-09-01", "2023-09-05", "2023-08-31"),
    ("2023-09-04", "2023-09-05", "2023-08-31"),
    ("2024-04-29", "2024-05-02", "2024-04-26"),
    ("2025-09-01", "2025-09-03", "2025-08-29"),
    ("2026-01-02", "2026-01-05", "2025-12-31"),
]

HOLIDAYS = [c[0] for c in CLOSURES]
RESUME_DAYS = sorted({c[1] for c in CLOSURES})


def test_measured_holidays_are_not_sessions():
    """[WHY] Nếu một ngày trong danh sách vẫn là "phiên", bộ dò khuyết phiên (Chốt #2) sẽ
    báo CRITICAL vào đúng ngày sàn đóng cửa — và vì đây là ngày lịch sử, nó sẽ báo MÃI MÃI.
    """
    from src.database.session_continuity import is_session_day

    for d in HOLIDAYS:
        assert is_session_day(d) is False, f"{d} là ngày sàn đóng cửa (3 bảng đều 0 dòng)"


def test_each_closure_is_contiguous():
    """[WHY] Sửa nửa vời là nguy hiểm hơn không sửa: nếu chỉ thêm 1-2 ngày giữa kỳ nghỉ,
    `previous_session` sẽ dừng ở một ngày 0 bar và mọi phép "phiên trước" vẫn sai, nhưng
    cổng khuyết phiên lại im lặng vì ngày đó "có trong lịch là nghỉ" một phần.
    """
    from src.database.session_continuity import previous_session

    for holiday, _resume, pre_closure in CLOSURES:
        assert previous_session(holiday) == pre_closure, (
            f"previous_session({holiday}) phải nhảy về {pre_closure} "
            f"(đầu kỳ nghỉ), không dừng giữa kỳ nghỉ"
        )


def test_resume_days_are_still_sessions():
    """[WHY] Chống vá quá tay: nếu ai đó "sửa" bằng cách đánh dấu cả tuần là nghỉ, hệ thống
    sẽ bỏ qua những phiên có ~1.000 bar thật — mất dữ liệu đúng (nguy hiểm hơn báo động giả).
    """
    from src.database.session_continuity import is_session_day

    for d in RESUME_DAYS:
        assert is_session_day(d) is True, f"{d} là ngày giao dịch lại — phải là phiên"
    # [WHY] vài phiên thường xen kẽ, để chắc chắn không có "nới lỏng theo tháng".
    for d in ("2023-01-19", "2023-04-28", "2023-08-31", "2024-04-26", "2025-08-29"):
        assert is_session_day(d) is True, d


def test_live_three_tables_empty_on_holidays_and_full_on_neighbours():
    """[WHY] Ghim BẰNG CHỨNG vào CSDL thật, hai chiều:
    (a) ngày nghỉ: cả 3 bảng 0 dòng — nếu một bảng bỗng có dòng thì "nghỉ" là kết luận sai;
    (b) hai bên kỳ nghỉ: >= 100 bar — nếu mất bar thì lịch "đúng" trên giấy nhưng dữ liệu
        đã biến mất (AGENTS §4A: không biết ≠ 0).
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

        for holiday, resume, pre_closure in CLOSURES:
            assert bars(holiday) == 0, f"{holiday} phải 0 bar"
            assert rows("market_wide_breadth_daily", holiday) == 0, holiday
            assert rows("regime_history", holiday) == 0, holiday
            assert bars(pre_closure) >= 100, f"{pre_closure} phải có bar (phiên trước kỳ nghỉ)"
            assert bars(resume) >= 100, f"{resume} phải có bar (ngày giao dịch lại)"
    finally:
        con.close()
