import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


def _hydrate_path():
    import os as _os
    import tempfile as _tempfile
    _temp_root = Path(_tempfile.gettempdir()).resolve()
    is_frozen = (
        getattr(sys, 'frozen', False)
        or not Path(sys.executable).stem.lower().startswith("python")
    )
    if is_frozen:
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    try:
        resolved = root_path.resolve()
        if _temp_root in resolved.parents or resolved == _temp_root:
            root_path = Path(sys.executable).resolve().parent
    except (OSError, RuntimeError):
        pass
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.core.macro.gold_regime_engine import analyze_gold_regime, cross_reference_with_market
from src.core.macro.gold_spread_engine import analyze_domestic_premium, get_premium_driver

from src.core.canonical_output_adapter import localize_output
from src.services.macro.gold_service import get_gold_cognition_layer, get_gold_dashboard
from src.services.macro.gold_world_service import fetch_world_gold_live, seed_world_gold_to_db
from src.services.macro_service import get_macro_status

router = APIRouter()


@router.get("/")
async def get_gold_data():
    """Lấy dữ liệu giá vàng SJC + BTMC + spread."""
    try:
        return localize_output(get_gold_dashboard())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold service error: {str(e)}")


@router.get("/regime")
async def get_gold_regime():
    """Lấy gold regime + cognitive signals."""
    try:
        regime = analyze_gold_regime()
        macro = {}
        try:
            macro = get_macro_status()
        except Exception:
            pass
        cognition = cross_reference_with_market(macro)
        return localize_output({
            "regime": regime,
            "cognition": cognition.get("gold_cognition", {}),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold regime error: {str(e)}")


@router.get("/world")
async def get_world_gold():
    """Lấy giá vàng thế giới XAUUSD từ Stooq."""
    try:
        price = fetch_world_gold_live()
        if price is None:
            raise HTTPException(status_code=503, detail="World gold service unavailable")
        return localize_output({"xau_usd": price, "timestamp": int(__import__("time").time())})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"World gold error: {str(e)}")


@router.get("/premium")
async def get_gold_premium():
    """Lấy Domestic Premium: SJC - XAUUSD quy đổi."""
    try:
        return localize_output(analyze_domestic_premium())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold premium error: {str(e)}")


@router.get("/premium/driver")
async def get_gold_premium_driver(lookback_days: int = Query(5, description="Số phiên nhìn lại để so baseline")):
    """Phân tích nguyên nhân premium thay đổi: XAUUSD, USD/VND, hay SJC."""
    try:
        return localize_output(get_premium_driver(lookback_days=lookback_days))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold driver analysis error: {str(e)}")


@router.get("/cognition")
async def get_gold_cognition():
    """Gold Cognition Layer — hợp nhất VN + Global + Premium."""
    try:
        return localize_output(get_gold_cognition_layer())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold cognition error: {str(e)}")


@router.post("/seed")
async def seed_world_gold():
    """Seed XAUUSD từ Stooq vào macro_history."""
    try:
        ok = seed_world_gold_to_db()
        return localize_output({"seeded": ok})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gold seed error: {str(e)}")
