"""
trader_concierge.py — Action layer for traders (100% Vietnamese).

Sits on top of all engine layers. No new computation — only synthesises
existing signals into actionable trading guidance.

Output answers 3 questions:
  - Hom nay nen lam gi?
  - Co nen vao lenh khong?
  - Rui ro dang tang hay giam?
"""


def _risk_verdict(drift_assessment: dict, hazard_rate: float) -> str:
    drift_status = drift_assessment.get("drift_status", "NONE")
    risk_mismatch = drift_assessment.get("risk_tone_mismatch", False)

    if drift_status in ("HIGH", "CRITICAL"):
        return "Không xác định — nhiễu hệ thống đang ở mức quá cao."
    if hazard_rate > 1.0:
        return "Đang tăng cao. Thị trường tiềm ẩn rủi ro đột biến."
    if risk_mismatch:
        return "Hệ thống đang có sai lệch trong nhận diện rủi ro. Thận trọng."
    if hazard_rate < 0.4:
        return "Ở mức thấp. Thị trường đang ổn định."
    return "Trung bình. Cần quan sát thêm."


def _regime_action(
    regime_status: str,
    driver_state: dict,
    flow_rotation: str | None,
    drift_status: str,
) -> str:
    if drift_status in ("HIGH", "CRITICAL"):
        return "Dừng giao dịch. Hệ thống đang bị nhiễu, tín hiệu không đáng tin cậy."

    dominant = driver_state.get("dominant", "UNKNOWN")
    entropy = driver_state.get("entropy", 0.5)

    if regime_status == "CRISIS":
        return "Đang ở chế độ phòng thủ. Bảo toàn vốn là ưu tiên hàng đầu."

    if regime_status == "TRENDING":
        if dominant == "STRUCTURE" and entropy > 1.3:
            return "Thị trường có xu hướng nhưng cấu trúc còn lỏng lẻo. Cần xác nhận thêm trước khi vào."
        if dominant == "FLOW" or dominant == "BREADTH":
            return "Xu hướng đang có dòng tiền hỗ trợ. Có thể bám theo xu hướng chính."
        return "Thị trường đang có xu hướng rõ. Có thể cân nhắc giao dịch theo hướng chủ đạo."

    # RANGING
    if flow_rotation and "risk_off" in flow_rotation:
        return "Dòng tiền đang rút lui khỏi tài sản rủi ro. Giảm tỷ trọng và chờ."
    if flow_rotation and "concentration" in flow_rotation:
        return "Tiền đang tập trung vào nhóm nhỏ. Chỉ giao dịch cổ phiếu có dòng tiền dẫn dắt."
    if flow_rotation and "speculative" in flow_rotation:
        return "Dòng tiền đang lan tỏa kiểu đầu cơ. Không bám theo — chờ xu hướng thật."
    if flow_rotation and "broad sweep" in flow_rotation:
        return "Thị trường đang duy trì ổn định nhưng thiếu động lực bức phá. Quan sát và chờ."

    return "Thị trường đang đi ngang. Chưa có tín hiệu rõ ràng. Tốt nhất là đứng ngoài và quan sát."


def _enter_verdict(
    regime_status: str,
    drift_status: str,
    ets_score: float,
    flow_rotation: str | None,
    driver_state: dict,
) -> str:
    if drift_status in ("HIGH", "CRITICAL"):
        return "Không. Hệ thống đang mất ổn định."

    if regime_status == "CRISIS":
        return "Không. Thị trường đang rủi ro cao."

    if regime_status == "TRENDING":
        if ets_score >= 0.5:
            return "Có thể. Hệ thống đọc đúng xu hướng và có độ tin cậy khá."
        return "Cân nhắc — vào một phần nhỏ. Tín hiệu xu hướng có nhưng chưa thật rõ."

    # RANGING
    if flow_rotation and "risk_off" in flow_rotation:
        return "Không nên. Dòng tiền đang rút."

    dominant = driver_state.get("dominant", "")
    entropy = driver_state.get("entropy", 0.5)

    if dominant == "STRUCTURE" and entropy > 1.4:
        return "Không. Thị trường đang ở trạng thái nhiễu cấu trúc."
    if entropy > 1.5:
        return "Không. Thị trường mơ hồ, chưa có lực dẫn dắt rõ."

    return "Tốt nhất là quan sát. Chờ thêm tín hiệu từ các phiên tới."


def trading_insight(snapshot: dict) -> dict:
    """Generate a pure-Vietnamese trader action report from a snapshot.

    Args:
        snapshot: A snapshot dict containing at minimum
            'regime_status', 'driver_state', 'drift_assessment',
            'explain_validation', 'flow_bias_score', 'breadth_health'.

    Returns:
        dict with keys:
            hành_động         : str — hôm nay nên làm gì
            vào_lệnh          : str — có nên vào lệnh không
            rủi_ro            : str — rủi ro đang tăng hay giảm
    """
    regime_status = snapshot.get("regime_status", "RANGING")
    driver_state = snapshot.get("driver_state", {})
    drift_assessment = snapshot.get("drift_assessment", {})
    ev = snapshot.get("explain_validation", {})
    hazard_rate = snapshot.get("hazard_rate", 0.0)

    drift_status = drift_assessment.get("drift_status", "NONE")
    flow_rotation = drift_assessment.get("flow_rotation")
    ets_score = ev.get("ets_score", 0.0)

    return {
        "hành_động": _regime_action(regime_status, driver_state, flow_rotation, drift_status),
        "vào_lệnh": _enter_verdict(regime_status, drift_status, ets_score, flow_rotation, driver_state),
        "rủi_ro": _risk_verdict(drift_assessment, hazard_rate),
    }
