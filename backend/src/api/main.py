import json
import math
import sys
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.core.errors import (
    AnalysisError,
    ConvergenceError,
    DataAccessError,
    DataIntegrityError,
    GovernorDecisionError,
    NaNArrayError,
    ProvenanceError,
    ProviderError,
    PTCKError,
    RateLimitError,
)
from starlette.responses import JSONResponse


class _NanSafeJSONResponse(JSONResponse):
    """JSONResponse that converts NaN/Infinity to null before serialization."""

    def render(self, content) -> bytes:
        return json.dumps(
            _canonicalize_json(content),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")


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


# ── Centralized Domain Exception → HTTP Status Mapping ─────────────────
# WHY: Error Discipline (Skill: code-py-314) — domain exceptions from
# governor/engine/core/services propagate to the API boundary and are
# converted here ONCE into a standard XAI JSON envelope. Routes should
# NOT re-wrap exceptions; they raise PTCKError subclasses and this handler
# maps them. This keeps status semantics consistent across the whole API.
_PTCK_STATUS_MAP: dict[type[PTCKError], int] = {
    RateLimitError: 429,
    ProviderError: 503,
    DataAccessError: 503,
    DataIntegrityError: 422,
    AnalysisError: 422,
    ConvergenceError: 422,
    NaNArrayError: 422,
    GovernorDecisionError: 409,
    ProvenanceError: 400,
}


def _status_for_error(exc: PTCKError) -> int:
    """Map a PTCKError subclass to an HTTP status, walking the MRO."""
    for cls in type(exc).__mro__:
        if cls in _PTCK_STATUS_MAP:
            return _PTCK_STATUS_MAP[cls]
    return 500


def _hydrate_path():
    # ==============================================================================
    # WHY: Nuitka 4.1.3 on Python 3.14 does NOT reliably set sys.frozen.
    # Without this, onefile mode resolves PROJECT_ROOT to the temp extraction dir.
    # Databases are at the install dir, causing "DB not found" on every API call.
    # BOUNDARY: sys.frozen check + executable name fallback.
    # ==============================================================================
    candidate = Path(sys.executable).resolve().parent
    if Path(sys.executable).stem.lower().startswith("python"):
        current = Path(__file__).resolve().parent
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                candidate = current
                break
            current = current.parent
    root_path = candidate
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    src_dir = root_path / "backend" / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.append(str(src_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.api import ipo_signal_api
from src.api.routes import (
    backtest,
    breadth,
    flow,
    gold,
    holdings,
    intelligence,
    macro,
    market_state,
    models,
    operations,
    portfolio,
    replay,
    sandbox,
    screener,
    search,
    silver,
    snapshot,
    system,
    telemetry,
    v1_absorption,
    v1_belief,
    v1_epistemic,
    v1_i18n,
    v1_macro,
    v1_screener,
    v1_system,
    watchlist,
    weekly,
    xray,
)
from src.core.canonical_output_adapter import localize_output

app = FastAPI(title="PTCK VNSTOCK API", version="1.5.2", default_response_class=_NanSafeJSONResponse)


@app.exception_handler(PTCKError)
async def ptck_error_handler(request: Request, exc: PTCKError) -> JSONResponse:
    """Chuyển mọi domain exception sang HTTP JSON chuẩn XAI.

    WHY (Error Discipline — Skill: code-py-314): governor/engine/core/
    services raise PTCKError subclasses with semantic payload; chúng
    propagate qua API boundary và được map sang HTTP status TẬP TRUNG tại
    đây. Route KHÔNG re-wrap — chỉ raise PTCKError và handler này lo.
    """
    return JSONResponse(
        status_code=_status_for_error(exc),
        content={
            "status": "error",
            "error_type": type(exc).__name__,
            "message": exc.message,
            "payload": exc.payload,
        },
    )


def get_frontend_dist_path() -> Path:
    # ==============================================================================
    # WHY: Nuitka does NOT set sys._MEIPASS (PyInstaller-specific). Use
    # sys.executable parent (install dir) instead. The frontend dist/
    # is bundled at install_dir/backend/data/frontend/dist by NSIS.
    # ==============================================================================
    if not Path(sys.executable).stem.lower().startswith("python"):
        candidate = Path(sys.executable).resolve().parent / "backend" / "data" / "frontend" / "dist"
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / "frontend" / "dist"


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
app.include_router(v1_epistemic.router, prefix="/api/v1/epistemic", tags=["Phase 15 - Epistemic Engine & Composite Dashboard"])
app.include_router(gold.router, prefix="/api/v1/gold", tags=["Gold Macro - Phase 14"])
app.include_router(silver.router, prefix="/api/v1/silver", tags=["Silver - Phase 1A"])
app.include_router(holdings.router, prefix="/api/v1/holdings", tags=["HoldingsView - Phase 15"])
app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["Telemetry - Sprint 1"])
app.include_router(weekly.router, prefix="/api/v1/weekly", tags=["Weekly Cognitive Report - Phase 16"])
app.include_router(operations.router, prefix="/api/operations", tags=["Operations - Tactical Console"])
app.include_router(search.router, prefix="/api", tags=["Search - FTS5"])
app.include_router(system.router, prefix="/api/system", tags=["System - Session Info"])
app.include_router(snapshot.router, prefix="/api/v1/snapshot", tags=["DDI Gate - Delta Divergence Index"])
app.include_router(v1_macro.router, prefix="/api/v1", tags=["V1 - Bilingual Macro"])
app.include_router(v1_absorption.router, prefix="/api/v1", tags=["V1 - Bilingual Absorption"])
app.include_router(v1_screener.router, prefix="/api/v1", tags=["V1 - Bilingual Screener"])
app.include_router(v1_system.router, prefix="/api/v1", tags=["V1 - Bilingual System"])
app.include_router(v1_belief.router, prefix="/api/v1", tags=["V1 - Belief & Sentinel"])
app.include_router(v1_i18n.router, prefix="/api/v1", tags=["V1 - i18n Dictionary"])
app.include_router(sandbox.router, prefix="/api/sandbox", tags=["Phase 5 - Paper Trading Sandbox"])


import time
from collections import deque

# ── SYNC GATE: 2FA Cooldown + 60s Window ──
# Chỉ cho phép mở van sau 12h, và chỉ mở trong 60 giây.
_request_log: deque[float] = deque(maxlen=20)
_SYNC_LOCK_FILE = Path.home() / ".ptck_vn" / "data" / "sync_lock.json"
_SYNC_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_sync_state() -> dict:
    if _SYNC_LOCK_FILE.exists():
        return json.loads(_SYNC_LOCK_FILE.read_text())
    return {"last_sync": 0, "window_open": False, "window_expiry": 0}


def _write_sync_state(state: dict):
    _SYNC_LOCK_FILE.write_text(json.dumps(state))


def _check_cooldown() -> tuple[bool, float]:
    state = _read_sync_state()
    now = time.time()
    last_sync = state.get("last_sync", 0)
    remaining = (12 * 3600) - (now - last_sync)
    if remaining > 0:
        return False, remaining
    return True, 0


def _is_window_open() -> bool:
    state = _read_sync_state()
    now = time.time()
    if state.get("window_open", False) and state.get("window_expiry", 0) > now:
        return True
    # Auto-close expired window
    if state.get("window_open", False):
        state["window_open"] = False
        _write_sync_state(state)
    return False


@app.middleware("http")
async def circuit_breaker(request: Request, call_next):
    """Van ngắt: Offline mặc định. Chỉ mở khi 2FA xác nhận."""
    now = time.time()
    path = request.url.path

    # Whitelist: luôn cho phép health + gate endpoints
    # WHY session-info trong whitelist: Frontend BackendGate block render
    # đến khi endpoint này trả ok. Nếu để dưới van ngắt/rate-limit,
    # user thấy white-screen vĩnh viễn dù backend đang sống (anti-pattern:
    # health checks must never be rate-limited). Read-only 1 query DB.
    if path in (
        "/health",
        "/api",
        "/api/system/gate",
        "/api/system/gate/open",
        "/api/system/gate/close",
        "/api/system/session-info",
    ):
        return await call_next(request)

    # Nếu cửa sổ sync chưa mở, chặn mọi /api/* (trừ operations đã được xác nhận thủ công)
    if not _is_window_open() and path.startswith("/api/") and not path.startswith("/api/operations/"):
        return JSONResponse(
            status_code=503,
            content={"error": "OFFLINE", "message": "Hệ thống đang ở chế độ Offline. Bấm CHỐT DỮ LIỆU để mở van 60 giây."},
        )

    # Rate limit trong cửa sổ mở: tối đa 15 req/phút
    _request_log.append(now)
    recent = sum(1 for t in _request_log if now - t < 60)
    if recent > 15:
        return JSONResponse(
            status_code=429,
            content={"error": "RATE_LIMITED", "message": "Hệ thống đang bảo vệ IP — quá nhiều request. Đợi 60 giây."},
        )

    return await call_next(request)


@app.get("/api/system/gate")
async def get_gate_status():
    can_sync, remaining = _check_cooldown()
    window_open = _is_window_open()
    state = _read_sync_state()
    return {
        "can_sync": can_sync,
        "remaining_seconds": int(remaining) if not can_sync else 0,
        "window_open": window_open,
        "window_expiry": int(state.get("window_expiry", 0)),
    }


@app.post("/api/system/gate/open")
async def open_sync_window():
    """Mở van 60 giây sau khi vượt qua cooldown 12 giờ."""
    can_sync, remaining = _check_cooldown()
    if not can_sync:
        return JSONResponse(
            status_code=403,
            content={
                "error": "COOLDOWN",
                "message": f"Chưa đủ 12 giờ. Còn {int(remaining)} giây.",
                "remaining": int(remaining),
            },
        )
    state = _read_sync_state()
    now = time.time()
    state["last_sync"] = now
    state["window_open"] = True
    state["window_expiry"] = now + 60
    _write_sync_state(state)
    return {"status": "OPEN", "message": "Van đã mở trong 60 giây", "expiry": int(state["window_expiry"])}


@app.post("/api/system/gate/close")
async def close_sync_window():
    """Đóng van thủ công hoặc sau khi tải xong dữ liệu."""
    state = _read_sync_state()
    state["window_open"] = False
    state["window_expiry"] = 0
    _write_sync_state(state)
    return {"status": "CLOSED", "message": "Van đã đóng"}


@app.get("/api")
async def root():
    return localize_output(
        {
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
                "/api/v1/snapshot/ddi",
                "/api/v1/snapshot/index",
                "/api/v1/snapshot/params",
            ],
        }
    )


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
    except Exception as _exc:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        import logging as _logging

        _logging.getLogger(__name__).warning("[CAGL] Verification skipped: %s", _exc)
