"""Shadow CAO — Separate SQLite storage (never touches production tables)."""
import json
import logging
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

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

SHADOW_DB_PATH = str(src.config.DATA_DIR / "shadow_cao.db")


SCHEMA_DECISION_LOGS = """
CREATE TABLE IF NOT EXISTS shadow_decision_logs (
    decision_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    posture TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    confidence REAL NOT NULL,
    engine_scores TEXT NOT NULL,
    decision_weights TEXT NOT NULL DEFAULT '{}',
    market_regime TEXT,
    regime_score REAL DEFAULT 0.0,
    vnindex_level REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_ABLATION_RESULTS = """
CREATE TABLE IF NOT EXISTS shadow_ablations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    engine_removed TEXT NOT NULL,
    baseline_action TEXT NOT NULL,
    baseline_confidence REAL NOT NULL,
    ablated_action TEXT NOT NULL,
    ablated_confidence REAL NOT NULL,
    action_changed INTEGER NOT NULL DEFAULT 0,
    confidence_delta REAL NOT NULL DEFAULT 0.0,
    decision_flip INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, engine_removed)
);
"""

SCHEMA_OUTCOME_LOGS = """
CREATE TABLE IF NOT EXISTS shadow_outcome_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    actual_outcome REAL NOT NULL,
    actual_success INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
);
"""

SCHEMA_ATTRIBUTION_PERTURBATIONS = """
CREATE TABLE IF NOT EXISTS shadow_attribution_perturbations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    engine TEXT NOT NULL,
    signal_at_decision REAL NOT NULL DEFAULT 0.0,
    baseline_contribution REAL NOT NULL DEFAULT 0.0,
    ablated_contribution REAL NOT NULL DEFAULT 0.0,
    contribution_delta REAL NOT NULL DEFAULT 0.0,
    baseline_tp REAL NOT NULL DEFAULT 0.0,
    ablated_tp REAL NOT NULL DEFAULT 0.0,
    baseline_fp REAL NOT NULL DEFAULT 0.0,
    ablated_fp REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days, engine)
);
"""

SCHEMA_ENGINE_PROFILES = """
CREATE TABLE IF NOT EXISTS shadow_engine_profiles (
    engine TEXT PRIMARY KEY,
    total_decisions INTEGER NOT NULL DEFAULT 0,
    flip_count INTEGER NOT NULL DEFAULT 0,
    avg_confidence_delta REAL NOT NULL DEFAULT 0.0,
    action_change_ratio REAL NOT NULL DEFAULT 0.0,
    stability_score REAL NOT NULL DEFAULT 0.0,
    last_updated TEXT NOT NULL
);
"""

SCHEMA_BELIEF_STATE = """
CREATE TABLE IF NOT EXISTS shadow_belief_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def get_shadow_connection():
    conn = sqlite3.connect(SHADOW_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_shadow_database():
    Path(SHADOW_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_shadow_connection() as conn:
        conn.execute(SCHEMA_DECISION_LOGS)
        conn.execute(SCHEMA_ABLATION_RESULTS)
        conn.execute(SCHEMA_OUTCOME_LOGS)
        conn.execute(SCHEMA_ATTRIBUTION_PERTURBATIONS)
        conn.execute(SCHEMA_ENGINE_PROFILES)
        conn.execute(SCHEMA_BELIEF_STATE)
    logger.info("[SHADOW_CAO] Database initialized: %s", SHADOW_DB_PATH)


def save_decision_log(entry: "ShadowDecisionLog") -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_decision_logs
                (decision_id, timestamp, posture, risk_level, confidence,
                 engine_scores, decision_weights, market_regime,
                 regime_score, vnindex_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.decision_id,
                entry.timestamp,
                entry.posture,
                entry.risk_level,
                entry.confidence,
                json.dumps(entry.engine_scores, ensure_ascii=False),
                json.dumps(entry.decision_weights, ensure_ascii=False),
                entry.market_regime,
                entry.regime_score,
                entry.vnindex_level,
            ))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save decision log: %s", e)
        return False


def save_ablation_result(result: "AblationResult", decision_id: str) -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_ablations
                (decision_id, engine_removed, baseline_action,
                 baseline_confidence, ablated_action, ablated_confidence,
                 action_changed, confidence_delta, decision_flip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_id,
                result.engine_removed,
                result.baseline_action,
                result.baseline_confidence,
                result.ablated_action,
                result.ablated_confidence,
                1 if result.action_changed else 0,
                result.confidence_delta,
                1 if result.decision_flip else 0,
            ))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save ablation: %s", e)
        return False


def save_outcome_log(decision_id: str, horizon_days: int,
                     actual_outcome: float, success: bool) -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_outcome_logs
                (decision_id, horizon_days, actual_outcome, actual_success)
                VALUES (?, ?, ?, ?)
            """, (decision_id, horizon_days, actual_outcome, 1 if success else 0))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save outcome log: %s", e)
        return False


def save_attribution_perturbation(
    decision_id: str, horizon_days: int, engine: str,
    signal: float, baseline_c: float, ablated_c: float,
    delta_c: float, baseline_tp: float, ablated_tp: float,
    baseline_fp: float, ablated_fp: float,
) -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_attribution_perturbations
                (decision_id, horizon_days, engine, signal_at_decision,
                 baseline_contribution, ablated_contribution, contribution_delta,
                 baseline_tp, ablated_tp, baseline_fp, ablated_fp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_id, horizon_days, engine, signal,
                baseline_c, ablated_c, delta_c,
                baseline_tp, ablated_tp, baseline_fp, ablated_fp,
            ))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save perturbation: %s", e)
        return False


def save_engine_profile(profile: "EngineAblationProfile") -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_engine_profiles
                (engine, total_decisions, flip_count, avg_confidence_delta,
                 action_change_ratio, stability_score, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                profile.engine,
                profile.total_decisions,
                profile.flip_count,
                profile.avg_confidence_delta,
                profile.action_change_ratio,
                profile.stability_score,
                datetime.now().isoformat(),
            ))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save engine profile: %s", e)
        return False


def save_belief_value(key: str, value: str) -> bool:
    try:
        with get_shadow_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO shadow_belief_state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
        return True
    except Exception as e:
        logger.error("[SHADOW_CAO] Failed to save belief state: %s", e)
        return False


def get_decision_log(decision_id: str) -> dict:
    with get_shadow_connection() as conn:
        row = conn.execute(
            "SELECT * FROM shadow_decision_logs WHERE decision_id = ?",
            (decision_id,)
        ).fetchone()
    return dict(row) if row else None


def get_ablations_for_decision(decision_id: str) -> list[dict]:
    with get_shadow_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_ablations WHERE decision_id = ? ORDER BY confidence_delta DESC",
            (decision_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_decision_logs(limit: int = 200) -> list[dict]:
    with get_shadow_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_decision_logs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_engine_profiles() -> list[dict]:
    with get_shadow_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_engine_profiles ORDER BY engine"
        ).fetchall()
    return [dict(r) for r in rows]


def get_belief_value(key: str) -> str:
    with get_shadow_connection() as conn:
        row = conn.execute(
            "SELECT value FROM shadow_belief_state WHERE key = ?",
            (key,)
        ).fetchone()
    return row["value"] if row else None


def get_shadow_stats() -> dict:
    try:
        initialize_shadow_database()
    except Exception:
        pass
    with get_shadow_connection() as conn:
        logs = conn.execute("SELECT COUNT(*) as c FROM shadow_decision_logs").fetchone()["c"]
        ablations = conn.execute("SELECT COUNT(*) as c FROM shadow_ablations").fetchone()["c"]
        outcomes = conn.execute("SELECT COUNT(*) as c FROM shadow_outcome_logs").fetchone()["c"]
        perturbations = conn.execute("SELECT COUNT(*) as c FROM shadow_attribution_perturbations").fetchone()["c"]
    return {
        "decision_logs": logs,
        "ablations": ablations,
        "outcomes": outcomes,
        "perturbations": perturbations,
    }
