import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException


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
from src.services.dashboard_service import get_dashboard_data

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard():
    """
    Trạm biến áp trung tâm — gộp Macro + Breadth + Screener + Regime History.
    Single endpoint cho trang chủ Frontend.
    """
    try:
        return localize_output(get_dashboard_data())
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        raise HTTPException(status_code=500, detail=f"Dashboard error: {str(e)}")
