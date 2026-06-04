
from .models import SSIReport, AxisScore, SSILevel


def _score_consistency(regime_status: str, regime_score: float,
                       breadth_health: float, bdi_signal: str) -> tuple[float, str]:
    contradictions = 0
    if regime_status == "TRENDING" and breadth_health < 30:
        contradictions += 1
    if regime_status == "TRENDING" and bdi_signal == "PHAN_KY_AM":
        contradictions += 1
    if regime_status == "CRISIS" and breadth_health > 50:
        contradictions += 1
    if bdi_signal == "PHAN_KY_DUONG" and breadth_health < 35:
        contradictions += 1

    if contradictions >= 2:
        return 0.2, "Nhiều mâu thuẫn nội tại — regime, breadth và cấu trúc không đồng pha"
    elif contradictions == 1:
        return 0.5, "Một phần tín hiệu chưa khớp — cần theo dõi thêm"
    return 0.9, "Các tín hiệu nội tại nhất quán — regime, breadth và cấu trúc đồng pha"


def _score_breadth_confirmation(breadth_health: float, lcr_pct: float) -> tuple[float, str]:
    if breadth_health >= 55 and lcr_pct <= 25:
        return 0.9, "Độ rộng tốt và thanh khoản phân tán — thị trường lan tỏa thực"
    elif breadth_health >= 40 and lcr_pct <= 35:
        return 0.6, "Độ rộng trung bình, tập trung vừa phải — chấp nhận được"
    elif breadth_health >= 25:
        return 0.35, f"Độ rộng yếu (b={breadth_health:.0f}%) với LCR={lcr_pct:.0f}% — khả năng index giả tạo"
    return 0.1, f"Độ rộng rất yếu (b={breadth_health:.0f}%) và tập trung cao (LCR={lcr_pct:.0f}%) — không đại diện thị trường"


def _score_drift_alignment(drift_label: str, regime_status: str) -> tuple[float, str]:
    conflict_patterns = [
        ("EXPANSION", "STRUCTURAL"),
        ("TRENDING", "STRUCTURAL"),
        ("TRENDING", "TRANSIENT"),
        ("RANGING", "STRUCTURAL"),
        ("CRISIS", "NONE"),
    ]
    for reg, drf in conflict_patterns:
        if regime_status == reg and drf in drift_label.upper():
            return 0.15, f"MSM={regime_status} nhưng drift='{drift_label}' — state và drift mâu thuẫn"

    if "NONE" in drift_label.upper():
        return 0.85, "Drift ở mức nền — state và drift nhất quán"
    if "NOISE" in drift_label.upper():
        return 0.6, "Drift nhẹ — state tạm chấp nhận"
    return 0.4, f"Drift đang hoạt động ('{drift_label}') — state cần kiểm chứng thêm"


def _score_flow_stability(flow_status: str, flow_velocity: float,
                          rotation_velocity: float) -> tuple[float, str]:
    if flow_status in ("MỞ_RỘNG", "DUY_TRÌ") and rotation_velocity < 0.5:
        return 0.85, "Dòng tiền ổn định, không xoay vòng đột ngột"
    elif flow_status == "PHÂN_HÓA" and rotation_velocity >= 0.5:
        return 0.35, "Dòng tiền đang xoay vòng — phân hóa và chưa ổn định"
    elif flow_status == "THU_HẸP":
        return 0.15, "Dòng tiền thu hẹp — thanh khoản giảm, thị trường yếu"
    return 0.5, "Dòng tiền trung tính — chưa rõ hướng"


def compute_ssi(
    *,
    regime_status: str = "UNKNOWN",
    regime_score: float = 0.0,
    breadth_health: float = 0.0,
    lcr_pct: float = 30.0,
    bdi_signal: str = "CAN_BANG",
    drift_label: str = "NONE",
    flow_status: str = "UNKNOWN",
    flow_velocity: float = 0.0,
    rotation_velocity: float = 0.0,
) -> SSIReport:
    w_consistency = 0.25
    w_breadth = 0.25
    w_drift = 0.25
    w_flow = 0.25

    s_consistency, d_consistency = _score_consistency(regime_status, regime_score, breadth_health, bdi_signal)
    s_breadth, d_breadth = _score_breadth_confirmation(breadth_health, lcr_pct)
    s_drift, d_drift = _score_drift_alignment(drift_label, regime_status)
    s_flow, d_flow = _score_flow_stability(flow_status, flow_velocity, rotation_velocity)

    total = (
        w_consistency * s_consistency
        + w_breadth * s_breadth
        + w_drift * s_drift
        + w_flow * s_flow
    )

    if total >= 0.65:
        level: SSILevel = "HIGH"
        label = "Ổn định"
        color = "green"
        summary = "State hiện tại có độ tin cậy cao — các tín hiệu đồng thuận"
    elif total >= 0.35:
        level = "MEDIUM"
        label = "Đang chuyển pha"
        color = "yellow"
        summary = "State đang trong quá trình chuyển pha — cần quan sát thêm"
    else:
        level = "LOW"
        label = "Nhiễu / giả"
        color = "red"
        summary = "State hiện tại có độ tin cậy thấp — bề mặt và cấu trúc mâu thuẫn"

    return SSIReport(
        level=level,
        score=round(total, 3),
        label_vi=label,
        color=color,
        summary_vi=summary,
        state_consistency=AxisScore(score=round(s_consistency, 3), label_vi="Nhất quán nội tại", detail_vi=d_consistency),
        breadth_confirmation=AxisScore(score=round(s_breadth, 3), label_vi="Độ lan tỏa", detail_vi=d_breadth),
        drift_alignment=AxisScore(score=round(s_drift, 3), label_vi="Khớp drift", detail_vi=d_drift),
        flow_stability=AxisScore(score=round(s_flow, 3), label_vi="Ổn định dòng tiền", detail_vi=d_flow),
    )
