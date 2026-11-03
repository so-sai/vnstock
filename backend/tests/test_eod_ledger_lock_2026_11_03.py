# -*- coding: utf-8 -*-
"""Sổ cái EOD (`eod_run_ledger`) không được chết vì khóa SQLite (Mục 21, 03/11/2026).

[WHY]
    `eod_run_ledger` đứng yên ở `2026-07-16` (23 dòng) suốt 3,5 tháng. Log mỗi phiên có
    một dòng "catch-up thất bại (non-blocking): database is locked" — nhưng vì nó
    non-blocking nên EOD vẫn SUCCESS và không ai truy vết. Hậu quả: lớp bảo vệ lũy đẳng
    thứ nhất (ledger) chết âm thầm, chỉ còn lớp thứ hai (`paper_equity_curve`).

[ROOT CAUSE]
    `_catchup_gap_days()` chạy BÊN TRONG `global_transaction()` (đang giữ `BEGIN IMMEDIATE`
    = write lock). Nhưng các helper nó gọi — `_ensure_ledger_schema()`, `_ledger_succeeded()`,
    `_record_ledger()` — đều tự mở KẾT NỐI RIÊNG và ghi/DDL. Trong SQLite, writer thứ hai
    chờ write lock tới hết `busy_timeout` rồi ném `database is locked`; catch-up bị nuốt
    bởi `except Exception` (non-blocking) ⇒ ledger không bao giờ tiến.

[INVARIANT]
    (a) Trong transaction, mọi ghi ledger PHẢI dùng chính `conn` của transaction (một
        writer duy nhất, chịu ROLLBACK chung).
    (b) Ngoài transaction (ledger fallback sau rollback), writer PHẢI retry với
        exponential backoff khi gặp "database is locked".
    (c) Lỗi KHÁC lock (schema sai, dữ liệu sai) không được retry — nếu không sẽ che
        giấu lỗi thật và làm chậm pipeline.
"""

import ast
import contextlib
import inspect
import sqlite3
import sys
import textwrap
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


def test_records_into_injected_connection(tmp_path):
    """[WHY] Đây là bản vá gốc: khi có `conn` của transaction, ledger phải ghi vào CHÍNH
    kết nối đó (không mở kết nối thứ hai để tranh khóa). Chứng minh bằng DB tạm: nếu code
    vẫn mở kết nối riêng, dòng sẽ không bao giờ xuất hiện trong file tạm này.
    """
    from src.engine.eod_runner import _record_ledger

    db = tmp_path / "ledger.db"
    con = sqlite3.connect(str(db))
    try:
        _record_ledger("2026-11-03", "P_TEST", "SUCCESS", conn=con)
        con.commit()
        row = con.execute(
            "SELECT status, attempts FROM eod_run_ledger WHERE as_of_date=? AND portfolio_id=?",
            ("2026-11-03", "P_TEST"),
        ).fetchone()
    finally:
        con.close()
    assert row == ("SUCCESS", 0)


def test_retries_on_locked_then_succeeds(tmp_path, monkeypatch):
    """[WHY] Đường ledger fallback chạy SAU khi transaction đóng, nhưng cron/agent khác có
    thể vẫn đang giữ khóa. Retry + backoff phải cứu được, thay vì bỏ mất bản ghi.
    """
    from src.engine import eod_runner

    db = tmp_path / "ledger_retry.db"
    calls = {"n": 0}

    @contextlib.contextmanager
    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        con = sqlite3.connect(str(db))
        try:
            yield con
            con.commit()
        finally:
            con.close()

    monkeypatch.setattr(eod_runner, "get_connection", flaky)
    eod_runner._record_ledger("2026-11-03", "P_RETRY", "SUCCESS", base_delay=0.0)

    assert calls["n"] == 3, "phải thử lại đúng 2 lần rồi thành công"
    con = sqlite3.connect(str(db))
    try:
        assert con.execute(
            "SELECT status FROM eod_run_ledger WHERE as_of_date='2026-11-03' AND portfolio_id='P_RETRY'"
        ).fetchone() == ("SUCCESS",)
    finally:
        con.close()


def test_gives_up_after_max_retries(monkeypatch):
    """[WHY] Chống treo vô hạn: khóa kéo dài phải ném lỗi sau số lần giới hạn để EOD không
    bị treo cả phiên (TRANSACTION_TIMEOUT / ledger fallback vẫn phải kết thúc).
    """
    from src.engine import eod_runner

    calls = {"n": 0}

    @contextlib.contextmanager
    def always_locked():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")
        yield  # pragma: no cover - không bao giờ tới

    monkeypatch.setattr(eod_runner, "get_connection", always_locked)
    with pytest.raises(sqlite3.OperationalError):
        eod_runner._record_ledger("2026-11-03", "P_LOCK", "SUCCESS", base_delay=0.0)
    assert calls["n"] == eod_runner.LEDGER_RETRIES


def test_does_not_retry_non_lock_errors(monkeypatch):
    """[WHY] Chống vá quá tay: `no such column`/`no such table` là lỗi schema — retry chỉ
    làm chậm và che giấu lỗi thật. Phải ném ngay ở lần đầu.
    """
    from src.engine import eod_runner

    calls = {"n": 0}

    @contextlib.contextmanager
    def broken_schema():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: eod_run_ledger")
        yield  # pragma: no cover

    monkeypatch.setattr(eod_runner, "get_connection", broken_schema)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        eod_runner._record_ledger("2026-11-03", "P_BAD", "SUCCESS", base_delay=0.0)
    assert calls["n"] == 1, "không được retry lỗi không phải lock"


def _calls_with_conn(func, name: str) -> list[ast.Call]:
    """Các lời gọi `name(...)` trong `func` có truyền `conn=` (phân tích AST)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name:
            if any(kw.arg == "conn" for kw in node.keywords):
                out.append(node)
    return out


def _all_calls(func, name: str) -> list[ast.Call]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == name
    ]


def test_catchup_threads_transaction_conn_into_ledger_helpers():
    """[WHY] Test cấu trúc khoá ĐÚNG nguyên nhân gốc: bên trong transaction, mọi helper
    ledger (`_ledger_succeeded`, `_record_ledger`, `detect_gap_days`) phải nhận
    `conn=conn`. Chỉ cần một chỗ quên là khóa lại xuất hiện và catch-up chết im lặng.
    Dùng AST thay vì so khớp chuỗi: một lời gọi xuống dòng không được phép làm test
    luận ra "đã sửa" (hoặc "chưa sửa") một cách sai lệch.
    """
    from src.engine import eod_runner

    ledger_calls = _all_calls(eod_runner._catchup_gap_days, "_record_ledger")
    assert len(ledger_calls) >= 3, f"catch-up phải ghi ledger ở 3 nhánh, thấy {len(ledger_calls)}"
    assert len(_calls_with_conn(eod_runner._catchup_gap_days, "_record_ledger")) == len(ledger_calls), (
        "mọi _record_ledger trong catch-up phải truyền conn=conn"
    )
    assert _calls_with_conn(eod_runner._catchup_gap_days, "detect_gap_days"), (
        "detect_gap_days phải nhận conn=conn (nó ghi ledger để đồng bộ phiên cũ)"
    )

    gap_ledger = _all_calls(eod_runner.detect_gap_days, "_record_ledger")
    assert gap_ledger and len(_calls_with_conn(eod_runner.detect_gap_days, "_record_ledger")) == len(gap_ledger)
    assert _calls_with_conn(eod_runner.detect_gap_days, "_ledger_succeeded")


def test_ledger_write_joins_transaction_and_rolls_back(tmp_path):
    """[WHY] Chống vá quá tay theo hướng ngược lại: ghi ledger trên conn transaction
    KHÔNG được tự commit. Nếu `record_eod_write_audit(conn=...)` (vốn có `commit()` và
    `executescript()` — thứ phát COMMIT ngầm) chốt transaction, thì ROLLBACK của
    `global_transaction` mất tác dụng và bút toán dở dang nằm lại vĩnh viễn.
    Test mô phỏng đúng giao thức: BEGIN IMMEDIATE → ghi ledger → ROLLBACK → phải sạch.
    """
    from src.engine.eod_runner import _record_ledger

    db = tmp_path / "txn.db"
    con = sqlite3.connect(str(db))
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        _record_ledger("2026-11-03", "P_TXN", "SUCCESS", conn=con)
        assert con.in_transaction, "ghi ledger không được chốt transaction của caller"
        con.execute("ROLLBACK")

        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        n_ledger = (
            con.execute("SELECT COUNT(*) FROM eod_run_ledger").fetchone()[0] if "eod_run_ledger" in tables else 0
        )
        n_audit = (
            con.execute("SELECT COUNT(*) FROM eod_write_audit_ledger").fetchone()[0]
            if "eod_write_audit_ledger" in tables
            else 0
        )
    finally:
        con.close()
    assert n_ledger == 0, "ledger phải rollback cùng transaction"
    assert n_audit == 0, "audit phải rollback cùng transaction (không được tự commit)"
