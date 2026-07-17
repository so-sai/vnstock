"""v1_macro.py — API endpoint: trạng thái vĩ mô song ngữ.

Cung cấp macro status dưới định dạng HCI (Human-Computer Interface)
với localization song ngữ EN/VI cho mọi tín hiệu.
"""
import sys
from datetime import datetime
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

from src.core.bilingual_schema import (
    REGIME_STATES, EARLY_WARNING, GOVERNOR_STATES, to_hci,
)

router = APIRouter()


def _score_to_signal(score: float) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= 70:
        return "TRENDING"
    if score >= 40:
        return "RANGING"
    return "CRISIS"


@router.get("/macro/status")
async def get_macro_status_v1(target_date: Optional[str] = Query(None, description="YYYY-MM-DD")):
    """Trạng thái vĩ mô song ngữ (HCI format).

    Trả về regime + early warning + governor status dưới dạng
    metric array với localization EN/VI cho mỗi tín hiệu.
    """
    try:
        from src.services.macro_service import get_macro_status as _get_macro_status

        data = _get_macro_status(target_date=target_date)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Macro engine error: {str(e)}")

    regime_score = data.get("regime_score", data.get("score"))
    regime_signal = _score_to_signal(regime_score)
    stale = data.get("macro_stale", data.get("stale", False))

    sensors = data.get("sensors", {})
    early_warnings = data.get("early_warnings", data.get("warnings", []))

    metrics = [
        to_hci("REGIME_SCORE", regime_score, regime_signal, REGIME_STATES),
    ]

    for sensor_id, sensor_val in sensors.items():
        if isinstance(sensor_val, (list, tuple)) and len(sensor_val) > 1:
            value = sensor_val[0]
        else:
            value = sensor_val
        signal = _score_to_signal(value if isinstance(value, (int, float)) else None)
        metrics.append(to_hci(sensor_id.upper(), value, signal, REGIME_STATES))

    warns = []
    for ew in (early_warnings if isinstance(early_warnings, list) else []):
        ew_key = ew if isinstance(ew, str) else ew.get("type", "UNKNOWN")
        ew_entry = EARLY_WARNING.get(ew_key)
        if ew_entry:
            warns.append(to_hci("EARLY_WARNING", 1.0, ew_key, EARLY_WARNING))

    governor_state = data.get("governor_state", "RISK_ON")
    hdr = data.get("hdr", data.get("hdr_global", 0.0))

    return {
        "target_date": data.get("target_date", target_date or datetime.now().strftime("%Y-%m-%d")),
        "timestamp": datetime.now().isoformat(),
        "stale": stale,
        "metrics": metrics,
        "early_warnings": warns,
        "governor": {
            "state": to_hci("GOVERNOR", governor_state, governor_state, GOVERNOR_STATES),
            "hdr_global": hdr,
        },
    }
