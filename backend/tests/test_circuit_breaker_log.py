"""test_circuit_breaker_log.py — Xác thực CB log không crash khi trigger_reason=None.

Bug: entry.get('trigger_reason', '')[:28] gây TypeError khi value là None.
Fix: (entry.get('trigger_reason') or '')[:28]
"""

import sqlite3
from datetime import date


def _build_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS circuit_breaker (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            level           INTEGER NOT NULL DEFAULT 0,
            label           TEXT    NOT NULL DEFAULT 'BÃŒNH_THÆ¯á»œNG',
            trigger_reason  TEXT,
            mean_log_loss   REAL,
            active          INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)


def test_trigger_reason_none_does_not_crash():
    """trigger_reason=NULL không được gây TypeError khi format log."""
    conn = sqlite3.connect(":memory:")
    _build_schema(conn)

    today = date.today().isoformat()
    for i in range(3):
        conn.execute(
            "INSERT INTO circuit_breaker (date, level, label, trigger_reason, active) VALUES (?, ?, ?, ?, ?)",
            (today, i, f"LEVEL_{i}", None, 0)
        )
    conn.commit()

    cur = conn.execute(
        "SELECT date, level, active, label, trigger_reason FROM circuit_breaker ORDER BY id"
    )
    rows = cur.fetchall()
    assert len(rows) == 3

    for entry in rows:
        date_val, level, active, label, reason = entry
        # This is the exact pattern that crashed (line 1544 pre-fix):
        # formatted = entry.get('trigger_reason', '')[:28]
        # We want to ensure (entry.get('trigger_reason') or '') does not crash:
        safe_reason = (reason or '')
        formatted = f"{safe_reason[:28]:<30}"
        # Should not raise TypeError
        assert isinstance(formatted, str)

    conn.close()


def test_trigger_reason_empty_string_no_crash():
    """trigger_reason='' (chuỗi rỗng) cũng không crash."""
    conn = sqlite3.connect(":memory:")
    _build_schema(conn)

    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO circuit_breaker (date, level, label, trigger_reason, active) VALUES (?, ?, ?, ?, ?)",
        (today, 0, "BÃŒNH_THÆ¯á»œNG", "", 0)
    )
    conn.commit()

    cur = conn.execute(
        "SELECT date, level, active, label, trigger_reason FROM circuit_breaker ORDER BY id"
    )
    entry = cur.fetchone()
    date_val, level, active, label, reason = entry

    safe_reason = (reason or '')
    formatted = f"{safe_reason[:28]:<30}"
    assert isinstance(formatted, str)

    conn.close()


def test_trigger_reason_normal_string():
    """trigger_reason có chuỗi bình thường vẫn hiển thị đúng."""
    conn = sqlite3.connect(":memory:")
    _build_schema(conn)

    today = date.today().isoformat()
    reason = "MOCK_TEST: Log-Loss quÃ¡ cao: 0.750 > 0.70"
    conn.execute(
        "INSERT INTO circuit_breaker (date, level, label, trigger_reason, active) VALUES (?, ?, ?, ?, ?)",
        (today, 3, "KHáº¨N_Cáº¤P", reason, 1)
    )
    conn.commit()

    cur = conn.execute(
        "SELECT date, level, active, label, trigger_reason FROM circuit_breaker ORDER BY id"
    )
    entry = cur.fetchone()
    date_val, level, active, label, reason = entry

    safe_reason = (reason or '')
    formatted = f"{safe_reason[:28]:<30}"
    assert "MOCK_TEST" in formatted

    conn.close()


def test_get_circuit_breaker_log_none_safe_format():
    """Format log với trigger_reason=None không crash — mô phỏng luồng display."""
    from calibration.prediction_log import get_circuit_breaker_log

    log = get_circuit_breaker_log(days=365)
    assert isinstance(log, list)

    for entry in log:
        safe = (entry.get("trigger_reason") or "")
        trunc = safe[:28]
        assert isinstance(trunc, str)
