"""Telemetry Evaluator — judges decisions after 5, 10, 20, 30 days"""

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection
from src.telemetry.attribution import decompose_attribution
from src.telemetry.models import OutcomeRecord
from src.telemetry.storage import get_pending_decisions, get_snapshot, save_outcome

HORIZONS = [5, 10, 20, 30]


def _get_vnindex_at_date(target_date: str) -> float:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date = ?", (target_date,)
            ).fetchone()
            if row:
                return float(row[0])
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.warning("[TELEMETRY] Cannot fetch VNINDEX at %s: %s", target_date, e)
    return 0.0


def _get_vnindex_latest_before(target_date: str) -> float:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC LIMIT 1",
                (target_date,),
            ).fetchone()
            if row:
                return float(row[0])
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.warning("[TELEMETRY] Cannot fetch VNINDEX before %s: %s", target_date, e)
    return 0.0


def _determine_success(posture: str, vnindex_return: float) -> bool:
    posture_upper = posture.upper()
    if posture_upper in ("ENTER", "SCALE_IN"):
        return vnindex_return > 0.01
    elif posture_upper == "HOLD":
        return vnindex_return > -0.02
    elif posture_upper in ("REDUCE", "EXIT", "STAND_DOWN"):
        return vnindex_return < -0.005
    return False


def evaluate_single(decision_id: str, horizon_days: int) -> OutcomeRecord:
    snapshot = get_snapshot(decision_id)
    if not snapshot:
        logger.warning("[TELEMETRY] No snapshot found for %s", decision_id)
        return None

    entry_date = snapshot["timestamp"][:10] if isinstance(snapshot["timestamp"], str) else str(snapshot["timestamp"])[:10]
    entry_vnindex = snapshot["vnindex_level"]

    target_date = (datetime.strptime(entry_date, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    exit_vnindex = _get_vnindex_at_date(target_date)
    if exit_vnindex == 0.0:
        exit_vnindex = _get_vnindex_latest_before(target_date)

    vnindex_return = (exit_vnindex - entry_vnindex) / entry_vnindex if entry_vnindex > 0 else 0.0
    success = _determine_success(snapshot["posture"], vnindex_return)

    # Canonical: asset_return = VNINDEX (tài sản cơ sở), benchmark_return = risk-free (0% cho ngắn hạn)
    asset_return = vnindex_return
    benchmark_return = 0.0

    record = OutcomeRecord(
        decision_id=decision_id,
        horizon_days=horizon_days,
        vnindex_entry=entry_vnindex,
        vnindex_exit=exit_vnindex,
        vnindex_return=round(asset_return, 4),
        benchmark_return=round(benchmark_return, 4),
        success=success,
    )

    save_outcome(record)

    try:
        if snapshot.get("engine_scores"):
            import json

            raw = snapshot["engine_scores"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            scores = json.loads(raw)
            if scores:
                decompose_attribution(decision_id, horizon_days, scores)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
        logger.warning("[TELEMETRY] Attribution hook failed: %s", e)

    logger.info(
        "[TELEMETRY] Evaluated %s | %dd | return=%.2f%% | %s",
        decision_id,
        horizon_days,
        vnindex_return * 100,
        "CORRECT" if success else "WRONG",
    )
    return record


def evaluate_pending():
    results = []
    for horizon in HORIZONS:
        pending = get_pending_decisions(horizon)
        for snap in pending:
            try:
                record = evaluate_single(snap["decision_id"], horizon)
                if record:
                    results.append(record)
            except Exception as e:  # noqa: BLE001 - batch isolation: 1 decision lỗi không dừng các decision khác
                logger.error("[TELEMETRY] evaluate_single(%s, %d): %s", snap["decision_id"], horizon, e)
    logger.info("[TELEMETRY] Evaluated %d pending outcomes", len(results))
    return results


def run_telemetry_evaluation():
    logger.info("[TELEMETRY] Running scheduled evaluation...")
    try:
        return evaluate_pending()
    except Exception as e:  # noqa: BLE001 - telemetry best-effort: evaluation fail → trả [] không chặn EOD
        logger.error("[TELEMETRY] Evaluation failed: %s", e)
        return []
