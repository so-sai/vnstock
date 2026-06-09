import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

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

from src.models.models import MacroStatus
from src.services.macro_service import get_macro_status, get_regime_history

from src.core.canonical_output_adapter import localize_output
router = APIRouter()


@router.get("/")
async def get_macro_data(target_date: Optional[str] = Query(None, description="YYYY-MM-DD")):
    """
    Lấy trạng thái Vĩ mô + Regime Score.
    Nếu không truyền target_date, lấy ngày giao dịch gần nhất.
    """
    try:
        data = get_macro_status(target_date=target_date)
        return localize_output(MacroStatus(**data).model_dump(by_alias=True))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Macro engine error: {str(e)}")


@router.get("/history")
async def get_macro_history(limit: int = Query(90, ge=1, le=365)):
    """Lấy lịch sử Regime Score để vẽ biểu đồ Timeline."""
    try:
        return localize_output(get_regime_history(limit=limit))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
