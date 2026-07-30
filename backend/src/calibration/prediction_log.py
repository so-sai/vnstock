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
        CREATE TABLE IF NOT EXISTS evidence_registry (
            node_id        TEXT PRIMARY KEY,
            alpha          REAL DEFAULT 10.0,
            beta           REAL DEFAULT 10.0,
            brier_accum    REAL DEFAULT 0.0,
            n_updates      INTEGER DEFAULT 0,
            ece_score      REAL DEFAULT 0.0,
            reliability    REAL DEFAULT 0.5,
            drift_score    REAL DEFAULT 0.0,
            applicability  REAL DEFAULT 1.0,
            last_updated   TEXT
        );
        CREATE TABLE IF NOT EXISTS circuit_breaker (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            level           INTEGER NOT NULL DEFAULT 0,
            label           TEXT    NOT NULL DEFAULT 'BÌNH_THƯỜNG',
            trigger_reason  TEXT,
            mean_log_loss   REAL,
            threshold       REAL,
            recent_avg_ll   REAL,
            older_avg_ll    REAL,
            active          INTEGER NOT NULL DEFAULT 0,
            resolved_at     TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def init_model_registry_schema():
    """Initialize model_registry table for Sprint 4 Competing Hypotheses Engine.
    
    WHY separate table from prediction_log? model_registry tracks hypothesis-level
    lifecycle (state transitions ACTIVE/DORMANT/RETIRED) and BMA weights, while
    prediction_log tracks per-symbol Governor decisions. The two tables join on
    date for audit: "which hypothesis dominated when and why."
    """
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            model_id        TEXT PRIMARY KEY,
            hypothesis      TEXT NOT NULL,
            hypothesis_en   TEXT DEFAULT '',
            prior           REAL DEFAULT 0.333,
            posterior       REAL DEFAULT 0.333,
            state           TEXT DEFAULT 'ACTIVE',
            regime_fit      TEXT DEFAULT '{}',
            n_trades        INTEGER DEFAULT 0,
            n_wins          INTEGER DEFAULT 0,
            sharpe          REAL DEFAULT 0.0,
            max_drawdown    REAL DEFAULT 0.0,
            brier_accum     REAL DEFAULT 0.0,
            log_loss_accum  REAL DEFAULT 0.0,
            counter_signals INTEGER DEFAULT 0,
            last_active     TEXT,
            retirement_reason TEXT DEFAULT '',
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id        TEXT NOT NULL,
            state           TEXT NOT NULL,
            posterior       REAL NOT NULL,
            reason          TEXT DEFAULT '',
            changed_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
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


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker — tự động đóng băng vị thế khi degradation
# ═══════════════════════════════════════════════════════════════

CB_LEVEL_NONE = 0       # Hoạt động bình thường
CB_LEVEL_CAUTION = 1    # Degradation nhẹ → chặn OPEN
CB_LEVEL_ACTIVE = 2     # Degradation mạnh → chặn OPEN/SCALE_IN, hạ HOLD
CB_LEVEL_EMERGENCY = 3  # Log-Loss rất cao → đóng băng toàn bộ

CB_LABELS = {
    CB_LEVEL_NONE: "BÌNH_THƯỜNG",
    CB_LEVEL_CAUTION: "CẢNH_BÁO",
    CB_LEVEL_ACTIVE: "KÍCH_HOẠT",
    CB_LEVEL_EMERGENCY: "KHẨN_CẤP",
}

CB_ACTION_MAP = {
    # (current_level, original_action) → (overridden_action, capped_alloc)
    CB_LEVEL_CAUTION: {
        "OPEN": ("SCALE_IN", None),
        "SCALE_IN": ("HOLD", None),
    },
    CB_LEVEL_ACTIVE: {
        "OPEN": ("REDUCE", -5.0),
        "SCALE_IN": ("REDUCE", -5.0),
        "HOLD": ("REDUCE", -5.0),
    },
    CB_LEVEL_EMERGENCY: {
        "OPEN": ("VETO", 0.0),
        "SCALE_IN": ("VETO", 0.0),
        "HOLD": ("VETO", 0.0),
        "REDUCE": ("VETO", 0.0),
        "WAIT": ("VETO", 0.0),
        "AVOID": ("VETO", 0.0),
    },
}


def init_circuit_breaker():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS circuit_breaker (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            level           INTEGER NOT NULL DEFAULT 0,
            label           TEXT    NOT NULL DEFAULT 'BÌNH_THƯỜNG',
            trigger_reason  TEXT,
            mean_log_loss   REAL,
            threshold       REAL,
            recent_avg_ll   REAL,
            older_avg_ll    REAL,
            active          INTEGER NOT NULL DEFAULT 0,
            resolved_at     TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def get_circuit_breaker_state() -> dict:
    """Return latest circuit breaker state. Auto-creates initial entry if empty."""
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM circuit_breaker
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    if row:
        r = dict(row)
        conn.close()
        return r
    # Default: inactive, level 0
    conn.execute("""
        INSERT INTO circuit_breaker (date, level, label, active)
        VALUES (?, 0, 'BÌNH_THƯỜNG', 0)
    """, (str(date.today()),))
    conn.commit()
    conn.close()
    return {"level": 0, "label": "BÌNH_THƯỜNG", "active": 0}


def set_circuit_breaker(
    level: int,
    trigger_reason: str = "",
    mean_log_loss: Optional[float] = None,
    threshold: float = 0.05,
    recent_avg_ll: Optional[float] = None,
    older_avg_ll: Optional[float] = None,
):
    """Record new circuit breaker state. Previous active entry is auto-resolved."""
    today = str(date.today())
    label = CB_LABELS.get(level, "UNKNOWN")
    active = 1 if level > 0 else 0

    conn = get_conn()
    # Resolve previous active entry
    conn.execute("""
        UPDATE circuit_breaker
        SET resolved_at = datetime('now','localtime')
        WHERE active = 1 AND resolved_at IS NULL
    """)
    # Insert new state
    conn.execute("""
        INSERT INTO circuit_breaker
            (date, level, label, active, trigger_reason,
             mean_log_loss, threshold, recent_avg_ll, older_avg_ll)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (today, level, label, active, trigger_reason,
          mean_log_loss, threshold, recent_avg_ll, older_avg_ll))
    conn.commit()
    conn.close()


def check_circuit_breaker_auto(days: int = 90, ll_threshold: float = 0.05) -> dict:
    """Auto-check calibration trend and activate circuit breaker if degradation detected.

    Returns:
        dict with level, label, action_overrides applied
    """
    try:
        from calibration.calibrator import calibration_trend_report
        report = calibration_trend_report(days=days)
    except Exception:
        return {"level": CB_LEVEL_NONE, "label": CB_LABELS[CB_LEVEL_NONE],
                "active": 0, "error": "calibration_trend_report failed"}

    if report.get("status") != "OK":
        return {"level": CB_LEVEL_NONE, "label": CB_LABELS[CB_LEVEL_NONE],
                "active": 0, "reason": "NO_DATA"}

    trend = report.get("trend", {})
    if not trend:
        return {"level": CB_LEVEL_NONE, "label": CB_LABELS[CB_LEVEL_NONE],
                "active": 0, "reason": "NO_TREND"}

    recent_ll = trend.get("recent_avg_log_loss", 0.0)
    older_ll = trend.get("older_avg_log_loss", 0.0)
    degradation = trend.get("degradation_detected", False)
    diff = recent_ll - older_ll

    latest = report.get("latest", {})
    mean_ll = latest.get("mean_log_loss", 0.0) or 0.0

    # Determine level
    if mean_ll > 0.70:
        level = CB_LEVEL_EMERGENCY
        reason = f"Log-Loss quá cao: {mean_ll:.3f} > 0.70"
    elif degradation and diff > 0.10:
        level = CB_LEVEL_ACTIVE
        reason = f"Degradation mạnh: ΔLL = {diff:.3f} > 0.10"
    elif degradation and diff > ll_threshold:
        level = CB_LEVEL_CAUTION
        reason = f"Degradation nhẹ: ΔLL = {diff:.3f} > {ll_threshold}"
    else:
        level = CB_LEVEL_NONE
        reason = "Bình thường"

    # Persist
    set_circuit_breaker(
        level=level,
        trigger_reason=reason,
        mean_log_loss=mean_ll,
        threshold=ll_threshold,
        recent_avg_ll=recent_ll,
        older_avg_ll=older_ll,
    )

    return {
        "level": level,
        "label": CB_LABELS.get(level, "UNKNOWN"),
        "active": 1 if level > 0 else 0,
        "reason": reason,
        "recent_avg_ll": recent_ll,
        "older_avg_ll": older_ll,
        "diff": round(diff, 4),
        "mean_log_loss": mean_ll,
    }


def init_macro_sensory_log():
    """Initialize macro_sensory_log table for WorldSensor (P0.5) snapshots.
    
    WHY separate table from macro_history (screener_cache.db)?
    macro_history stores individual variable rows (1 row per variable per date),
    optimized for time-series queries. macro_sensory_log stores the full 10-field
    WorldSensor snapshot as a single JSON row per date, optimized for audit trail
    and Governor consumption. Both are written in sync during daily-update Step 1a.
    """
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_sensory_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL UNIQUE,
            fed_target_rate REAL DEFAULT 0.0,
            fomc_dissent    INTEGER DEFAULT 0,
            qt_balance_tr   REAL DEFAULT 0.0,
            reserves_tr     REAL DEFAULT 0.0,
            us10y_yield     REAL DEFAULT 0.0,
            usd_index       REAL DEFAULT 0.0,
            brent_oil       REAL DEFAULT 0.0,
            implied_hike_prob REAL DEFAULT 0.0,
            next_meeting    TEXT DEFAULT '',
            fed_uncertainty REAL DEFAULT 0.0,
            source          TEXT DEFAULT 'world_sensor',
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def get_circuit_breaker_log(days: int = 90) -> List[Dict]:
    """Return circuit breaker history."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM circuit_breaker
        WHERE date >= ?
        ORDER BY id DESC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
