import sys
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
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import logging
import sqlite3

import pandas as pd

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)


def _derive_events(df: pd.DataFrame) -> list:
    """Derive decision events from regime_history data."""
    events = []
    if df.empty or "regime_status" not in df.columns:
        return events

    prev_status = None
    prev_recovery = 0
    blocked_days = 0

    for _, row in df.iterrows():
        date = row.get("date", "")
        if pd.isna(date):
            continue

        status = row.get("regime_status")
        score = row.get("regime_score")
        recovery = row.get("recovery_flag", 0)
        atr_ratio = row.get("atr_ratio") or 0
        breadth = row.get("breadth_pct") or 50
        velocity = row.get("breadth_velocity") or 0

        # Regime flip detection
        if prev_status and status and status != prev_status:
            events.append(
                {
                    "date": str(date),
                    "event_type": "REGIME_FLIP",
                    "model": "C",
                    "reason": f"{prev_status} → {status}",
                    "context": status or "",
                    "confidence": float(score) if score else 0,
                    "meta": {"from": prev_status, "to": status},
                }
            )

        # Recovery event
        if recovery and not prev_recovery:
            events.append(
                {
                    "date": str(date),
                    "event_type": "RECOVERY_FIRE",
                    "model": "RECOVERY",
                    "reason": "Recovery detector activated",
                    "context": status or "",
                    "confidence": float(score) if score else 0,
                    "meta": {"breadth_pct": float(breadth) if breadth else 0},
                }
            )

        # Blocked state tracking (RANGING or CRISIS active_model = NONE or B)
        if status in ("RANGING", "CRISIS"):
            blocked_days += 1
            reason_parts = []
            if atr_ratio and atr_ratio > 1.2:
                reason_parts.append("ATR_SHOCK")
            if velocity is not None and velocity < -10:
                reason_parts.append("BREADTH_COLLAPSE")
            if breadth is not None and breadth < 30:
                reason_parts.append("LOW_PARTICIPATION")
            if score is not None and score < 0.35:
                reason_parts.append("REGIME_CRISIS")

            # Only emit BLOCKED event if there's at least one reason
            if reason_parts:
                events.append(
                    {
                        "date": str(date),
                        "event_type": "BLOCKED",
                        "model": "B",
                        "reason": "|".join(reason_parts),
                        "context": status or "",
                        "confidence": float(score) if score else 0,
                        "meta": {
                            "atr_ratio": float(atr_ratio) if atr_ratio else 0,
                            "breadth_pct": float(breadth) if breadth else 0,
                            "velocity": float(velocity) if velocity else 0,
                            "blocked_days": blocked_days,
                        },
                    }
                )
        else:
            blocked_days = 0

        # PICK events when active_model = B and not blocked
        active_model = row.get("active_model")
        if active_model == "B" and status == "TRENDING" and score and score >= 0.7:
            events.append(
                {
                    "date": str(date),
                    "event_type": "PICK",
                    "model": "B",
                    "reason": "Pullback recovery setup",
                    "context": "PRIMARY_MA200",
                    "confidence": float(score),
                    "meta": {
                        "breadth_pct": float(breadth) if breadth else 0,
                        "velocity": float(velocity) if velocity else 0,
                    },
                }
            )

        # Active model A picks
        if active_model == "A" and status == "TRENDING" and score and score >= 0.8:
            events.append(
                {
                    "date": str(date),
                    "event_type": "PICK",
                    "model": "A",
                    "reason": "Momentum continuation",
                    "context": "STRONG_TREND",
                    "confidence": float(score),
                    "meta": {},
                }
            )

        prev_status = status
        prev_recovery = recovery

    return events


def get_replay_timeline(limit: int = 365) -> list:
    """
    Serves combined daily timeline data + derived events for the Replay Timeline Viewer.
    """
    query = f"""
        SELECT
            d.date,
            d.open, d.high, d.low, d.close, d.volume,
            AVG(d.close) OVER (ORDER BY d.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
            AVG(d.close) OVER (ORDER BY d.date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as ma200,
            r.regime_score,
            r.status as regime_status,
            r.breadth_pct,
            r.breadth_velocity,
            r.trend_score,
            r.vol_score,
            r.atr_ratio,
            r.active_model,
            r.recovery_flag
        FROM daily_ohlcv d
        LEFT JOIN regime_history r ON d.date = r.date
        WHERE d.symbol = 'VNINDEX'
        ORDER BY d.date DESC
        LIMIT {limit}
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(query, conn)
        if df.empty:
            return {"days": [], "events": []}

        df = df.sort_values("date").reset_index(drop=True)
        dates = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
        df = df.assign(date=dates)
        df = df.where(pd.notna(df), None)

        events = _derive_events(df)
        records = df.to_dict(orient="records")

        return {
            "days": records,
            "events": events,
        }
    except (sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
        logger.error(f"Replay timeline failed: {e}")
        return {"days": [], "events": []}
