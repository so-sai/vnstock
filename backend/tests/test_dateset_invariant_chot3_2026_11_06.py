# -*- coding: utf-8 -*-
"""CHỐT #3 — Mục 27 (NQ-17 §II.1): bất biến tập ngày, PHẠM VI HỢP ĐỒNG.

[WHY]
    Sự cố 04/11/2026 lộ ra một lỗ hổng đo lường: CẢ `paper_equity_curve` VÀ `eod_run_ledger`
    cùng khuyết 04/11. Nếu chỉ so hai bảng kế toán với nhau thì "cùng khuyết" trông y hệt
    "khớp nhau" — tức phép kiểm tra tự mù đúng vào sự cố cần bắt. Vì vậy mẫu số phải là
    LỊCH PHIÊN, không phải một bảng dữ liệu.

[ROOT CAUSE]
    Không có bất kỳ test/invariant nào so tập ngày giữa các bảng dẫn xuất. 2.400 unit test
    hiện có đều kiểm tra GIÁ TRỊ trong một bảng đã có sẵn dòng; không bài nào kiểm tra
    "dòng đó có tồn tại đúng phiên hay không".

[INVARIANT]
    (a) thị trường: 60 phiên gần nhất phải có mặt ở `daily_ohlcv` (≥100 bar hợp lệ),
        `market_wide_breadth_daily`, `regime_history`;
    (b) kế toán: 20 phiên gần nhất phải có mặt ở `paper_equity_curve` VÀ `eod_run_ledger`;
    (c) lệch NGOÀI cửa sổ = nợ kế thừa, KHÔNG fail-closed (NQ-17 §II.1) — nhưng nợ kế thừa
        phải được ĐO và GHIM SỐ, để một lỗ hổng MỚI không thể trà trộn vào "nợ cũ";
    (d) ngày nghỉ / phantom holiday không bao giờ là phiên ⇒ không bao giờ bị báo thiếu.
"""

import sqlite3
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
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_DB = BACKEND_DIR / "data" / "screener_cache.db"

# [WHY] GHIM nợ kế thừa đo ngày 06/11/2026 (sau khi sửa lịch 31/08). Đây KHÔNG phải miễn
# trừ cho tương lai: nếu một lỗ hổng mới xuất hiện, số đếm đổi ⇒ test đỏ. Nếu nợ cũ được
# trả (backfill Mục 22), test cũng đỏ ⇒ buộc cập nhật số đo thay vì để baseline cũ che mất.
LEGACY_EQUITY_GAPS = 22
LEGACY_LEDGER_GAPS = 54
LEGACY_LAST_GAP = "2026-10-02"  # lỗ hổng kế thừa MUỘN NHẤT; sau ngày này phải sạch


def _sessions_ending(as_of: str, n: int) -> list[str]:
    from src.database.session_continuity import session_window_end

    return session_window_end(as_of, n)


def _db(sessions: list[str], drop: dict[str, set[str]] | None = None) -> sqlite3.Connection:
    """Dựng đủ 5 bảng; `drop[ten_bang]` = các ngày bị khoét khỏi bảng đó."""
    drop = drop or {}
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, is_stale INTEGER)")
    con.execute("CREATE TABLE market_wide_breadth_daily (date TEXT)")
    con.execute("CREATE TABLE regime_history (date TEXT)")
    con.execute("CREATE TABLE paper_equity_curve (date TEXT)")
    con.execute("CREATE TABLE eod_run_ledger (as_of_date TEXT)")

    bars, breadth, regime, equity, ledger = [], [], [], [], []
    for d in sessions:
        if d not in drop.get("daily_ohlcv", set()):
            # [WHY] 120 bar/phiên: vượt ngưỡng MIN_VALID_BARS_PER_SESSION (100) để test đo
            # đúng "có phiên" chứ không đo ngưỡng; ca "partial" được test riêng bên dưới.
            bars += [(f"S{i}", d, 0) for i in range(120)]
        if d not in drop.get("market_wide_breadth_daily", set()):
            breadth.append((d,))
        if d not in drop.get("regime_history", set()):
            regime.append((d,))
        if d not in drop.get("paper_equity_curve", set()):
            equity.append((d,))
        if d not in drop.get("eod_run_ledger", set()):
            ledger.append((d,))
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?)", bars)
    con.executemany("INSERT INTO market_wide_breadth_daily VALUES (?)", breadth)
    con.executemany("INSERT INTO regime_history VALUES (?)", regime)
    con.executemany("INSERT INTO paper_equity_curve VALUES (?)", equity)
    con.executemany("INSERT INTO eod_run_ledger VALUES (?)", ledger)
    con.commit()
    return con


# ===================================================================
# 1. Phát hiện lỗ hổng (RED) + chống vá quá tay (GREEN)
# ===================================================================
def test_market_hole_is_detected_and_named():
    """[WHY] Đúng ca 04/11: breadth thiếu 1 phiên trong cửa sổ ⇒ cổng phải đỏ và NÊU TÊN
    ngày. Chỉ báo "lệch tập ngày" mà không nêu ngày là vô dụng lúc 15:45.
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s, {"market_wide_breadth_daily": {"2026-11-04"}})
    try:
        with pytest.raises(SessionSetMismatchError) as exc:
            assert_session_set_alignment(con, "2026-11-05")
        msg = str(exc.value)
        assert "2026-11-04" in msg
        assert "market_wide_breadth_daily" in msg
    finally:
        con.close()


def test_full_alignment_passes():
    """[WHY] Chống vá quá tay: dữ liệu đầy đủ thì KHÔNG được chặn — cổng đỏ vô cớ sẽ bị tắt.
    """
    from src.database.session_continuity import assert_session_set_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s)
    try:
        report = assert_session_set_alignment(con, "2026-11-05")
    finally:
        con.close()
    assert report["ok"] is True
    assert report["market_window"][2] == 60


def test_partial_bars_are_not_counted_as_present():
    """[WHY] 40 bar/900 không phải "phiên có dữ liệu" mà là ingest chết — nếu tính là có thì
    cổng mù đúng vào loại sự cố khó thấy nhất.
    """
    from src.database.session_continuity import collect_session_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s)
    con.execute("DELETE FROM daily_ohlcv WHERE date = '2026-11-04' AND rowid NOT IN "
                "(SELECT rowid FROM daily_ohlcv WHERE date = '2026-11-04' LIMIT 40)")
    con.commit()
    try:
        report = collect_session_alignment(con, "2026-11-05")
    finally:
        con.close()
    assert report["market_missing"]["daily_ohlcv"] == ["2026-11-04"]


def test_legacy_hole_outside_market_window_is_not_fail_closed():
    """[WHY] NQ-17 §II.1: lệch trước cửa sổ 60 phiên là dữ liệu lịch sử kế thừa, KHÔNG được
    fail-closed — nếu không, khác biệt 2020 (`regime` ra đời 12/2020) sẽ làm cổng đỏ vĩnh viễn.
    """
    from src.database.session_continuity import assert_session_set_alignment

    # [WHY] 70 phiên: phải DƯ 10 phiên ngoài cửa sổ 60 thì mới đo được "ngoài cửa sổ".
    s = _sessions_ending("2026-11-05", 70)
    con = _db(s, {"regime_history": {s[0]}})
    try:
        report = assert_session_set_alignment(con, "2026-11-05")
    finally:
        con.close()
    assert report["ok"] is True, report


def test_accounting_hole_in_BOTH_tables_still_fails():
    """[WHY] Đây là lý do tồn tại của Chốt #3: 04/11/2026 thiếu ở CẢ equity và ledger, nên
    so-chéo-hai-bảng không phát hiện được. Mẫu số phải là LỊCH.
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s, {"paper_equity_curve": {"2026-11-04"}, "eod_run_ledger": {"2026-11-04"}})
    try:
        with pytest.raises(SessionSetMismatchError) as exc:
            assert_session_set_alignment(con, "2026-11-05")
        assert "2026-11-04" in str(exc.value)
    finally:
        con.close()


def test_accounting_legacy_outside_20_sessions_is_scoped():
    """[WHY] Cùng một lỗ hổng: NGOÀI 20 phiên ⇒ không chặn (nợ cũ), nhưng khi hỏi TOÀN BỘ
    từ epoch ⇒ phải thấy. Chứng minh phạm vi là thật, không phải bịt mắt.
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment

    s = _sessions_ending("2026-11-05", 70)
    old = s[5]
    con = _db(s, {"paper_equity_curve": {old}, "eod_run_ledger": {old}})
    try:
        assert assert_session_set_alignment(con, "2026-11-05")["ok"] is True
        with pytest.raises(SessionSetMismatchError):
            assert_session_set_alignment(
                con, "2026-11-05", accounting_window=None, contract_epoch=s[0]
            )
    finally:
        con.close()


def test_holidays_are_never_expected_sessions():
    """[WHY] Chống vá quá tay: nếu lịch lại coi 31/08–02/09/2026 là phiên, cổng sẽ đòi dữ
    liệu của những ngày sàn đóng cửa và đỏ mỗi ngày.
    """
    from src.database.session_continuity import session_window_end

    win = session_window_end("2026-09-04", 4)
    assert "2026-08-31" not in win
    assert "2026-09-01" not in win
    assert "2026-09-02" not in win
    assert win[-3:] == ["2026-08-28", "2026-09-03", "2026-09-04"]


# ===================================================================
# 2. Trạng thái CSDL THẬT (Sentinel thường trực)
# ===================================================================
def test_live_market_alignment_is_exact():
    """[WHY] Sentinel 60 phiên: chỉ cần một phiên khuyết lọt vào là test đỏ NGAY hôm sau,
    thay vì để EOD hạch toán trên nền khuyết rồi phát hiện sau 2 giờ (sự cố 04/11).

    [SỬA 06/11/2026, NQ-18 §II.2] Trước đây test này khẳng định `ok is True`. Từ khi Chốt #3
    có thêm CHIỀU NGƯỢC, `ok` còn phụ thuộc "không có dòng trên ngày không giao dịch" — mà
    CSDL thật đang có đúng 3 dòng như vậy ở ngày `2026-09-01` (nợ đo được, chưa thanh lý).
    Vì thế ở đây khẳng định phần CHIỀU THIẾU (mục đích gốc của sentinel này) và ghim phần
    dòng thừa; tập dòng thừa được ghim CHÍNH XÁC ở
    `test_dateset_reverse_direction_2026_11_06.py::test_live_known_orphans_are_pinned_exactly`.
    Không nới lỏng: nếu thiếu một phiên, `market_missing` sẽ khác rỗng ⇒ test này đỏ.
    """
    from src.database.session_continuity import collect_session_alignment

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = collect_session_alignment(con)
    finally:
        con.close()
    assert not any(report["market_missing"].values()), report
    assert report["market_window"][2] == 60
    assert report["market_spurious"]["market_wide_breadth_daily"] == ["2026-09-01"], report
    assert report["market_spurious"]["regime_history"] == ["2026-09-01"], report
    assert report["market_spurious"]["daily_ohlcv"] == [], report


def test_live_accounting_last_20_sessions_is_clean():
    """[WHY] Vùng không khoan nhượng: 20 phiên gần nhất KHÔNG được có bất kỳ lỗ hổng nào.
    Nếu cron lại miss một ngày, test này đỏ trong vòng một tháng — trước khi nợ kịp tích tụ.
    """
    from src.database.session_continuity import collect_session_alignment

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = collect_session_alignment(con, accounting_window=20)
    finally:
        con.close()
    assert report["accounting_missing"]["paper_equity_curve"] == [], report
    assert report["accounting_missing"]["eod_run_ledger"] == [], report


def test_live_legacy_accounting_gaps_match_measured_baseline():
    """[WHY] Nợ kế thừa phải được GHIM SỐ. Không có test này, một lỗ hổng mới (ví dụ lại miss
    một phiên) sẽ trà trộn vào danh sách "nợ cũ" và không ai thấy số đếm đổi.
    """
    from src.database.session_continuity import collect_session_alignment

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = collect_session_alignment(con, accounting_window=None)
    finally:
        con.close()

    eq = report["accounting_missing"]["paper_equity_curve"]
    lg = report["accounting_missing"]["eod_run_ledger"]
    assert len(eq) == LEGACY_EQUITY_GAPS, f"nợ equity đổi: {len(eq)} != {LEGACY_EQUITY_GAPS} — {eq}"
    assert len(lg) == LEGACY_LEDGER_GAPS, f"nợ ledger đổi: {len(lg)} != {LEGACY_LEDGER_GAPS} — {lg}"
    for days in (eq, lg):
        assert days == sorted(days)
        assert max(days) <= LEGACY_LAST_GAP, f"lỗ hổng MỚI sau {LEGACY_LAST_GAP}: {days}"
