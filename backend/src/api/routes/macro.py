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

from src.models.models import MacroStatus

router = APIRouter()

@router.get("/", response_model=MacroStatus)
async def get_macro_data():
    # Mock data for initial testing
    return {
        "usd_cnh": 7.24,
        "copper_price": 9500.0,
        "dxy_index": 104.5,
        "interbank_rate": 4.2,
        "sbv_action": "Neutral",
        "risk_level": "Emerald"
    }
