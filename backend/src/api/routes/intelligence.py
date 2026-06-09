"""
Phase 12 — Actionable Intelligence API endpoints.
Compresses all engine outputs into simple, actionable decisions.
"""
import sys, logging
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query

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

import logging
from src.services.actionable_intelligence_service import (
    get_live_summary,
    get_portfolio_coach,
    get_opportunity_queue,
    get_scenario_simulation,
    get_position_narrative,
)

logger = logging.getLogger(__name__)
from src.core.canonical_output_adapter import localize_output
router = APIRouter()


@router.get("/live-summary")
async def live_summary():
    """1-glance live market summary — regime + decision + liquidity + rotation."""
    try:
        return localize_output(get_live_summary())
    except Exception as e:
        logger.error(f"Live summary endpoint failed: {type(e).__name__}: {e}")
        return localize_output({
            "regime": "RANGING",
            "decision": {"action": "HOLD", "confidence": 50, "risk": "SAFE", "constraint": "ALLOWED"},
            "liquidity_phase": "NEUTRAL",
            "rotation_regime": "NEUTRAL",
            "breakout_context": "LOW_BREAKOUT_ACTIVITY",
            "positions_count": 0,
            "coach_instruction": "Hệ thống đang thu thập dữ liệu thị trường. Vui lòng quay lại sau phiên giao dịch.",
            "updated_at": datetime.now().isoformat(),
        })


@router.get("/coach")
async def portfolio_coach():
    """What should I do next? Plain-language mentor advice."""
    try:
        return localize_output(get_portfolio_coach())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities")
async def opportunity_queue(top_n: int = Query(5, ge=1, le=20)):
    """Top actionable buy/sell opportunities ranked by combined score."""
    try:
        return localize_output(get_opportunity_queue(top_n))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scenario")
async def scenario_simulation(scenario: str = Query("drop_5pct", pattern="^(drop_5pct|drop_10pct|surge_3pct)$")):
    """What-if simulation for market scenarios."""
    try:
        return localize_output(get_scenario_simulation(scenario))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position-narrative/{symbol}")
async def position_narrative(symbol: str):
    """Is this position still valid? Full narrative for a held position."""
    try:
        return localize_output(get_position_narrative(symbol.upper()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
