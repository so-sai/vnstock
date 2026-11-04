# -*- coding: utf-8 -*-
"""Không được có dòng kế toán mang ngày TƯƠNG LAI (Mục 23, NQ-16 04/11/2026).

[WHY]
    `paper_equity_curve` có 7 dòng và `paper_cash_ledger` có 1 dòng mang ngày
    2099-01-04..2099-01-12 với `portfolio_id = SEL_PAPER_V1`, `created_at =
    2026-09-28T15:42` — dấu vết một lần chạy test dùng ngày sentinel 2099.
    Mọi consumer dùng `SELECT MAX(date)` hoặc `ORDER BY date DESC` (Sentinel,
    daily-update, eod-run, vẽ equity curve) sẽ tưởng hệ thống đã có dữ liệu tới
    năm 2099 ⇒ các chốt chặn freshness/stale im lặng vô hiệu.

[ROOT CAUSE]
    Không tồn tại cổng nào kiểm tra "ngày ghi vào sổ kế toán phải <= hôm nay".
    Test dùng 2099 để KHÔNG đụng dữ liệu thật, nhưng lại ghi vào CSDL thật bằng
    `portfolio_id` thật thay vì namespace `__TEST__`.

[INVARIANT]
    (a) Sau migration, 0 dòng có `date > hôm nay` trong hai bảng kế toán;
    (b) purge chỉ xoá đúng phần tương lai — dòng lịch sử thật phải còn nguyên;
    (c) nếu số dòng định xoá khác số đã kiểm đếm (`expected`) thì TỪ CHỐI xoá.
"""

import sqlite3
import sys
from datetime import datetime
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
TODAY = datetime.now().strftime("%Y-%m-%d")


def _make_ledger_db(path: Path) -> sqlite3.Connection:
    """DB tạm có đúng hình dạng hai bảng kế toán (chỉ cột cần cho phép đo)."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE paper_equity_curve (portfolio_id TEXT, date TEXT, total_equity REAL)")
    con.execute("CREATE TABLE paper_cash_ledger (portfolio_id TEXT, date TEXT, amount REAL)")
    return con


# ===================================================================
# 1. Hàm purge: chỉ xoá tương lai, có cổng chống xoá lố
# ===================================================================
def test_purge_only_removes_future_rows(tmp_path):
    """[WHY] Chống vá quá tay: 2099 là rác, nhưng 2020 và 'hôm nay' là dữ liệu thật.
    Một lệnh `DELETE FROM ... WHERE date > today` viết sai (>=, hoặc thiếu WHERE)
    sẽ xoá mất lịch sử — test này khoá chặn điều đó.
    """
    from src.database.date_sanity import purge_future_rows

    con = _make_ledger_db(tmp_path / "led.db")
    try:
        con.executemany(
            "INSERT INTO paper_equity_curve VALUES (?,?,?)",
            [
                ("P", "2020-03-02", 1.0),
                ("P", TODAY, 2.0),
                ("P", "2099-01-04", 3.0),
                ("P", "2099-01-12", 4.0),
            ],
        )
        con.commit()

        assert purge_future_rows(con, "paper_equity_curve", "date", TODAY) == 2
        left = [r[0] for r in con.execute("SELECT date FROM paper_equity_curve ORDER BY date")]
    finally:
        con.close()
    assert left == ["2020-03-02", TODAY], f"chỉ được xoá 2 dòng 2099, còn {left}"


def test_purge_refuses_when_count_does_not_match(tmp_path):
    """[WHY] Đếm-trước-xoá-sau là lá chắn cuối: nếu giữa lúc kiểm đếm và lúc xoá có
    tiến trình khác ghi thêm dòng tương lai, `expected` lệch ⇒ phải DỪNG, không xoá
    mù. Sai ở đây là mất dữ liệu không hồi phục.
    """
    from src.database.date_sanity import purge_future_rows

    con = _make_ledger_db(tmp_path / "led2.db")
    try:
        con.execute("INSERT INTO paper_cash_ledger VALUES ('P','2099-01-04',1.0)")
        con.commit()
        with pytest.raises(RuntimeError, match="lệch"):
            purge_future_rows(con, "paper_cash_ledger", "date", TODAY, expected=5)
        n = con.execute("SELECT COUNT(*) FROM paper_cash_ledger").fetchone()[0]
    finally:
        con.close()
    assert n == 1, "từ chối xoá thì bảng phải nguyên vẹn"


def test_fetch_future_rows_returns_isolation_payload(tmp_path):
    """[WHY] Phải có bản ghi cách ly (JSON) trước khi xoá — xoá mà không lưu vết là
    phá provenance, đúng thứ hệ thống này cấm.
    """
    from src.database.date_sanity import fetch_future_rows

    con = _make_ledger_db(tmp_path / "led3.db")
    try:
        con.execute(
            "INSERT INTO paper_equity_curve VALUES ('SEL_PAPER_V1','2099-01-04',975372848.0)"
        )
        con.commit()
        rows = fetch_future_rows(con, "paper_equity_curve", "date", TODAY)
    finally:
        con.close()
    assert len(rows) == 1
    assert rows[0]["date"] == "2099-01-04"
    assert rows[0]["portfolio_id"] == "SEL_PAPER_V1"


# ===================================================================
# 2. Bất biến trên CSDL THẬT (Sentinel cho sổ kế toán)
# ===================================================================
@pytest.mark.parametrize("table", ["paper_equity_curve", "paper_cash_ledger"])
def test_live_paper_ledgers_have_no_future_dates(table):
    """[WHY] Đây là chốt chặn thường trực: nếu một test/script tương lai lại ghi ngày
    sentinel vào CSDL thật, test này đỏ NGAY trong lần chạy kế tiếp, thay vì để
    freshness check chết âm thầm như lần 28/09/2026.
    """
    from src.database.date_sanity import count_future_rows

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        n = count_future_rows(con, table, "date", TODAY)
        sample = [
            r[0]
            for r in con.execute(f"SELECT date FROM {table} WHERE date > ? ORDER BY date LIMIT 5", (TODAY,))
        ]
    finally:
        con.close()
    assert n == 0, f"{table}: {n} dòng ngày tương lai (> {TODAY}), ví dụ {sample}"


def test_live_equity_keeps_real_recent_sessions():
    """[WHY] Chống vá quá tay ở tầng dữ liệu: purge 8 dòng rác KHÔNG được kéo theo
    dòng thật của các phiên vừa chốt sổ. 02/11 và 03/11 phải còn nguyên.
    """
    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        rows = dict(
            con.execute(
                "SELECT date, total_equity FROM paper_equity_curve WHERE date IN ('2026-11-02','2026-11-03')"
            ).fetchall()
        )
    finally:
        con.close()
    assert set(rows) == {"2026-11-02", "2026-11-03"}, f"thiếu dòng phiên thật: {rows}"
    assert rows["2026-11-02"] == pytest.approx(832_482_848.35, abs=1.0)
    assert rows["2026-11-03"] == pytest.approx(841_146_848.35, abs=1.0)
