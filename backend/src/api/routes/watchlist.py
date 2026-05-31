import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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

from src.portfolio.watchlist_state_manager import WatchlistStateManager, _store_pin_history
from src.portfolio.portfolio_recommendation_engine import generate_recommendations, TIER_LABELS

router = APIRouter()
manager = WatchlistStateManager()


class PinSymbolInput(BaseModel):
    symbol: str


@router.get("/pins")
async def get_pins():
    """Layer 1: Danh sách mã user đang theo dõi."""
    try:
        pins = manager.get_pins()
        return {"symbols": pins, "count": len(pins)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pin")
async def add_pin(data: PinSymbolInput):
    """Ghim mã vào danh mục theo dõi."""
    try:
        symbol = data.symbol.upper()
        ok = manager.add_pin(symbol)
        if ok:
            _store_pin_history(symbol, "PIN")
        return {"symbol": symbol, "pinned": ok, "pins": manager.get_pins()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pin/{symbol}")
async def remove_pin(symbol: str):
    """Bỏ ghim mã khỏi danh mục theo dõi."""
    try:
        sym = symbol.upper()
        ok = manager.remove_pin(sym)
        if ok:
            _store_pin_history(sym, "UNPIN")
        return {"symbol": sym, "unpinned": ok, "pins": manager.get_pins()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/is-pinned/{symbol}")
async def check_pinned(symbol: str):
    """Kiểm tra mã đã được ghim chưa."""
    try:
        return {"symbol": symbol.upper(), "pinned": manager.is_pinned(symbol.upper())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations")
async def get_recommendations():
    """Layer 2: Đề xuất AI động hôm nay (3-tier)."""
    try:
        result = generate_recommendations()
        recs = result.get('recommendations', {})
        tiers = {}
        for tier_key in ['core', 'rotation', 'opportunity']:
            items = recs.get(tier_key, [])
            tiers[tier_key] = {
                'label': TIER_LABELS.get(tier_key, tier_key.upper()),
                'symbols': [r['symbol'] for r in items[:8]],
                'details': items[:8],
            }
        return {
            'date': result['date'],
            'regime': result.get('regime', {}),
            'recommendations': tiers,
            'summary': result.get('summary', {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/combined")
async def get_combined():
    """Layer 1 + Layer 2: tổng hợp danh mục theo dõi và đề xuất AI."""
    try:
        pins = manager.get_pins()
        result = generate_recommendations()
        recs = result.get('recommendations', {})
        tiers = {}
        for tier_key in ['core', 'rotation', 'opportunity']:
            items = recs.get(tier_key, [])
            tiers[tier_key] = {
                'label': TIER_LABELS.get(tier_key, tier_key.upper()),
                'symbols': [r['symbol'] for r in items[:8]],
                'details': items[:8],
            }
        return {
            'date': result['date'],
            'user_pins': pins,
            'user_pin_count': len(pins),
            'recommendations': tiers,
            'regime': result.get('regime', {}),
            'summary': result.get('summary', {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
