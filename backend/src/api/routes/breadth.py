import sys
from pathlib import Path

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

from src.core.canonical_output_adapter import localize_output
from src.services.breadth_service import get_breadth_analysis, get_breadth_history
from src.services.heatmap_service import get_breadth_stacked_history

router = APIRouter()


@router.get("/")
async def get_breadth():
    """Lấy phân tích độ rộng thị trường hiện tại."""
    try:
        return localize_output(get_breadth_analysis())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_breadth_history_endpoint(limit: int = Query(60, ge=1, le=365)):
    """Lấy lịch sử breadth_pct để vẽ biểu đồ."""
    try:
        return localize_output(get_breadth_history(limit=limit))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stacked-history")
async def get_breadth_stacked_endpoint(limit: int = Query(60, ge=5, le=365)):
    """
    Lấy lịch sử độ rộng 3 lớp (above MA20, between MA20-MA50, below MA50)
    dùng cho biểu đồ cột xếp chồng.
    """
    try:
        return localize_output(get_breadth_stacked_history(limit=limit))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
