from .models import DecisionView, TimeHorizon, EntropyState
from .state_labels import get_label
from .narrative_matcher import render_best_template


def build_decision_view(
    *,
    risk_state: str = "NEUTRAL",
    flow_state: str = "STABLE",
    truth_status: str = "PROBABILISTIC",
    market_phase: str = "SIDEWAYS",
    entropy_state: str = "HỘI_TỤ",
    signal_alignment: float = 0.5,
    time_horizon: TimeHorizon = "NGẮN_HẠN",
    lci_quality: str | None = None,
) -> DecisionView:
    risk_label = get_label("risk", risk_state)
    flow_label = get_label("liquidity", flow_state)
    epistemic_label = get_label("epistemic", truth_status)
    entropy_label = get_label("entropy", entropy_state)
    phase_label = get_label("market_phase", market_phase)
    lci_label = get_label("lci", lci_quality) if lci_quality else None

    # ── Risk level mapping ──
    if risk_label and risk_label.severity_score >= 0.8:
        risk_level = "CAO"
    elif risk_label and risk_label.severity_score >= 0.5:
        risk_level = "TRUNG_BINH"
    else:
        risk_level = "THẤP"

    # ── Dominant signal descriptor (in Vietnamese) ──
    dominant_parts = []
    if flow_label:
        dominant_parts.append(flow_label.label_vi)
    if phase_label:
        dominant_parts.append(phase_label.label_vi)
    if lci_label:
        dominant_parts.append(lci_label.label_vi)
    dominant_signal_vi = ", ".join(dominant_parts) if dominant_parts else "Chưa xác định"

    # ── Confidence summary ──
    if epistemic_label:
        confidence_summary_vi = epistemic_label.label_vi
    else:
        confidence_summary_vi = "Chưa xác định"

    # ── Primary conflict ──
    primary_conflict_vi = None
    if truth_status == "CONFLICTED" and flow_label and risk_label:
        primary_conflict_vi = (
            f"Dòng tiền {flow_label.label_vi.lower()} "
            f"nhưng rủi ro ở mức {risk_label.label_vi.lower()}."
        )

    # ── Urgency ──
    urgency = max(
        (risk_label.severity_score if risk_label else 0.0) * 0.6,
        (1.0 - signal_alignment) * 0.4,
    )

    # ── Posture ──
    if risk_level in ("CAO",) or urgency >= 0.8:
        posture = "GIẢM_RỦI_RO"
    elif risk_level == "TRUNG_BINH" and truth_status in ("CONFLICTED", "DEGRADED"):
        posture = "QUAN_SÁT"
    elif flow_label and flow_label.action_bias in ("BUY",):
        posture = "TĂNG_TỶ_TRỌNG"
    else:
        posture = "GIỮ_VỊ_THẾ"

    # ── Short explanation (best narrative match) ──
    state_for_narrative = {
        "risk_state": risk_state,
        "flow_state": flow_state,
        "truth_status": truth_status,
        "market_phase": market_phase,
        "entropy_state": entropy_state,
    }
    if lci_quality:
        state_for_narrative["lci_quality"] = lci_quality
    explanation = render_best_template(state_for_narrative, horizon=time_horizon)

    return DecisionView(
        recommended_posture=posture,
        risk_level=risk_level,
        risk_color=risk_label.color if risk_label else "gray",
        dominant_signal_vi=dominant_signal_vi,
        signal_alignment=signal_alignment,
        confidence_summary_vi=confidence_summary_vi,
        primary_conflict_vi=primary_conflict_vi,
        short_explanation_vi=explanation,
        time_horizon=time_horizon,
        decision_urgency=round(urgency, 2),
        entropy_state=entropy_state,
    )
