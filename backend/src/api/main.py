import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


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
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.api.routes import macro, screener, models, breadth, portfolio, backtest, xray, replay, intelligence, flow

app = FastAPI(title="PTCK VNSTOCK API", version="1.5.2")


def get_frontend_dist_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "frontend" / "dist"
        if candidate.exists():
            return candidate
    candidate = PROJECT_ROOT / "frontend" / "dist"
    return candidate

frontend_dist = get_frontend_dist_path()
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

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
app.include_router(flow.router, prefix="/api/v1/flow", tags=["Phase 12B - Asia Flow Map"])


@app.get("/")
async def root():
    return {
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
            "/api/v1/flow/map",
        ],
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}
