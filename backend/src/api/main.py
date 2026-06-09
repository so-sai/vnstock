import sys
import math
import json
import numpy as np
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse


class _NanSafeJSONResponse(JSONResponse):
    """JSONResponse that converts NaN/Infinity to null before serialization."""
    def render(self, content) -> bytes:
        return json.dumps(
            _canonicalize_json(content),
            ensure_ascii=False,
            allow_nan=False,
        ).encode('utf-8')


def _canonicalize_json(obj):
    if isinstance(obj, dict):
        return {k: _canonicalize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if (np.isnan(obj) or np.isinf(obj)) else float(obj)
    if isinstance(obj, np.ndarray):
        return _canonicalize_json(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


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
    backend_dir = root_path / "backend"
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.core.canonical_output_adapter import localize_output
from src.api.routes import macro, screener, models, breadth, portfolio, backtest, xray, replay, intelligence, flow, watchlist, market_state, gold, holdings, telemetry, weekly
from src.api import ipo_signal_api

app = FastAPI(title="PTCK VNSTOCK API", version="1.5.2", default_response_class=_NanSafeJSONResponse)


def get_frontend_dist_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "frontend" / "dist"
        if candidate.exists():
            return candidate
    candidate = PROJECT_ROOT / "frontend" / "dist"
    return candidate

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(macro.router, prefix="/api/macro", tags=["Macro"])
app.include_router(screener.router, prefix="/api/screener", tags=["Screener"])
app.include_router(models.router, prefix="/api/models", tags=["Dashboard"])
app.include_router(breadth.router, prefix="/api/breadth", tags=["Breadth"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["Backtest"])
app.include_router(xray.router, prefix="/api/xray", tags=["XRay"])
app.include_router(replay.router, prefix="/api/replay", tags=["Replay"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["Phase 12 - Actionable Intelligence"])
app.include_router(ipo_signal_api.router, prefix="/api", tags=["IPO Signal"])
app.include_router(flow.router, prefix="/api/v1/flow", tags=["Phase 12B - Asia Flow Map"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["Watchlist"])
app.include_router(market_state.router, prefix="/api/v1/market-state", tags=["Phase 13 - Market State Coordinator"])
app.include_router(gold.router, prefix="/api/v1/gold", tags=["Gold Macro - Phase 14"])
app.include_router(holdings.router, prefix="/api/v1/holdings", tags=["HoldingsView - Phase 15"])
app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["Telemetry - Sprint 1"])
app.include_router(weekly.router, prefix="/api/v1/weekly", tags=["Weekly Cognitive Report - Phase 16"])


@app.get("/api")
async def root():
    return localize_output({
        "message": "PTCK VNSTOCK API v1.5.2 - Diamond Shield Trading System",
        "docs": "/docs",
        "endpoints": [
            "/api/macro/",
            "/api/macro/history",
            "/api/screener/",
            "/api/screener/rankings",
            "/api/models/dashboard",
            "/api/breadth/",
            "/api/breadth/history",
            "/api/portfolio/",
            "/api/backtest/",
            "/api/backtest/stress-test",
            "/api/xray/{symbol}",
            "/api/replay/timeline",
            "/api/intelligence/live-summary",
            "/api/intelligence/coach",
            "/api/intelligence/opportunities",
            "/api/intelligence/scenario",
            "/api/intelligence/position-narrative/{symbol}",
            "/api/intelligence/ipo-signal/",
            "/api/intelligence/ipo-signal/history/",
            "/api/v1/flow/banner",
            "/api/watchlist/pins",
            "/api/watchlist/pin",
            "/api/watchlist/pin/{symbol}",
            "/api/watchlist/is-pinned/{symbol}",
            "/api/watchlist/recommendations",
            "/api/watchlist/combined",
            "/api/v1/market-state/",
            "/api/v1/market-state/meta",
            "/api/v1/market-state/regime",
            "/api/v1/gold/",
            "/api/v1/gold/regime",
            "/api/v1/holdings/",
            "/api/v1/holdings/exposure",
            "/api/v1/telemetry/",
            "/api/v1/telemetry/stats",
            "/api/v1/telemetry/{decision_id}",
            "/api/v1/telemetry/{decision_id}/evaluate",
            "/api/v1/telemetry/attribution/{decision_id}",
            "/api/v1/telemetry/attribution/{decision_id}/summary-vi",
            "/api/v1/telemetry/engines",
            "/api/v1/telemetry/engines/refresh",
            "/api/v1/weekly/",
        ],
    })


@app.get("/health")
async def health_check():
    return localize_output({"status": "ok"})


frontend_dist = get_frontend_dist_path()
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

# CAGL boot-time verification (set CAGL_MODE=WARN / STRICT / SHADOW to enable)
import os as _os
_cagl_mode = _os.environ.get("CAGL_MODE", "").upper()
if _cagl_mode in ("SHADOW", "WARN", "STRICT"):
    try:
        from src.core.cagl import verify_cagl
        verify_cagl(app, mode=_cagl_mode)
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger(__name__).warning("[CAGL] Verification skipped: %s", _exc)
