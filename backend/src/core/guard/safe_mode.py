from typing import Any

from .color_engine import COLOR_MAP, ColorDecision

SAFE_MODE_ACTIVE_KEY = "safe_mode_active"

FALLBACK_DECISION_VIEW = {
    "recommended_posture": "QUAN_SÁT",
    "risk_level": "THẤP",
    "risk_color": COLOR_MAP["GRAY"],
    "dominant_signal_vi": "Không xác định",
    "signal_alignment": 0.0,
    "confidence_summary_vi": "Chưa đủ dữ liệu",
    "primary_conflict_vi": None,
    "short_explanation_vi": "Hệ thống chưa đủ tín hiệu để đưa ra nhận định. Vui lòng kiểm tra kết nối dữ liệu.",
    "time_horizon": "NGẮN_HẠN",
    "decision_urgency": 0.0,
    "entropy_state": "HỘI_TỤ",
}


def should_activate_safe_mode(state: dict[str, Any]) -> bool:
    if state.get("system_healthy") is False:
        return True
    if state.get("engine_count", 0) == 0:
        return True
    if state.get("active_signals", 0) == 0:
        return True
    if state.get("freshness", 1.0) < 0.3:
        return True
    return False


def get_fallback_color() -> ColorDecision:
    return ColorDecision(
        token="GRAY",
        hex=COLOR_MAP["GRAY"],
        label_vi="Chưa đủ tín hiệu",
        reason="Safe mode active — no reliable data available",
    )


def get_fallback_decision_view() -> dict:
    return dict(FALLBACK_DECISION_VIEW)
