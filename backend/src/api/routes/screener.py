import asyncio
import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


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

from src.core.canonical_output_adapter import localize_output
from src.services.heatmap_service import get_heatmap_data
from src.services.screener_service import get_rs_rankings, get_screener_results

router = APIRouter()


@router.get("/")
async def get_screener_results_endpoint(top_n: int = Query(50, ge=1, le=200)):
    """
    Lấy kết quả Screener + RS Rating.
    Nếu thị trường CRISIS (không có breakout), tự động fallback sang top RS Rating.
    """
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, get_screener_results, top_n)
        return localize_output(results)
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        raise HTTPException(status_code=500, detail=f"Screener error: {str(e)}")


@router.get("/rankings")
async def get_rs_rankings_endpoint(top_n: int = Query(100, ge=1, le=500)):
    """Lấy danh sách xếp hạng RS Rating."""
    try:
        loop = asyncio.get_event_loop()
        return localize_output(await loop.run_in_executor(None, get_rs_rankings, top_n))
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/heatmap")
async def get_heatmap_endpoint(top_n: int = Query(50, ge=5, le=200)):
    """
    Lấy ma trận nhiệt RS lịch sử 10 phiên cho top N mã.
    """
    try:
        loop = asyncio.get_event_loop()
        return localize_output(await loop.run_in_executor(None, get_heatmap_data, top_n))
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        raise HTTPException(status_code=500, detail=str(e))
