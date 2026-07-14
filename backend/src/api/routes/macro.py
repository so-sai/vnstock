import sys
from pathlib import Path
from typing import Optional

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
from src.models.models import MacroStatus
from src.services.macro_service import get_macro_status, get_regime_history

router = APIRouter()


@router.get("/")
async def get_macro_data(target_date: Optional[str] = Query(None, description="YYYY-MM-DD")):
    """
    Lấy trạng thái Vĩ mô + Regime Score, kèm cờ cảnh báo stale.
    Nếu không truyền target_date, lấy ngày giao dịch gần nhất.
    """
    try:
        data = get_macro_status(target_date=target_date)
        result = MacroStatus(**data).model_dump(by_alias=True)
        result["stale_warning"] = data.get("macro_stale", False)
        return localize_output(result)
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


@router.get("/status/{sensor_id}")
async def get_sensor_status(sensor_id: str):
    """Lấy trạng thái chi tiết một cảm biến vĩ mô cụ thể.
    
    Trả về: giá trị gần nhất, is_stale flag, last_updated timestamp.
    """
    try:
        from src.services.macro_service import get_macro_status
        data = get_macro_status()
        sensors = data.get("sensors", {})
        
        if sensor_id not in sensors:
            raise HTTPException(
                status_code=404,
                detail=f"Sensor '{sensor_id}' not found. Available: {list(sensors.keys())}"
            )
        
        sensor_val = sensors[sensor_id]
        # Safe tuple unpacking: handle both (value, is_stale) tuple and raw value
        if isinstance(sensor_val, tuple) and len(sensor_val) == 2:
            value, is_stale = sensor_val
        else:
            value, is_stale = sensor_val, False
        
        return localize_output({
            "sensor_id": sensor_id,
            "value": value,
            "is_stale": is_stale,
            "status": "STALE" if is_stale else "FRESH"
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sensor status error: {str(e)}")
