"""
Portfolio State Memory Layer v1.0 — Runtime
Closed-loop experience memory for adaptive capital control.
Turns portfolio history into a control signal.
"""

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.portfolio_db import PORTFOLIO_DB_PATH

logger = logging.getLogger(__name__)

_initialized = False

SCHEMA_MODEL_REGIME = """
CREATE TABLE IF NOT EXISTS model_regime_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    regime TEXT NOT NULL,
    trades INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    total_r REAL NOT NULL DEFAULT 0.0,
    avg_r REAL NOT NULL DEFAULT 0.0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    expectancy REAL NOT NULL DEFAULT 0.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model, regime)
);
"""

SCHEMA_DECAY_LOG = """
CREATE TABLE IF NOT EXISTS conviction_decay_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    regime TEXT NOT NULL,
    streak_losses INTEGER NOT NULL DEFAULT 0,
    streak_wins INTEGER NOT NULL DEFAULT 0,
    decay_factor REAL NOT NULL DEFAULT 1.0,
    boost_factor REAL NOT NULL DEFAULT 1.0,
    reason TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_RISK_PATH = """
CREATE TABLE IF NOT EXISTS portfolio_risk_path (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_exposure REAL NOT NULL DEFAULT 0.0,
    portfolio_heat REAL NOT NULL DEFAULT 0.0,
    throttle_factor REAL NOT NULL DEFAULT 1.0,
    dampener_avg REAL NOT NULL DEFAULT 1.0,
    drawdown_pct REAL NOT NULL DEFAULT 0.0,
    rolling_vol REAL NOT NULL DEFAULT 0.0
);
"""

SCHEMA_STATE_MEMORY = """
CREATE TABLE IF NOT EXISTS portfolio_state_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    model_source TEXT NOT NULL,
    regime TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    entry_score REAL NOT NULL DEFAULT 0.0,
    exit_score REAL,
    pnl_pct REAL,
    r_multiple REAL,
    holding_period_days INTEGER,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    exit_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _raw_conn():
    conn = sqlite3.connect(PORTFOLIO_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def initialize_memory_tables():
    conn = _raw_conn()
    try:
        conn.execute(SCHEMA_MODEL_REGIME)
        conn.execute(SCHEMA_DECAY_LOG)
        conn.execute(SCHEMA_RISK_PATH)
        conn.execute(SCHEMA_STATE_MEMORY)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_memory_tables():
    conn = _raw_conn()
    try:
        existing = {d[1] for d in conn.execute("PRAGMA table_info(portfolio_state_memory)").fetchall()}
        additions = []
        if "exit_reason" not in existing:
            additions.append(("exit_reason", "TEXT"))
        for col_name, col_type in additions:
            conn.execute(f"ALTER TABLE portfolio_state_memory ADD COLUMN {col_name} {col_type}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_connection():
    global _initialized
    if not _initialized:
        _initialized = True
        initialize_memory_tables()
        migrate_memory_tables()
    return _raw_conn()


def record_trade_memory(
    symbol: str,
    model_source: str,
    regime: str,
    entry_time: str,
    exit_time: str,
    entry_score: float,
    exit_score: float | None = None,
    pnl_pct: float | None = None,
    r_multiple: float | None = None,
    holding_period_days: int | None = None,
    max_favorable_excursion: float | None = None,
    max_adverse_excursion: float | None = None,
    exit_reason: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO portfolio_state_memory
                (symbol, model_source, regime, entry_time, exit_time,
                 entry_score, exit_score, pnl_pct, r_multiple,
                 holding_period_days, max_favorable_excursion,
                 max_adverse_excursion, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                model_source,
                regime,
                entry_time,
                exit_time,
                entry_score,
                exit_score,
                pnl_pct,
                r_multiple,
                holding_period_days,
                max_favorable_excursion,
                max_adverse_excursion,
                exit_reason,
            ),
        )
        conn.commit()
        return {"ok": True, "symbol": symbol}
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def update_model_regime_stats(model: str, regime: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN COALESCE(pnl_pct,0) > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN COALESCE(pnl_pct,0) <= 0 THEN 1 ELSE 0 END),
                   COALESCE(SUM(r_multiple), 0)
            FROM portfolio_state_memory
            WHERE model_source = ? AND regime = ?
        """,
            (model, regime),
        ).fetchone()

        trades, wins, losses, total_r = row
        if trades is None or trades == 0:
            return {"ok": True, "trades": 0}

        win_rate = round(wins / trades, 4) if trades > 0 else 0.0
        avg_r = round(total_r / trades, 4) if trades > 0 else 0.0
        expectancy = round((win_rate * avg_r) - ((1 - win_rate) * 1.0), 4)

        conn.execute(
            """
            INSERT INTO model_regime_performance
            (model, regime, trades, wins, losses, total_r, avg_r, win_rate, expectancy, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(model, regime) DO UPDATE SET
                trades = excluded.trades,
                wins = excluded.wins,
                losses = excluded.losses,
                total_r = excluded.total_r,
                avg_r = excluded.avg_r,
                win_rate = excluded.win_rate,
                expectancy = excluded.expectancy,
                last_updated = CURRENT_TIMESTAMP
        """,
            (model, regime, trades, wins, losses, total_r, avg_r, win_rate, expectancy),
        )
        conn.commit()

        return {
            "ok": True,
            "model": model,
            "regime": regime,
            "trades": trades,
            "win_rate": win_rate,
            "avg_r": avg_r,
            "expectancy": expectancy,
        }
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def log_conviction_decay(
    model: str, regime: str, streak_losses: int, streak_wins: int, decay_factor: float, boost_factor: float, reason: str
):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO conviction_decay_log
                (model, regime, streak_losses, streak_wins, decay_factor, boost_factor, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (model, regime, streak_losses, streak_wins, decay_factor, boost_factor, reason),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def snapshot_risk_path(
    total_exposure: float,
    portfolio_heat: float,
    throttle_factor: float,
    dampener_avg: float,
    drawdown_pct: float,
    rolling_vol: float,
):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO portfolio_risk_path
                (total_exposure, portfolio_heat, throttle_factor, dampener_avg, drawdown_pct, rolling_vol)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (total_exposure, portfolio_heat, throttle_factor, dampener_avg, drawdown_pct, rolling_vol),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_model_regime_performance(model: str, regime: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT trades, wins, losses, win_rate, avg_r, expectancy
            FROM model_regime_performance
            WHERE model = ? AND regime = ?
        """,
            (model, regime),
        ).fetchone()
        if not row:
            return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0}
        return {
            "trades": row[0],
            "wins": row[1],
            "losses": row[2],
            "win_rate": row[3],
            "avg_r": row[4],
            "expectancy": row[5],
        }
    finally:
        conn.close()


def get_recent_model_streak(model: str, limit: int = 5) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pnl_pct FROM portfolio_state_memory
            WHERE model_source = ?
            ORDER BY created_at DESC LIMIT ?
        """,
            (model, limit),
        ).fetchall()
        if len(rows) < 3:
            return {"streak_losses": 0, "streak_wins": 0, "win_rate_10d": None}
        losses = sum(1 for r in rows if r[0] is not None and r[0] < 0)
        wins = len(rows) - losses
        streak = 0
        for r in rows:
            if r[0] is not None and r[0] < 0:
                streak -= 1
            elif r[0] is not None and r[0] > 0:
                streak = 0
            else:
                break
        return {
            "streak_losses": abs(streak) if streak < 0 else 0,
            "streak_wins": streak if streak > 0 else 0,
            "win_rate_10d": round(wins / len(rows), 4) if rows else None,
        }
    finally:
        conn.close()


def compute_regime_decay_factor(model: str, regime: str) -> float:
    perf = get_model_regime_performance(model, regime)
    if perf["trades"] < 3:
        return 1.0

    factor = 1.0

    if regime in ("RANGING", "CRISIS") and model == "MODEL_A":
        factor *= 0.8
    if regime == "RECOVERY" and model == "MODEL_B":
        factor *= 1.2
    if perf["win_rate"] < 0.35 and perf["trades"] >= 5:
        factor *= 0.7
    if perf["expectancy"] < -0.3 and perf["trades"] >= 5:
        factor *= 0.5
    if perf["win_rate"] > 0.65 and perf["avg_r"] > 1.5:
        factor *= 1.15
    if perf["expectancy"] > 0.5:
        factor *= 1.1

    return round(min(max(factor, 0.2), 2.0), 2)


def get_effective_dampener(thesis_source: str, current_regime: str) -> dict:
    streak = get_recent_model_streak(thesis_source)
    regime_bias = compute_regime_decay_factor(thesis_source, current_regime)

    decay = 1.0
    boost = 1.0
    reason = "NORMAL"

    if streak["streak_losses"] >= 3:
        decay = 0.5
        reason = f"LOSS_STREAK_{streak['streak_losses']}"
    elif streak.get("win_rate_10d") and streak["win_rate_10d"] > 0.65:
        boost = 1.1
        reason = f"HIGH_WINRATE_{streak['win_rate_10d']:.2f}"

    net = round(decay * boost * regime_bias, 4)

    if decay < 1.0 or boost > 1.0 or regime_bias != 1.0:
        log_conviction_decay(
            thesis_source, current_regime, streak["streak_losses"], streak["streak_wins"], decay, boost, reason
        )

    return {
        "net_dampener": net,
        "decay_factor": decay,
        "boost_factor": boost,
        "regime_bias": regime_bias,
        "reason": reason,
        "streak_losses": streak["streak_losses"],
        "win_rate_10d": streak["win_rate_10d"],
    }


def get_risk_path_window(days: int = 30) -> list:
    conn = get_connection()
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT timestamp, total_exposure, portfolio_heat, throttle_factor,
                   dampener_avg, drawdown_pct, rolling_vol
            FROM portfolio_risk_path
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
        """,
            (since,),
        ).fetchall()
        return [
            {
                "timestamp": r[0],
                "total_exposure": r[1],
                "portfolio_heat": r[2],
                "throttle_factor": r[3],
                "dampener_avg": r[4],
                "drawdown_pct": r[5],
                "rolling_vol": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


if __name__ == "__main__":
    initialize_memory_tables()
    migrate_memory_tables()
    print("Memory engine tables initialized.")
