"""
/api/v1/telemetry — Decision Telemetry endpoints (Sprint 1 + 2).
Provides snapshots, outcomes, attribution, and engine performance.
"""
import sys
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


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

from src.telemetry.storage import (
    get_all_snapshots,
    get_outcomes,
    get_snapshot,
    get_snapshot_stats,
    initialize_telemetry_database,
    get_attributions,
    get_attribution_summary,
    get_engine_performance,
)
from src.telemetry.evaluator import evaluate_single, evaluate_pending, HORIZONS
from src.telemetry.attribution import update_engine_performance as refresh_perf, generate_summary_vi


@router.get("/", summary="All decision snapshots")
async def get_telemetry_snapshots(limit: int = Query(20, ge=1, le=200)):
    try:
        initialize_telemetry_database()
        return get_all_snapshots(limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", summary="Telemetry statistics")
async def get_telemetry_stats():
    try:
        initialize_telemetry_database()
        return get_snapshot_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{decision_id}", summary="Single decision snapshot")
async def get_telemetry_decision(decision_id: str):
    try:
        initialize_telemetry_database()
        snap = get_snapshot(decision_id)
        if not snap:
            raise HTTPException(status_code=404, detail="Decision not found")
        outcomes = get_outcomes(decision_id)
        return {"snapshot": snap, "outcomes": outcomes}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{decision_id}/evaluate", summary="Evaluate a decision")
async def evaluate_decision(
    decision_id: str,
    horizon: int = Query(20, description="Evaluation horizon in days"),
):
    try:
        initialize_telemetry_database()
        record = evaluate_single(decision_id, horizon)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Cannot evaluate — snapshot missing or no market data",
            )
        return record
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evaluate-pending", summary="Evaluate all pending decisions")
async def evaluate_all_pending():
    try:
        initialize_telemetry_database()
        results = evaluate_pending()
        return {"evaluated": len(results), "horizons": HORIZONS}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Sprint 2: Attribution ─────────────────────────────────


@router.get("/attribution/{decision_id}", summary="Engine attribution for a decision")
async def get_decision_attribution(
    decision_id: str,
    horizon: int = Query(20, description="Evaluation horizon in days"),
):
    try:
        initialize_telemetry_database()
        summary = get_attribution_summary(decision_id, horizon)
        if not summary:
            raise HTTPException(status_code=404, detail="No attribution found")
        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/attribution/{decision_id}/summary-vi", summary="Vietnamese UI summary for a decision")
async def get_attribution_summary_vi(
    decision_id: str,
    horizon: int = Query(20, description="Evaluation horizon in days"),
):
    try:
        initialize_telemetry_database()
        summary = generate_summary_vi(decision_id, horizon)
        if not summary:
            raise HTTPException(status_code=404, detail="No attribution summary available")
        return summary.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engines", summary="Engine performance summary")
async def get_engine_perf(engine: str = Query(None, description="Filter by engine")):
    try:
        initialize_telemetry_database()
        return get_engine_performance(engine)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/engines/refresh", summary="Recalculate engine performance")
async def refresh_engine_perf(window: int = Query(30, ge=7, le=90)):
    try:
        initialize_telemetry_database()
        results = refresh_perf(window_days=window)
        return {"updated": len(results), "window_days": window}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
