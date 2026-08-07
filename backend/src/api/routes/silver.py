"""
Silver API Router — /api/v1/silver/
Cung cấp: giá bạc nội địa (BTMC), thế giới (XAGUSD), Gold/Silver Ratio.
"""

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
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from core.macro.precious_metal_ratio import assess_gs_ratio_regime, calculate_gold_silver_ratio, get_gs_ratio_from_db
from src.core.canonical_output_adapter import localize_output
from src.services.macro.gold_world_service import fetch_world_gold_live
from src.services.macro.silver_service import get_silver_dashboard as get_silver_dashboard_data
from src.services.macro.silver_world_service import fetch_world_silver_live, seed_world_silver_to_db

router = APIRouter()


@router.get("/")
async def get_silver_dashboard():
    """Lấy giá bạc BTMC nội địa + world XAGUSD + Gold/Silver Ratio."""
    try:
        dash = get_silver_dashboard_data()
        xag = fetch_world_silver_live()
        xau = fetch_world_gold_live()
        gs_ratio = calculate_gold_silver_ratio(xau, xag)
        gs_regime = assess_gs_ratio_regime(gs_ratio)

        return localize_output(
            {
                "domestic_silver": {
                    "buy": dash.get("btmc_buy", 0),
                    "sell": dash.get("btmc_sell", 0),
                    "spread": dash.get("btmc_spread", 0),
                    "brand": dash.get("brand", ""),
                    "source": "BTMC",
                },
                "world_silver": {
                    "xag_usd": xag if xag else 0,
                },
                "gold_silver_ratio": gs_regime,
                "timestamp": int(__import__("time").time()),
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Silver dashboard error: {str(e)}")


@router.get("/world")
async def get_world_silver():
    """Lấy giá bạc thế giới XAGUSD."""
    try:
        price = fetch_world_silver_live()
        if price is None:
            raise HTTPException(status_code=503, detail="World silver service unavailable")
        return localize_output({"xag_usd": price, "timestamp": int(__import__("time").time())})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"World silver error: {str(e)}")


@router.get("/gs-ratio")
async def get_gold_silver_ratio():
    """Lấy Gold/Silver Ratio + regime classification."""
    try:
        return localize_output(get_gs_ratio_from_db())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GS ratio error: {str(e)}")


@router.post("/seed")
async def seed_world_silver():
    """Seed XAGUSD từ yfinance vào macro_history."""
    try:
        ok = seed_world_silver_to_db()
        return localize_output({"seeded": ok})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Silver seed error: {str(e)}")
