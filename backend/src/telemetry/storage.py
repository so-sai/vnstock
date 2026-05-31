"""Telemetry Storage — SQLite persistence for snapshots & outcomes"""
import sys
import os
import json
import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config

TELEMETRY_DB_PATH = str(src.config.DATA_DIR / "telemetry.db")

SCHEMA_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS decision_snapshots (
    decision_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    posture TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    confidence REAL NOT NULL,
    dominant_signal TEXT NOT NULL,
    vnindex_level REAL NOT NULL,
    opportunity_symbols TEXT NOT NULL DEFAULT '[]',
    holdings_health TEXT,
    market_regime TEXT,
    decision_weights TEXT,
    engine_scores TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_OUTCOMES = """
CREATE TABLE IF NOT EXISTS outcome_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    evaluated_date TEXT NOT NULL,
    vnindex_entry REAL NOT NULL,
    vnindex_exit REAL NOT NULL,
    vnindex_return REAL NOT NULL,
    benchmark_return REAL NOT NULL,
    success INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
);
"""

SCHEMA_ATTRIBUTIONS = """
CREATE TABLE IF NOT EXISTS attribution_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    contribution REAL NOT NULL,
    direction TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

IDX_OUTCOME_DECISION = """
CREATE INDEX IF NOT EXISTS idx_outcome_decision
ON outcome_records (decision_id)
"""

IDX_OUTCOME_HORIZON = """
CREATE INDEX IF NOT EXISTS idx_outcome_horizon
ON outcome_records (horizon_days)
"""

IDX_SNAPSHOT_TIMESTAMP = """
CREATE INDEX IF NOT EXISTS idx_snapshot_timestamp
ON decision_snapshots (timestamp)
"""

SCHEMA_ENGINE_ATTRIBUTION = """
CREATE TABLE IF NOT EXISTS engine_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    engine TEXT NOT NULL,
    signal_at_decision REAL NOT NULL,
    contribution REAL NOT NULL,
    true_positive_contribution REAL NOT NULL DEFAULT 0.0,
    false_positive_contribution REAL NOT NULL DEFAULT 0.0,
    direction TEXT NOT NULL,
    correlation REAL NOT NULL,
    accuracy REAL NOT NULL,
    precision REAL NOT NULL DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days, engine)
)
"""

SCHEMA_ENGINE_PERFORMANCE = """
CREATE TABLE IF NOT EXISTS engine_performance (
    engine TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    precision REAL NOT NULL DEFAULT 0.5,
    avg_contribution REAL NOT NULL,
    stability REAL NOT NULL,
    decisions_count INTEGER NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (engine, window_days)
)
"""

SCHEMA_MARKET_OUTCOME = """
CREATE TABLE IF NOT EXISTS market_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    asset_return REAL NOT NULL,
    benchmark_return REAL NOT NULL,
    alpha_return REAL NOT NULL,
    realized_volatility REAL NOT NULL DEFAULT 0.0,
    regime_shift INTEGER NOT NULL DEFAULT 0,
    sector_rotation TEXT NOT NULL DEFAULT 'NEUTRAL',
    liquidity_phase TEXT NOT NULL DEFAULT 'NEUTRAL',
    gold_change_pct REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
)
"""

IDX_ATTRIBUTION_DECISION = """
CREATE INDEX IF NOT EXISTS idx_attribution_decision
ON engine_attribution (decision_id, horizon_days)
"""

# Canonical schema migration — add columns to existing tables
ENGINE_ATTRIBUTION_COLUMNS_V2 = [
    ("true_positive_contribution", "REAL NOT NULL DEFAULT 0.0"),
    ("false_positive_contribution", "REAL NOT NULL DEFAULT 0.0"),
    ("precision", "REAL NOT NULL DEFAULT 0.5"),
]

ENGINE_PERFORMANCE_COLUMNS_V2 = [
    ("precision", "REAL NOT NULL DEFAULT 0.5"),
]


@contextmanager
def get_telemetry_connection():
    conn = sqlite3.connect(TELEMETRY_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-20000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA journal_size_limit=67108864")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_telemetry_database():
    os.makedirs(str(Path(TELEMETRY_DB_PATH).parent), exist_ok=True)
    with get_telemetry_connection() as conn:
        conn.execute(SCHEMA_SNAPSHOTS)
        conn.execute(SCHEMA_OUTCOMES)
        conn.execute(SCHEMA_ATTRIBUTIONS)
        conn.execute(SCHEMA_ENGINE_ATTRIBUTION)
        conn.execute(SCHEMA_ENGINE_PERFORMANCE)
        conn.execute(SCHEMA_MARKET_OUTCOME)
        conn.execute(IDX_OUTCOME_DECISION)
        conn.execute(IDX_OUTCOME_HORIZON)
        conn.execute(IDX_SNAPSHOT_TIMESTAMP)
        conn.execute(IDX_ATTRIBUTION_DECISION)
        _migrate_telemetry_schema(conn)
    logger.info("[TELEMETRY] Database initialized: %s", TELEMETRY_DB_PATH)


def _migrate_telemetry_schema(conn):
    existing = {d[1] for d in conn.execute("PRAGMA table_info(engine_attribution)").fetchall()}
    for col_name, col_type in ENGINE_ATTRIBUTION_COLUMNS_V2:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE engine_attribution ADD COLUMN {col_name} {col_type}")
            logger.info("[TELEMETRY] Migration: added %s to engine_attribution", col_name)
    existing_perf = {d[1] for d in conn.execute("PRAGMA table_info(engine_performance)").fetchall()}
    for col_name, col_type in ENGINE_PERFORMANCE_COLUMNS_V2:
        if col_name not in existing_perf:
            conn.execute(f"ALTER TABLE engine_performance ADD COLUMN {col_name} {col_type}")
            logger.info("[TELEMETRY] Migration: added %s to engine_performance", col_name)


def save_snapshot(snapshot) -> bool:
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO decision_snapshots
                (decision_id, timestamp, posture, risk_level, confidence,
                 dominant_signal, vnindex_level, opportunity_symbols,
                 holdings_health, market_regime, decision_weights,
                 engine_scores)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.decision_id,
                snapshot.timestamp.isoformat() if hasattr(snapshot.timestamp, 'isoformat') else str(snapshot.timestamp),
                snapshot.posture,
                snapshot.risk_level,
                snapshot.confidence,
                snapshot.dominant_signal,
                snapshot.vnindex_level,
                json.dumps(snapshot.opportunity_symbols, ensure_ascii=False),
                snapshot.holdings_health,
                snapshot.market_regime,
                snapshot.decision_weights,
                snapshot.engine_scores,
            ))
        return True
    except Exception as e:
        logger.error("[TELEMETRY] Failed to save snapshot: %s", e)
        return False


def save_outcome(record) -> bool:
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO outcome_records
                (decision_id, horizon_days, evaluated_date,
                 vnindex_entry, vnindex_exit, vnindex_return,
                 benchmark_return, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.decision_id,
                record.horizon_days,
                datetime.now().strftime("%Y-%m-%d"),
                record.vnindex_entry,
                record.vnindex_exit,
                record.vnindex_return,
                record.benchmark_return,
                1 if record.success else 0,
            ))
        return True
    except Exception as e:
        logger.error("[TELEMETRY] Failed to save outcome: %s", e)
        return False


def get_snapshot(decision_id: str):
    with get_telemetry_connection() as conn:
        row = conn.execute(
            "SELECT * FROM decision_snapshots WHERE decision_id = ?",
            (decision_id,)
        ).fetchone()
    return dict(row) if row else None


def get_pending_decisions(min_age_days: int) -> list:
    with get_telemetry_connection() as conn:
        rows = conn.execute("""
            SELECT s.* FROM decision_snapshots s
            WHERE NOT EXISTS (
                SELECT 1 FROM outcome_records o
                WHERE o.decision_id = s.decision_id
                AND o.horizon_days = ?
            )
            AND date(s.timestamp) <= date('now', ?)
        """, (min_age_days, f'-{min_age_days} days')).fetchall()
    return [dict(r) for r in rows]


def get_outcomes(decision_id: str = None) -> list:
    with get_telemetry_connection() as conn:
        if decision_id:
            rows = conn.execute(
                "SELECT * FROM outcome_records WHERE decision_id = ? ORDER BY horizon_days",
                (decision_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM outcome_records ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
    return [dict(r) for r in rows]


def get_all_snapshots(limit: int = 50) -> list:
    with get_telemetry_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM decision_snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_engine_attribution(attribution) -> bool:
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO engine_attribution
                (decision_id, horizon_days, engine, signal_at_decision,
                 contribution, true_positive_contribution,
                 false_positive_contribution, direction,
                 correlation, accuracy, precision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attribution.decision_id,
                attribution.horizon_days,
                attribution.engine,
                attribution.signal_at_decision,
                attribution.contribution,
                attribution.true_positive_contribution,
                attribution.false_positive_contribution,
                attribution.direction,
                attribution.correlation,
                attribution.accuracy,
                attribution.precision,
            ))
        return True
    except Exception as e:
        logger.error("[TELEMETRY] Failed to save attribution: %s", e)
        return False


def save_market_outcome(market_outcome) -> bool:
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO market_outcomes
                (decision_id, horizon_days, asset_return, benchmark_return,
                 alpha_return, realized_volatility, regime_shift,
                 sector_rotation, liquidity_phase, gold_change_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_outcome.decision_id,
                market_outcome.horizon_days,
                market_outcome.asset_return,
                market_outcome.benchmark_return,
                market_outcome.alpha_return,
                market_outcome.realized_volatility,
                1 if market_outcome.regime_shift else 0,
                market_outcome.sector_rotation,
                market_outcome.liquidity_phase,
                market_outcome.gold_change_pct,
            ))
        return True
    except Exception as e:
        logger.error("[TELEMETRY] Failed to save market outcome: %s", e)
        return False


def get_attributions(decision_id: str = None, limit: int = 100) -> list:
    with get_telemetry_connection() as conn:
        if decision_id:
            rows = conn.execute(
                "SELECT * FROM engine_attribution WHERE decision_id = ? ORDER BY contribution DESC",
                (decision_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM engine_attribution ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def save_engine_performance(perf) -> bool:
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO engine_performance
                (engine, window_days, accuracy, precision, avg_contribution,
                 stability, decisions_count, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                perf.engine,
                perf.window_days,
                perf.accuracy,
                perf.precision,
                perf.avg_contribution,
                perf.stability,
                perf.decisions_count,
                perf.last_updated,
            ))
        return True
    except Exception as e:
        logger.error("[TELEMETRY] Failed to save engine performance: %s", e)
        return False


def get_engine_performance(engine: str = None) -> list:
    with get_telemetry_connection() as conn:
        if engine:
            rows = conn.execute(
                "SELECT * FROM engine_performance WHERE engine = ? ORDER BY window_days",
                (engine,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM engine_performance ORDER BY engine, window_days"
            ).fetchall()
    return [dict(r) for r in rows]


def get_attribution_summary(decision_id: str, horizon_days: int) -> dict:
    with get_telemetry_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM engine_attribution
            WHERE decision_id = ? AND horizon_days = ?
            ORDER BY contribution DESC
        """, (decision_id, horizon_days)).fetchall()
        outcome = conn.execute("""
            SELECT vnindex_return, benchmark_return FROM outcome_records
            WHERE decision_id = ? AND horizon_days = ?
        """, (decision_id, horizon_days)).fetchone()
    if not rows:
        return None
    total_return = outcome["vnindex_return"] if outcome else 0
    benchmark = outcome["benchmark_return"] if outcome else 0
    alpha = total_return - benchmark
    rows_dicts = [dict(r) for r in rows]
    contribs = {rd["engine"]: rd["contribution"] for rd in rows_dicts}
    dominant = max(rows_dicts, key=lambda rd: abs(rd["contribution"]))
    return {
        "decision_id": decision_id,
        "horizon_days": horizon_days,
        "outcome_label": "DUNG" if total_return > 0 else "SAI",
        "primary_reason_vi": f"Engine {dominant['engine']} đóng góp {dominant['contribution']:.0%} vào kết quả",
        "secondary_reasons_vi": [
            f"{rd['engine']}: {rd['true_positive_contribution']:.2f} TP / {rd['false_positive_contribution']:.2f} FP"
            for rd in rows_dicts[:3]
        ],
        "engine_scorecard": contribs,
        "dominant_engine": dominant["engine"],
        "total_return": total_return,
        "alpha_return": round(alpha, 4),
        "confidence_recalibration": round(dominant.get("accuracy", 0.5) * 0.1, 4),
    }


def get_snapshot_stats() -> dict:
    with get_telemetry_connection() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM decision_snapshots").fetchone()["c"]
        evaluated = conn.execute("SELECT COUNT(DISTINCT decision_id) as c FROM outcome_records").fetchone()["c"]
        success_count = conn.execute(
            "SELECT COUNT(*) as c FROM outcome_records WHERE success = 1"
        ).fetchone()["c"]
        total_outcomes = conn.execute("SELECT COUNT(*) as c FROM outcome_records").fetchone()["c"]
    return {
        "total_decisions": total,
        "evaluated_decisions": evaluated,
        "total_outcomes": total_outcomes,
        "successful_outcomes": success_count,
        "success_rate": round(success_count / total_outcomes * 100, 1) if total_outcomes > 0 else 0,
    }
