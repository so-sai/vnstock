"""eod_write_audit.py — Sổ cái ghi nhật ký bất biến (Append-only Write Audit Ledger) cho EOD Pipeline.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Mọi hành động ghi/cập nhật vào các bảng cốt lõi (regime_history, market_wide_breadth_daily, eod_run_ledger)
  phải được lưu vết kiểm toán bất biến (append-only) thay vì ghi đè âm thầm (UPSERT / INSERT OR REPLACE).
ROOT CAUSE:
  Trước đây save_data_upsert và INSERT OR REPLACE ghi đè trực tiếp mà không ghi nhận caller, parameters,
  input_hash và output_score, dẫn đến việc bug EMA self-seeding ghi đè score 0.3459 -> 0.4551 mà không
  để lại dấu vết nguồn gốc (provenance).
INVARIANT:
  1. Bảng eod_write_audit_ledger là APPEND-ONLY: chỉ INSERT, cấm UPDATE và DELETE.
  2. Mọi lần ghi/cập nhật vào bảng cốt lõi EOD PHẢI ghi nhận một bản ghi audit kèm input_hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from src.database.db_core import get_connection

logger = logging.getLogger("PTCK_SYSTEM")


def _calculate_input_hash(data: Any) -> str:
    """Tính SHA256 checksum rút gọn (16 ký tự) đại diện cho input payload."""
    try:
        if isinstance(data, (dict, list)):
            serialized = json.dumps(data, sort_keys=True, default=str)
        else:
            serialized = str(data)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    except TypeError, ValueError, AttributeError:
        return "UNKNOWN_HASH"


def ensure_audit_ledger_schema(conn=None) -> None:
    """Tạo bảng eod_write_audit_ledger nếu chưa tồn tại (idempotent DDL)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS eod_write_audit_ledger (
        audit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        run_timestamp TEXT NOT NULL,
        as_of_date    TEXT NOT NULL,
        caller_module TEXT NOT NULL,
        target_table  TEXT NOT NULL,
        parameters    TEXT,
        input_hash    TEXT,
        output_score  REAL,
        status        TEXT DEFAULT 'SUCCESS',
        notes         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_audit_table_date ON eod_write_audit_ledger (target_table, as_of_date);
    """
    if conn is not None:
        conn.executescript(ddl)
        conn.commit()
    else:
        with get_connection() as c:
            c.executescript(ddl)
            c.commit()


def record_eod_write_audit(
    as_of_date: str,
    caller_module: str,
    target_table: str,
    parameters: Any = None,
    input_data: Any = None,
    output_score: float | None = None,
    status: str = "SUCCESS",
    notes: str | None = None,
    conn=None,
) -> int:
    """Ghi một dòng bất biến (append-only) vào sổ cái kiểm toán eod_write_audit_ledger."""
    ensure_audit_ledger_schema(conn=conn)
    now_iso = datetime.now().isoformat()

    params_str = (
        json.dumps(parameters, default=str)
        if isinstance(parameters, (dict, list))
        else (str(parameters) if parameters is not None else None)
    )
    input_hash = _calculate_input_hash(input_data) if input_data is not None else _calculate_input_hash(params_str)

    sql = """
    INSERT INTO eod_write_audit_ledger
        (run_timestamp, as_of_date, caller_module, target_table, parameters, input_hash, output_score, status, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    args = (now_iso, as_of_date, caller_module, target_table, params_str, input_hash, output_score, status, notes)

    if conn is not None:
        cur = conn.execute(sql, args)
        conn.commit()
        audit_id = cur.lastrowid
    else:
        with get_connection() as c:
            cur = c.execute(sql, args)
            c.commit()
            audit_id = cur.lastrowid

    logger.info(
        "[AUDIT_LEDGER] #%d | %s | table=%s date=%s caller=%s score=%s status=%s",
        audit_id,
        now_iso,
        target_table,
        as_of_date,
        caller_module,
        f"{output_score:.4f}" if output_score is not None else "None",
        status,
    )
    return audit_id
