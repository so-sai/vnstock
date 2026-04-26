import sys
from pathlib import Path
from fastapi import APIRouter

# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

from typing import List
from src.models.models import DiamondCandidate

router = APIRouter()

@router.get("/", response_model=List[DiamondCandidate])
async def get_screener_results():
    # Mock data for initial testing
    return [
        {
            "symbol": "FPT",
            "price": 135000,
            "change_p": 2.5,
            "return_6m": 45.2,
            "signal_v1": "Breakout",
            "volume_ratio": 1.5
        },
        {
            "symbol": "VCB",
            "price": 92000,
            "change_p": -0.5,
            "return_6m": 12.8,
            "signal_v1": "None",
            "volume_ratio": 0.8
        }
    ]
