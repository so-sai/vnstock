"""v1_epistemic.py — Epistemic REST API Route.

WHY:
  Cung cấp REST API endpoint cho Frontend Dashboard (React/TypeScript) để truy xuất:
    1. Composite Score Dashboard (0-100 Scale) kèm Veto Flags, Coverage, Coherence, Target Alloc %, Delta Action, Gap Mua.
    2. Deep Data Density Audit Report (Mật độ BCTC 30 quý 2019Q1-2026Q2) & Missing Quarters.
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.core.canonical_output_adapter import localize_output

router = APIRouter()

DEFAULT_TARGETS = [
    "FPT",
    "ACB",
    "HDB",
    "MBB",
    "VCB",
    "HPG",
    "VHM",
    "DGC",
    "MWG",
    "GAS",
    "IJC",
    "BCM",
    "VPB",
]


@router.get("/composite", summary="Composite Score & Epistemic Dashboard JSON")
async def get_composite_dashboard(
    symbols: list[str] | None = Query(None, description="Danh sách mã cổ phiếu"),
    policy: str = Query("BALANCED", description="Chính sách vận hành: CONSERVATIVE, BALANCED, AGGRESSIVE"),
):
    """Truy xuất Bảng điểm Tổng hợp Epistemic Composite Score (0-100) cho REST API Frontend."""
    syms = symbols if symbols else DEFAULT_TARGETS
    try:
        from src.governor.company_state import BayesianGovernor
        from src.governor.composite_score_projector import POLICIES, CompositeScoreProjector

        policy_obj = POLICIES.get(policy.upper(), POLICIES["BALANCED"])
        projector = CompositeScoreProjector(policy=policy_obj)

        engine = BayesianGovernor()
        analysis = engine.analyze(syms)
        engine.close()

        mandates = list(analysis.get("results", {}).values())
        results = projector.project_batch(mandates)

        data = [
            {
                "symbol": r.symbol,
                "macro_score": r.macro_score,
                "internal_score": r.internal_score,
                "market_score": r.market_score,
                "raw_score": r.raw_score,
                "final_score": r.final_score,
                "coverage": r.coverage,
                "coherence": r.coherence,
                "buy_gap": r.buy_gap,
                "target_alloc_pct": r.allocation_pct,
                "action_delta_pct": r.delta_pct,
                "margin_status": r.display_flag if "FULL_MARGIN" in r.display_flag else "NO_MARGIN",
                "governor_mandate": r.governor_mandate,
                "why_drivers": r.why_drivers,
                "veto_flag": r.veto_flag,
                "action": r.action,
                "recommendation": r.recommendation,
            }
            for r in results
        ]

        return localize_output(
            {
                "status": "success",
                "policy": policy_obj.name,
                "count": len(data),
                "generated_at": datetime.now().isoformat(),
                "data": data,
            }
        )
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        return localize_output(
            {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }
        )


@router.get("/data-density", summary="Deep Data Density Audit Report")
async def get_data_density_audit(
    symbols: list[str] | None = Query(None, description="Danh sách mã cổ phiếu"),
):
    """Truy xuất Báo cáo Mật độ Dữ liệu BCTC Chuyên sâu 30 quý gần nhất (2019Q1-2026Q2)."""
    syms = symbols if symbols else DEFAULT_TARGETS
    try:
        from src.audit.data_integrity_auditor import DataIntegrityAuditor

        auditor = DataIntegrityAuditor()
        audit_results = auditor.audit_many(syms)

        data = [
            {
                "symbol": r.symbol,
                "required_quarters": r.required_quarters,
                "available_quarters": r.available_quarters,
                "density_pct": r.density_pct,
                "missing_quarters": r.missing_quarters,
                "status": r.status,
                "healed": r.healed,
            }
            for r in audit_results.values()
        ]

        return localize_output(
            {
                "status": "success",
                "count": len(data),
                "generated_at": datetime.now().isoformat(),
                "data": data,
            }
        )
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        return localize_output(
            {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }
        )


@router.get("/vn20", summary="PTCK_VN20 Dynamic Index (Top 20 Stocks)")
async def get_vn20_index():
    """Return the dynamically built PTCK_VN20 index with sector distribution,
    silent throttle status, and multi-source fallback pipeline info."""
    from src.ptck_vn20_builder import get_vn20_api_data

    try:
        data = get_vn20_api_data()
        return localize_output(
            {
                "status": "success",
                "index": data.get("index", "PTCK_VN20"),
                "policy": "PTCK_VN20_DYNAMIC",
                "count": data.get("count", 0),
                "built_at": data.get("built_at", ""),
                "generated_at": data.get("built_at", ""),
                "data_density_avg": data.get("data_density_avg", 0),
                "fallback_active_source": data.get("fallback_active_source", "VCI"),
                "silent_throttle_status": data.get("silent_throttle_status", {}),
                "sector_distribution": data.get("sector_distribution", {}),
                "index_constituents": data.get("index_constituents", []),
                "timestamp": datetime.now().isoformat(),
            }
        )
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        return localize_output(
            {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }
        )


@router.get("/vn20/stream")
async def stream_vn20_status(request: Request):
    """SSE stream for PTCK_VN20 real-time telemetry (throttle status, live crawler events)."""
    from src.ptck_vn20_builder import get_current_throttle_status

    async def event_generator():
        last_payload_hash: str = ""
        while True:
            if await request.is_disconnected():
                break

            throttle = get_current_throttle_status()
            payload = json.dumps(throttle, ensure_ascii=False)

            # Only emit when state changes (avoid redundant pushes)
            payload_hash = str(hash(payload))
            if payload_hash != last_payload_hash:
                last_payload_hash = payload_hash
                yield f"event: throttle_update\ndata: {payload}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
