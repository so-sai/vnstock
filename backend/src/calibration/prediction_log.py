"""prediction_log.py — SQLite persistence for P3 Governor predictions.

Schema:
  prediction_log:
    id          INTEGER PRIMARY KEY
    date        TEXT  (prediction date)
    symbol      TEXT
    p_gain      REAL  (P3 posterior)
    eu          REAL  (Expected Utility of chosen action)
    kelly_alloc REAL  (Kelly allocation %)
    action      TEXT  (REDUCE / HOLD / ...)
    macro_state TEXT
    transmission_phase TEXT
    sector_phase TEXT
    health_archetype TEXT
    valuation_zone TEXT
    behavior_position TEXT
    outcome     REAL  (NULL=unresolved, 1=gain, 0=loss)
    log_loss    REAL  (NULL=unresolved)
    created_at  TEXT
    resolved_at TEXT
"""

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break

DATA_DIR = _candidate / "backend" / "data"
CALIB_DB = DATA_DIR / "calibration.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CALIB_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prediction_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            p_gain          REAL    NOT NULL,
            eu              REAL    NOT NULL,
            kelly_alloc     REAL    NOT NULL,
            action          TEXT    NOT NULL,
            macro_state     TEXT    NOT NULL,
            transmission_phase TEXT NOT NULL,
            sector_phase    TEXT    NOT NULL,
            health_archetype TEXT   NOT NULL,
            valuation_zone  TEXT    NOT NULL,
            behavior_position TEXT  NOT NULL,
            outcome         REAL,
            log_loss        REAL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            resolved_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pred_unresolved
            ON prediction_log(outcome)
            WHERE outcome IS NULL;
        CREATE INDEX IF NOT EXISTS idx_pred_date
            ON prediction_log(date);
        CREATE TABLE IF NOT EXISTS lr_beta_posteriors (
            evidence_key    TEXT PRIMARY KEY,
            alpha           REAL NOT NULL DEFAULT 1.0,
            beta            REAL NOT NULL DEFAULT 1.0,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS calibration_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            n_resolved      INTEGER NOT NULL DEFAULT 0,
            n_unresolved    INTEGER NOT NULL DEFAULT 0,
            mean_log_loss   REAL,
            mean_brier      REAL,
            ece             REAL,
            mce             REAL,
            accuracy        REAL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def insert_prediction(
    date_str: str,
    symbol: str,
    p_gain: float,
    eu: float,
    kelly_alloc: float,
    action: str,
    macro_state: str,
    transmission_phase: str,
    sector_phase: str,
    health_archetype: str,
    valuation_zone: str,
    behavior_position: str,
):
    conn = get_conn()
    conn.execute("""
        INSERT INTO prediction_log
            (date, symbol, p_gain, eu, kelly_alloc, action,
             macro_state, transmission_phase, sector_phase,
             health_archetype, valuation_zone, behavior_position)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        date_str, symbol, p_gain, eu, kelly_alloc, action,
        macro_state, transmission_phase, sector_phase,
        health_archetype, valuation_zone, behavior_position,
    ))
    conn.commit()
    conn.close()


def get_unresolved_predictions() -> List[Dict]:
    """Return all predictions with NULL outcome."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM prediction_log
        WHERE outcome IS NULL
        ORDER BY date ASC, symbol ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_outcome(pred_id: int, outcome: float, log_loss: float):
    """Mark a prediction as resolved with actual outcome and loss."""
    conn = get_conn()
    conn.execute("""
        UPDATE prediction_log
        SET outcome = ?, log_loss = ?, resolved_at = datetime('now','localtime')
        WHERE id = ?
    """, (outcome, log_loss, pred_id))
    conn.commit()
    conn.close()


def get_outcomes_for_calibration(days_back: int = 90) -> List[Dict]:
    """Return resolved predictions within window for calibration."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM prediction_log
        WHERE outcome IS NOT NULL AND date >= ?
        ORDER BY date DESC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_beta_posteriors() -> Dict[str, Tuple[float, float]]:
    """Return all (alpha, beta) tuples keyed by evidence_key."""
    conn = get_conn()
    rows = conn.execute("SELECT evidence_key, alpha, beta FROM lr_beta_posteriors").fetchall()
    conn.close()
    return {r["evidence_key"]: (r["alpha"], r["beta"]) for r in rows}


def upsert_beta(evidence_key: str, alpha: float, beta: float):
    conn = get_conn()
    conn.execute("""
        INSERT INTO lr_beta_posteriors (evidence_key, alpha, beta)
        VALUES (?,?,?)
        ON CONFLICT(evidence_key) DO UPDATE SET
            alpha = excluded.alpha,
            beta = excluded.beta,
            updated_at = datetime('now','localtime')
    """, (evidence_key, alpha, beta))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# Calibration History — time-series tracking of Log-Loss, ECE
# ═══════════════════════════════════════════════════════════════

def init_calibration_history():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            n_resolved      INTEGER NOT NULL DEFAULT 0,
            n_unresolved    INTEGER NOT NULL DEFAULT 0,
            mean_log_loss   REAL,
            mean_brier      REAL,
            ece             REAL,
            mce             REAL,
            accuracy        REAL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def insert_calibration_snapshot(
    date_str: str,
    n_resolved: int,
    n_unresolved: int,
    mean_log_loss: float = 0.0,
    mean_brier: float = 0.0,
    ece: float = 0.0,
    mce: float = 0.0,
    accuracy: float = 0.0,
):
    conn = get_conn()
    conn.execute("""
        INSERT INTO calibration_history
            (date, n_resolved, n_unresolved, mean_log_loss, mean_brier, ece, mce, accuracy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (date_str, n_resolved, n_unresolved, mean_log_loss, mean_brier, ece, mce, accuracy))
    conn.commit()
    conn.close()


def get_calibration_history(days: int = 90) -> List[Dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM calibration_history
        WHERE date >= ?
        ORDER BY date DESC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_calibration() -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM calibration_history
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None
