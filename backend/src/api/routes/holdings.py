import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException

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

from core.holdings.holdings_view_builder import build_holdings_view
from core.holdings.exposure_engine import compute_exposure_summary

router = APIRouter()


@router.get("/holdings")
async def get_holdings_view():
    """HoldingsView: portfolio cognition — health, stress, concentration, liquidity.
    Orthogonal to OpportunityView. No recommendations. No scoring.
    """
    try:
        view = build_holdings_view()
        return view.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HoldingsView error: {str(e)}")


@router.get("/holdings/exposure")
async def get_holdings_exposure():
    """Raw exposure metrics for advanced users."""
    try:
        return compute_exposure_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Exposure error: {str(e)}")
