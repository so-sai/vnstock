"""evidence_ledger.py — Decision Intelligence Ledger (Evidence ≠ Decision).

Ghi MỌI decision candidate (EXECUTE/WATCH/REJECT) của Governor thành 1 row,
tách bạch ba tầng:
  MODEL OUTPUT → Governor assessment → Policy outcome
  (action  = Governor khuyến nghị; decision = Policy cuối cùng)

Nguyên tắc (invariant, xem AGENTS.md + design đã chốt):
  1. WATCH/REJECT cũng ghi — opportunity cost chỉ đo được khi lưu cả thứ
     KHÔNG được thực hiện.
  2. Decision Budget CHỈ tiêu cho NEW_CAPITAL_DEPLOYMENT (triển khai vốn mới),
     không giới hạn khả năng quan sát/suy luận.
  3. Mỗi row giữ evidence_clusters immutable snapshot + metadata phiên bản
     (policy_version/governor_version/evidence_cluster_version) để replay/audit:
     sau này đổi clustering hoặc policy, biết quyết định cũ sinh từ phiên bản nào.
  4. No provenance = no trust: không có nguồn → không lưu cluster.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from calibration.prediction_log import CALIB_DB


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _resolve_db(db_path: str | None) -> Path:
    return Path(db_path) if db_path else CALIB_DB


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decision_ledger (
    decision_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT    NOT NULL,
    date                 TEXT    NOT NULL,
    symbol               TEXT    NOT NULL,
    model_id             TEXT,
    p_gain               REAL,
    eu                   REAL,
    kelly_alloc          REAL,
    action               TEXT    NOT NULL,
    decision             TEXT    NOT NULL,
    decision_reason      TEXT,
    decision_quality     TEXT,
    n_independent_evidence INTEGER NOT NULL DEFAULT 0,
    evidence_clusters    TEXT,
    decision_budget_type TEXT    NOT NULL DEFAULT 'NEW_CAPITAL_DEPLOYMENT',
    decision_budget_year INTEGER NOT NULL,
    slot_consumed        INTEGER NOT NULL DEFAULT 0,
    slot_index           INTEGER,
    entry_price          REAL,
    exit_price           REAL,
    return_pct           REAL,
    return_pct_60        REAL,
    return_pct_120       REAL,
    counterfactual_symbol TEXT,
    counterfactual_return REAL,
    opportunity_cost     REAL,
    opportunity_cost_60  REAL,
    opportunity_cost_120 REAL,
    outcome_status       TEXT    NOT NULL DEFAULT 'UNRESOLVED',
    resolved_at          TEXT,
    policy_version       TEXT    NOT NULL,
    governor_version     TEXT    NOT NULL,
    evidence_cluster_version TEXT NOT NULL,
    decision_budget      INTEGER NOT NULL,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_dl_date      ON decision_ledger(date);
CREATE INDEX IF NOT EXISTS idx_dl_decision  ON decision_ledger(decision);
CREATE INDEX IF NOT EXISTS idx_dl_budget    ON decision_ledger(decision_budget_year, decision_budget_type, slot_consumed);
CREATE INDEX IF NOT EXISTS idx_dl_outcome   ON decision_ledger(outcome_status);
"""

# Phiên bản metadata — đổi khi thay clustering hoặc policy.
POLICY_VERSION = "budget-v1"
GOVERNOR_VERSION = "company_state.v1"
EVIDENCE_CLUSTER_VERSION = "clustering-v1"
DEFAULT_DECISION_BUDGET = 20

# Những action tiêu NEW_CAPITAL_DEPLOYMENT budget.
# Governor trả OPEN (mở vị thế) / SCALE_IN (tích lũy) — cả 2 đều tiêu slot.
# BUY giữ lại để tương thích với callers dùng action mua thô.
CAPITAL_DEPLOYMENT_ACTIONS = {"BUY", "SCALE_IN", "OPEN"}


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_resolve_db(db_path)))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(db_path: str | None = None) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        # Migration: thêm cột multi-horizon cho DB cũ (idempotent).
        for col in ("return_pct_60", "return_pct_120", "opportunity_cost_60", "opportunity_cost_120"):
            cols = [r[1] for r in conn.execute("PRAGMA table_info(decision_ledger)").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE decision_ledger ADD COLUMN {col} REAL")
        conn.commit()
    finally:
        conn.close()


def insert_decision(
    *,
    date: str,
    symbol: str,
    action: str,
    decision: str,
    model_id: str | None = None,
    p_gain: float | None = None,
    eu: float | None = None,
    kelly_alloc: float | None = None,
    decision_reason: str | None = None,
    decision_quality: str | None = None,
    n_independent_evidence: int = 0,
    evidence_clusters: dict | None = None,
    decision_budget_year: int | None = None,
    slot_consumed: bool = False,
    slot_index: int | None = None,
    decision_budget: int = DEFAULT_DECISION_BUDGET,
    entry_price: float | None = None,
    db_path: str | None = None,
    policy_version: str = POLICY_VERSION,
    governor_version: str = GOVERNOR_VERSION,
    evidence_cluster_version: str = EVIDENCE_CLUSTER_VERSION,
    conn: sqlite3.Connection | None = None,
    commit: bool = True,
) -> int:
    """Ghi 1 decision candidate vào ledger. Trả về decision_id.

    Nếu slot_consumed=True và chưa có slot_index, tự cấp slot_index =
    (số slot đã tiêu trong năm + 1).

    conn/commit: replay gom nhiều insert vào 1 transaction (giảm write I/O).
    """
    year = decision_budget_year or int(date[:4])
    slot_idx = slot_index
    if slot_consumed:
        used = get_budget_usage(db_path, year, conn=conn)["used"]
        slot_idx = slot_index if slot_index is not None else used + 1

    own_conn = conn is None
    conn = conn or get_conn(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO decision_ledger (
                timestamp, date, symbol, model_id, p_gain, eu, kelly_alloc,
                action, decision, decision_reason, decision_quality,
                n_independent_evidence, evidence_clusters,
                decision_budget_type, decision_budget_year,
                slot_consumed, slot_index, entry_price,
                policy_version, governor_version, evidence_cluster_version,
                decision_budget
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW_CAPITAL_DEPLOYMENT', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                date,
                symbol,
                model_id,
                p_gain,
                eu,
                kelly_alloc,
                action,
                decision,
                decision_reason,
                decision_quality,
                n_independent_evidence,
                json.dumps(evidence_clusters, ensure_ascii=False) if evidence_clusters else None,
                year,
                1 if slot_consumed else 0,
                slot_idx,
                entry_price,
                policy_version,
                governor_version,
                evidence_cluster_version,
                decision_budget,
            ),
        )
        if commit:
            conn.commit()
        return int(cur.lastrowid)
    finally:
        if own_conn:
            conn.close()


def get_decisions(
    db_path: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    decision: str | None = None,
    budget_year: int | None = None,
    slot_consumed_only: bool = False,
) -> list[dict]:
    """Query ledger. Trả về list dict (evidence_clusters đã parse JSON)."""
    conn = get_conn(db_path)
    try:
        sql = "SELECT * FROM decision_ledger WHERE 1=1"
        params: list = []
        if date_from:
            sql += " AND date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND date <= ?"
            params.append(date_to)
        if decision:
            sql += " AND decision = ?"
            params.append(decision)
        if budget_year is not None:
            sql += " AND decision_budget_year = ?"
            params.append(budget_year)
        if slot_consumed_only:
            sql += " AND slot_consumed = 1"
        sql += " ORDER BY date ASC, decision_id ASC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("evidence_clusters"):
                try:
                    d["evidence_clusters"] = json.loads(d["evidence_clusters"])
                except json.JSONDecodeError, TypeError:
                    pass
            out.append(d)
        return out
    finally:
        conn.close()


def get_budget_usage(
    db_path: str | None = None,
    year: int | None = None,
    budget_type: str = "NEW_CAPITAL_DEPLOYMENT",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Số slot đã tiêu / còn lại trong năm cho một budget_type.

    Returns: {used, budget, remaining}. budget = budget của row mới nhất
    (decision_budget) hoặc DEFAULT_DECISION_BUDGET nếu năm trống.
    conn: reuse connection (batch transaction trong replay).
    """
    if year is None:
        year = int(datetime.now().year)
    own_conn = conn is None
    conn = conn or get_conn(db_path)
    try:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_ledger "
            "WHERE decision_budget_year=? AND decision_budget_type=? AND slot_consumed=1",
            (year, budget_type),
        ).fetchone()["n"]
        budget_row = conn.execute(
            "SELECT decision_budget FROM decision_ledger "
            "WHERE decision_budget_year=? AND decision_budget_type=? ORDER BY decision_id DESC LIMIT 1",
            (year, budget_type),
        ).fetchone()
        budget = budget_row["decision_budget"] if budget_row else DEFAULT_DECISION_BUDGET
        return {"used": used, "budget": budget, "remaining": max(0, budget - used)}
    finally:
        if own_conn:
            conn.close()
