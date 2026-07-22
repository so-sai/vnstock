import asyncio
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
from src.services.xray_service import get_xray_data

router = APIRouter()


@router.get("/{symbol}")
async def xray_endpoint(symbol: str, timeframe: str = Query("D", pattern="^(D|W|M)$")):
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_xray_data, symbol, timeframe)
        return localize_output(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"X-Ray error: {str(e)}")
