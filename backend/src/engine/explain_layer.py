"""
explain_layer.py — Vietnamese consciousness layer.

Pure function layer that translates internal control state into
Vietnamese human-readable narrative. No new computation, no state,
no effect on ROM/Hazard/Driver.

This is the "semantic compiler" of the control system.
"""

# ── Internal → Vietnamese mapping dictionaries ──────────────────────────

DRIVER_VI = {
    "BREADTH": "Độ rộng thị trường",
    "FLOW": "Dòng tiền",
    "STRUCTURE": "Cấu trúc thị trường",
    "VOLATILITY": "Biến động",
    "MOMENTUM": "Đà tăng",
    "MACRO": "Yếu tố vĩ mô",
    "UNKNOWN": "Không xác định",
}

REGIME_VI = {
    "TRENDING": "có xu hướng rõ ràng",
    "RANGING": "đi ngang",
    "CRISIS": "rủi ro cao",
}

REGIME_NOUN_VI = {
    "TRENDING": "xu hướng",
    "RANGING": "đi ngang",
    "CRISIS": "rủi ro",
}


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.6:
        return "cao"
    elif confidence >= 0.4:
        return "trung bình"
    return "thấp"


def _sharpness_label(sharpness: float) -> str:
    if sharpness >= 0.2:
        return "áp đảo"
    elif sharpness >= 0.1:
        return "vừa phải"
    return "mong manh"


def _hazard_label(hazard_rate: float) -> str:
    if hazard_rate < 0.3:
        return "thấp"
    elif hazard_rate < 0.7:
        return "trung bình"
    elif hazard_rate < 1.5:
        return "cao"
    return "rất cao"


def _regime_stability(regime_score: float) -> str:
    if regime_score > 0.65:
        return "tăng trưởng"
    elif regime_score > 0.35:
        return "trung tính"
    return "suy yếu"


# ── Narrative builders ────────────────────────────────────────────────


def _narrative_dominance(driver_state: dict) -> str:
    dominant = driver_state.get("dominant", "UNKNOWN")
    confidence = driver_state.get("confidence", 0.0)
    distribution = driver_state.get("distribution", {})

    dom_vi = DRIVER_VI.get(dominant, dominant)
    conf_label = _confidence_label(confidence)
    sharp_label = _sharpness_label(driver_state.get("sharpness", 0.0))

    # Build share breakdown for top drivers
    sorted_drivers = sorted(distribution.items(), key=lambda x: -x[1])[:3]
    shares = []
    for dr, sh in sorted_drivers:
        dr_vi = DRIVER_VI.get(dr, dr)
        shares.append(f"{dr_vi} ({sh:.0%})")

    share_text = ", ".join(shares)

    return f"{dom_vi} đang dẫn dắt thị trường với mức {sharp_label} (độ chắc chắn {conf_label}). Phân bố: {share_text}."


def _narrative_certainty(driver_state: dict) -> str:
    entropy = driver_state.get("entropy", 0.5)
    confidence = driver_state.get("confidence", 0.0)
    conf_label = _confidence_label(confidence)

    if entropy < 0.5 and confidence >= 0.6:
        return f"Thị trường rất rõ ràng. Một lực đang chi phối hoàn toàn (entropy thấp, độ chắc chắn {conf_label})."
    elif entropy < 1.0:
        return "Thị trường tương đối rõ. Có một lực dẫn dắt chính nhưng chưa áp đảo hoàn toàn."
    elif entropy < 1.3:
        return "Thị trường đang mơ hồ. Nhiều lực cạnh tranh, chưa có bên nào chi phối rõ."
    return "Thị trường đang hỗn loạn. Không có lực dẫn dắt rõ ràng, entropy ở mức cao."


def _narrative_system_behavior(
    driver_state: dict,
    regime_status: str,
    regime_score: float,
    hazard_rate: float = 0.0,
) -> str:
    entropy = driver_state.get("entropy", 0.5)
    confidence = driver_state.get("confidence", 0.0)
    regime_vi = REGIME_VI.get(regime_status, "không xác định")
    stability = _regime_stability(regime_score)

    parts = [f"Hệ thống đang ở trạng thái {regime_vi} ({stability})."]

    # Explain adaptive behavior based on driver state
    if entropy > 1.0 and confidence < 0.4:
        parts.append("Thị trường mơ hồ, hệ thống giảm độ nhạy để tránh phản ứng nhiễu.")
    elif entropy < 0.6 and confidence >= 0.6:
        parts.append("Thị trường rõ ràng, hệ thống tăng độ nhạy để bám sát tín hiệu.")

    # Explain hazard modulation
    if hazard_rate > 0:
        haz_label = _hazard_label(hazard_rate)
        if hazard_rate > 1.0:
            parts.append(f"Mức cảnh báo rủi ro ở ngưỡng {haz_label}, hệ thống đang thận trọng hơn.")
        elif hazard_rate < 0.4:
            parts.append(f"Mức cảnh báo rủi ro {haz_label}, hệ thống đang ở trạng thái thoải mái.")
        else:
            parts.append(f"Mức cảnh báo rủi ro {haz_label}.")

    return " ".join(parts)


def _narrative_reason(driver_state: dict, regime_score: float) -> str:
    dominant = driver_state.get("dominant", "UNKNOWN")
    distribution = driver_state.get("distribution", {})

    dom_vi = DRIVER_VI.get(dominant, dominant)

    # Find second-place driver
    sorted_drivers = sorted(distribution.items(), key=lambda x: -x[1])
    if len(sorted_drivers) >= 2:
        second = sorted_drivers[1][0]
        second_vi = DRIVER_VI.get(second, second)
        gap = sorted_drivers[0][1] - sorted_drivers[1][1]

        if gap < 0.05:
            return f"{dom_vi} và {second_vi} đang cạnh tranh sít sao. Chưa có bên nào thực sự chi phối."
        return f"{dom_vi} đang vượt trội so với {second_vi}. Đây là lực dẫn dắt chính của thị trường hiện tại."

    return f"{dom_vi} là lực dẫn dắt duy nhất."


def _narrative_trend(driver_state: dict, regime_status: str) -> str:
    entropy = driver_state.get("entropy", 0.5)

    if regime_status == "TRENDING":
        return "Thị trường đang có xu hướng rõ ràng. Hệ thống ưu tiên bám xu hướng."
    elif regime_status == "CRISIS":
        return "Thị trường đang ở trạng thái rủi ro. Hệ thống ưu tiên bảo toàn."
    else:
        if entropy > 1.0:
            return "Chưa có xu hướng rõ ràng. Thị trường đang dao động không có lực dẫn dắt."
        return "Thị trường đang đi ngang. Hệ thống duy trì trạng thái quan sát, chờ tín hiệu breakout."


# ── Main entry point ─────────────────────────────────────────────────


def explain_snapshot(
    driver_state: dict,
    regime_status: str,
    regime_score: float,
    hazard_rate: float = 0.0,
    include_raw: bool = False,
) -> dict:
    """
    Translate internal control state into Vietnamese narrative.

    Pure function — no side effects, no new computation.
    """
    narrative = {
        "lực_dẫn_dắt": _narrative_dominance(driver_state),
        "mức_độ_chắc_chắn": _narrative_certainty(driver_state),
        "hành_vi_hệ_thống": _narrative_system_behavior(driver_state, regime_status, regime_score, hazard_rate),
        "lý_do": _narrative_reason(driver_state, regime_score),
        "xu_hướng": _narrative_trend(driver_state, regime_status),
    }

    result = {"báo_cáo_hệ_thống": narrative}

    if include_raw:
        result["_raw"] = {
            "driver_state": driver_state,
            "regime_status": regime_status,
            "regime_score": regime_score,
            "hazard_rate": hazard_rate,
        }

    return result
