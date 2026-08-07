"""v1_macro.py — API endpoint: trạng thái vĩ mô song ngữ.

Cung cấp macro status dưới định dạng HCI (Human-Computer Interface)
với localization song ngữ EN/VI cho mọi tín hiệu.
"""

# WHY: Đây là endpoint v1 riêng (song song với macro.py không version) vì phục vụ nhóm
# client mới cần định dạng HCI chuẩn hóa (metric array + to_hci) và localization song ngữ
# ngay tại API. Tách route giúp v1 tiến hóa schema độc lập không phá vỡ client cũ đang
# đọc JSON flat từ macro.py.
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


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
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.core.bilingual_schema import (
    EARLY_WARNING,
    GOVERNOR_STATES,
    REGIME_STATES,
    to_hci,
)

router = APIRouter()


def _score_to_signal(score: float) -> str:
    # WHY: Bản đồ score→signal dùng thang 0-100 đơn giản (≥70 TRENDING, ≥40 RANGING,
    # còn lại CRISIS) để khớp ngưỡng status mà regime_engine dùng (0.65/0.35) — cùng một
    # ngôn ngữ trạng thái giữa lớp tính toán và lớp hiển thị, tránh lệch nhận định.
    if score is None:
        return "UNKNOWN"
    if score >= 70:
        return "TRENDING"
    if score >= 40:
        return "RANGING"
    return "CRISIS"


@router.get("/macro/status")
async def get_macro_status_v1(target_date: str | None = Query(None, description="YYYY-MM-DD")):
    """Trạng thái vĩ mô song ngữ (HCI format).

    Trả về regime + early warning + governor status dưới dạng
    metric array với localization EN/VI cho mỗi tín hiệu.
    """
    try:
        from src.services.macro_service import get_macro_status as _get_macro_status

        data = _get_macro_status(target_date=target_date)
    except RuntimeError as e:
        # WHY: RuntimeError đặc tả "dữ liệu chưa sẵn sàng" nên map 503 Service Unavailable
        # để client biết đây là tạm thời (retry), còn lỗi khác là 500 — phân biệt giúp
        # monitoring không gắn cờ nhầm một nguồn dữ liệu nghỉ lễ thành sự cố hệ thống.
        raise HTTPException(status_code=503, detail=str(e))
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
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
        # WHY: Sensor value có 2 định dạng lịch sử — tuple (value, is_stale) hoặc raw value.
        # Ưu tiên tuple để lấy phần tử đầu là value thực, còn raw value phục vụ schema cũ;
        # chấp nhận cả hai để v1 không phụ thuộc service trả về đúng một kiểu.
        if isinstance(sensor_val, (list, tuple)) and len(sensor_val) > 1:
            value = sensor_val[0]
        else:
            value = sensor_val
        signal = _score_to_signal(value if isinstance(value, (int, float)) else None)
        metrics.append(to_hci(sensor_id.upper(), value, signal, REGIME_STATES))

    warns = []
    for ew in early_warnings if isinstance(early_warnings, list) else []:
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
