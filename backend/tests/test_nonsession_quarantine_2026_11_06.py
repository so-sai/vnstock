# -*- coding: utf-8 -*-
"""Cách ly dòng trên NGÀY KHÔNG GIAO DỊCH — công cụ trả nợ Mục 28 #3 (NQ-18 §II.1).

[WHY]
    Chốt #3 chiều ngược (`20295b5`) đã phát hiện 3 dòng trên ngày Quốc khánh `2026-09-01`
    nhưng KHÔNG có đường thanh lý. Một cổng đỏ vĩnh viễn mà không có công cụ trả nợ sẽ bị
    tắt trong tuần đầu (bài học 31/08/2026). Bộ test này khoá cả HAI mặt:
      (a) công cụ xoá ĐÚNG dòng ma, để lại audit `RESTATED` đầy đủ;
      (b) công cụ TỪ CHỐI xoá khi "ngày không phải phiên" thực ra là LỊCH SAI.

[ROOT CAUSE được bảo vệ]
    Ca `2024-02-15/16/2024` (1.092 và 1.139 bar THẬT) chứng minh: nếu công cụ tin lịch mà
    xoá, nó sẽ phá huỷ phiên thật. Vì vậy luật là ">= 100 bar hợp lệ ⇒ CẤM XOÁ".

[INVARIANT]
    (a) dry-run (mặc định) không sửa gì;
    (b) xoá + audit cùng transaction, audit lỗi ⇒ rollback (dòng vẫn còn);
    (c) sau khi xoá phải tự kiểm lại; không tin lời;
    (d) xoá theo `rowid` + ngày ⇒ không lan sang phiên bên cạnh;
    (e) chạy lại lần hai là NO-OP (idempotent), không sinh audit rác.
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

PHANTOM = "2026-09-01"  # Quốc khánh 2026 — trong SSOT và trong NON_TRADING_SESSIONS
AS_OF = "2026-11-05"


def _sessions():
    from src.database.session_continuity import session_window_end

    return session_window_end(AS_OF, 60)


def _db(phantom_tables=("market_wide_breadth_daily",), phantom_bars=0):
    """Dựng 5 bảng trên đúng 60 phiên; chèn dòng ma ở `PHANTOM` theo yêu cầu."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, is_stale INTEGER DEFAULT 0)"
    )
    con.execute(
        "CREATE TABLE market_wide_breadth_daily (date TEXT PRIMARY KEY, label TEXT, "
        "total_active INTEGER, advances INTEGER, declines INTEGER, unchanged INTEGER, "
        "breadth_pct REAL, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE regime_history (date TEXT PRIMARY KEY, regime_score REAL, status TEXT, "
        "breadth_pct REAL)"
    )
    con.execute(
        "CREATE TABLE paper_equity_curve (portfolio_id TEXT, date TEXT, total_equity REAL, "
        "created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE eod_run_ledger (as_of_date TEXT, portfolio_id TEXT, status TEXT, "
        "created_at TEXT)"
    )

    sessions = _sessions()
    for d in sessions:
        con.executemany(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,0)",
            [(f"S{i}", d, 100.0) for i in range(120)],
        )
        con.execute("INSERT INTO market_wide_breadth_daily VALUES (?,?,?,?,?,?,?,?)",
                    (d, "ALL", 900, 400, 400, 100, 0.0, "2026-11-05T15:00:00"))
        con.execute("INSERT INTO regime_history VALUES (?,?,?,?)", (d, 0.5, "RANGING", 0.0))
        con.execute("INSERT INTO paper_equity_curve VALUES (?,?,?,?)",
                    ("SEL_PAPER_V1", d, 1_000_000_000.0, "2026-11-05T15:00:00"))
        con.execute("INSERT INTO eod_run_ledger VALUES (?,?,?,?)",
                    (d, "SEL_PAPER_V1", "SUCCESS", "2026-11-05T15:00:00"))

    if "market_wide_breadth_daily" in phantom_tables:
        con.execute("INSERT INTO market_wide_breadth_daily VALUES (?,?,?,?,?,?,?,?)",
                    (PHANTOM, "ALL", 939, 1, 0, 938, 0.106, "2026-10-15T11:45:24"))
    if "regime_history" in phantom_tables:
        con.execute("INSERT INTO regime_history VALUES (?,?,?,?)", (PHANTOM, 0.61, "RANGING", 0.0))
    if "paper_equity_curve" in phantom_tables:
        con.execute("INSERT INTO paper_equity_curve VALUES (?,?,?,?)",
                    ("SEL_PAPER_V1", PHANTOM, 1_008_488_848.3466899, "2026-09-01T15:45:57"))
    if phantom_bars:
        con.executemany(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,0)",
            [(f"P{i}", PHANTOM, 100.0) for i in range(phantom_bars)],
        )
    con.commit()
    return con


def _audit_rows(con):
    try:
        return list(con.execute(
            "SELECT as_of_date, target_table, status, notes, parameters "
            "FROM eod_write_audit_ledger WHERE status='RESTATED' ORDER BY audit_id"
        ))
    except sqlite3.OperationalError:
        return []


# ===================================================================
# 1. Quét + cách ly đúng dòng ma
# ===================================================================
def test_scan_finds_exactly_the_phantom_rows():
    """[WHY] Nếu quét bỏ sót, lệnh `--apply` sẽ báo "đã dọn" trong khi cổng Chốt #3 vẫn đỏ;
    nếu quét THỪA, lệnh sẽ đụng vào phiên thật. Cả hai đều là lỗi toàn vẹn dữ liệu.
    """
    from src.database.nonsession_quarantine import scan_nonsession_rows

    con = _db(("market_wide_breadth_daily", "regime_history", "paper_equity_curve"))
    try:
        report = scan_nonsession_rows(con)
    finally:
        con.close()
    assert report["count"] == 3, report
    assert sorted(r["table"] for r in report["rows"]) == [
        "market_wide_breadth_daily",
        "paper_equity_curve",
        "regime_history",
    ]
    assert all(r["date"] == PHANTOM for r in report["rows"])
    assert report["conflicts"] == []


def test_quarantine_deletes_rows_and_writes_full_audit():
    """[WHY] Audit phải chứa NGUYÊN BẢN payload cũ: sau khi xoá, đó là bằng chứng duy nhất về
    "đã xoá cái gì". Thiếu payload thì 3 tháng sau không ai tái lập được.
    """
    from src.database.nonsession_quarantine import quarantine_nonsession_rows, scan_nonsession_rows

    con = _db(("market_wide_breadth_daily", "regime_history", "paper_equity_curve"))
    try:
        report = scan_nonsession_rows(con)
        result = quarantine_nonsession_rows(
            con, report, caller_module="test", reason="PHANTOM_NON_SESSION: kiểm thử"
        )
        audit = _audit_rows(con)
        after = scan_nonsession_rows(con)
    finally:
        con.close()

    assert result["deleted"] == 3, result
    assert len(result["audit_ids"]) == 3, "mỗi (bảng, ngày) đúng MỘT dòng audit"
    assert after["clean"] is True, after
    assert len(audit) == 3
    tables = {a[1] for a in audit}
    assert tables == {"market_wide_breadth_daily", "regime_history", "paper_equity_curve"}
    assert all(a[0] == PHANTOM for a in audit)
    assert "PHANTOM_NON_SESSION" in audit[0][3]
    # [WHY] Payload cũ phải được lưu TRỌN: tìm đúng dòng của `paper_equity_curve` (audit sắp
    # theo tên bảng) để chắc chắn `total_equity` của phiên ma vẫn dựng lại được từ sổ kiểm toán.
    equity_audit = [a for a in audit if a[1] == "paper_equity_curve"]
    assert len(equity_audit) == 1
    assert "1008488848.3466899" in equity_audit[0][4], equity_audit[0][4]


# ===================================================================
# 2. Chống vá quá tay — CẤM xoá dữ liệu thật
# ===================================================================
def test_non_session_day_with_real_bars_is_refused_not_deleted():
    """[WHY] ĐÂY LÀ TEST QUAN TRỌNG NHẤT của file này. Ca `2024-02-15` (1.092 bar thật,
    vol 1,99 tỷ) từng bị cả Rust và Python coi là nghỉ. Nếu công cụ tin lịch và xoá, nó sẽ
    xoá mất hai phiên giao dịch thật. Luật phải là: có bar thật ⇒ LỊCH SAI ⇒ dừng.
    """
    from src.database.nonsession_quarantine import (
        CalendarDataConflictError,
        _valid_bars,
        quarantine_nonsession_rows,
        scan_nonsession_rows,
    )

    con = _db(("market_wide_breadth_daily",), phantom_bars=120)
    try:
        report = scan_nonsession_rows(con)
        assert report["conflicts"], "phải phát hiện xung đột lịch-dữ liệu"
        assert report["conflicts"][0]["valid_bars"] == 120

        with pytest.raises(CalendarDataConflictError) as exc:
            quarantine_nonsession_rows(con, report, caller_module="test", reason="thử xoá")
        assert "LỊCH SAI" in str(exc.value)

        # Không một dòng nào bị xoá, không một audit nào được ghi.
        assert _valid_bars(con, PHANTOM) == 120
        assert len(scan_nonsession_rows(con)["rows"]) >= 1
        assert _audit_rows(con) == []
    finally:
        con.close()


def test_stale_rows_on_non_session_day_are_not_treated_as_real_data():
    """[WHY] Chống vá quá tay theo chiều ngược lại: `is_stale=1` KHÔNG phải dữ liệu thị
    trường thật (nó là bar cũ được đánh dấu). Nếu tính cả bar stale là "thật", một ngày lễ có
    vài dòng stale sẽ chặn việc dọn dẹp và cổng đỏ vĩnh viễn.
    """
    from src.database.nonsession_quarantine import scan_nonsession_rows

    con = _db(("market_wide_breadth_daily",))
    try:
        con.executemany(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,1)",
            [(f"X{i}", PHANTOM, 100.0) for i in range(150)],
        )
        con.commit()
        report = scan_nonsession_rows(con)
    finally:
        con.close()
    assert report["conflicts"] == [], "bar is_stale=1 không phải dữ liệu thật"
    assert any(r["table"] == "daily_ohlcv" for r in report["rows"])


# ===================================================================
# 3. Fail-closed: audit lỗi ⇒ rollback
# ===================================================================
def test_audit_failure_rolls_back_the_deletion(monkeypatch):
    """[WHY] Chốt #5 đặt nguyên tắc "audit và bút toán cùng transaction". Nếu audit lỗi mà
    dòng vẫn bị xoá thì ta có đúng thứ nguy hiểm nhất: dữ liệu đã mất, không ai biết vì sao.
    """
    import src.database.nonsession_quarantine as nq

    con = _db(("market_wide_breadth_daily", "regime_history"))
    try:
        report = nq.scan_nonsession_rows(con)

        def _boom(*a, **k):
            raise RuntimeError("audit ledger hỏng")

        monkeypatch.setattr(nq, "record_eod_write_audit", _boom)
        with pytest.raises(RuntimeError, match="audit ledger hỏng"):
            nq.quarantine_nonsession_rows(con, report, caller_module="test", reason="thử")

        after = nq.scan_nonsession_rows(con)
        assert after["count"] == 2, "rollback: 2 dòng ma phải còn nguyên"
    finally:
        con.close()


def test_empty_reason_or_caller_is_rejected():
    """[WHY] Một lần xoá không lý do / không người chịu trách nhiệm là "xoá lén" — đúng thứ
    Chốt #5 được xây để chặn.
    """
    from src.database.nonsession_quarantine import (
        quarantine_nonsession_rows,
        scan_nonsession_rows,
    )

    con = _db(("market_wide_breadth_daily",))
    try:
        report = scan_nonsession_rows(con)
        with pytest.raises(ValueError, match="reason"):
            quarantine_nonsession_rows(con, report, caller_module="test", reason="   ")
        with pytest.raises(ValueError, match="caller_module"):
            quarantine_nonsession_rows(con, report, caller_module="", reason="có lý do")
        assert scan_nonsession_rows(con)["count"] == 1, "không được xoá gì"
    finally:
        con.close()


# ===================================================================
# 4. Idempotent
# ===================================================================
def test_second_run_is_noop_and_writes_no_extra_audit():
    """[WHY] Lệnh này sẽ được chạy lại (cron/ops). Chạy lại phải là NO-OP, nếu không mỗi lần
    chạy lại sinh một dòng `RESTATED` rác và làm nhiễu sổ kiểm toán.
    """
    from src.database.nonsession_quarantine import (
        quarantine_nonsession_rows,
        scan_nonsession_rows,
    )

    con = _db(("market_wide_breadth_daily",))
    try:
        first = quarantine_nonsession_rows(
            con, scan_nonsession_rows(con), caller_module="test", reason="lần 1"
        )
        assert first["deleted"] == 1
        second_report = scan_nonsession_rows(con)
        assert second_report["clean"] is True
        second = quarantine_nonsession_rows(
            con, second_report, caller_module="test", reason="lần 2"
        )
        assert second["deleted"] == 0
        assert second["audit_ids"] == []
        assert len(_audit_rows(con)) == 1
    finally:
        con.close()


# ===================================================================
# 5. CSDL THẬT — nợ phải NHÌN THẤY ĐƯỢC (không có ngày ma MỚI)
# ===================================================================
def test_live_scan_is_clean_after_quarantine():
    """[WHY] Hai điều phải đúng trên dữ liệu sống:
    (a) KHÔNG có xung đột "ngày nghỉ mà có bar thật" — nếu có, lịch (SSOT) vừa sai lại;
    (b) KHÔNG còn dòng nào trên ngày không giao dịch (kể cả toàn lịch sử). Trước 06/11/2026
    tồn tại 3 dòng ở `2026-09-01`; chúng đã được cách ly kèm audit RESTATED #3478–3480.
    Đây là khẳng định CHẶT HƠN, không phải miễn trừ: một dòng ma mới ⇒ đỏ.
    """
    from src.database.nonsession_quarantine import scan_nonsession_rows

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = scan_nonsession_rows(con, include_history=True)
    finally:
        con.close()

    assert report["conflicts"] == [], report["conflicts"]
    assert report["rows"] == [], f"còn dòng trên ngày không giao dịch: {report['rows']}"
    assert report["clean"] is True
