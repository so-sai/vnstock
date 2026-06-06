"""
drift_prevention.py — Cognitive drift detection for the control system.

Detects when the narrative (explain_layer) diverges from the driver reality.
Does NOT predict markets. Does NOT recommend trades.

This layer answers:
  "Is the system starting to misunderstand its own market perception?"

Three drift axes:
   1. ETS_DRIFT        — ETS is low or declining over time
   2. DOMINANCE_DRIFT  — narrative consistently picks wrong driver
   3. RISK_TONE_DRIFT  — narrative risk level contradicts hazard/volatility
   4. FLOW_DRIFT       — narrative misses flow rotation signals

KERNEL LAYER — English terms are the kernel's internal language.
UI layer (cognitive_drift_layer.py / cognitive_schema.py) maps to Vietnamese.
narrative_truth_gap is in Vietnamese because it is UI-facing.
"""

from typing import Optional

# ── Vietnamese risk/safety keywords from central schema ────────

from backend.src.core.cognitive_schema import RISK_KEYWORDS, SAFETY_KEYWORDS, DRIVER_VI_LOWER


def _narrative_risk_tone(narrative: dict) -> float:
    """Estimate risk level conveyed by narrative. 0.0 = cautious, 1.0 = risk-on."""
    text = " ".join(str(v) for v in narrative.values()).lower()
    risk_count = sum(1 for kw in RISK_KEYWORDS if kw in text)
    safety_count = sum(1 for kw in SAFETY_KEYWORDS if kw in text)
    total = risk_count + safety_count
    if total == 0:
        return 0.5
    return safety_count / total


def _detect_flow_rotation(driver_state: dict) -> Optional[str]:
    """Detect if money is rotating between drivers.

    Returns a rotation pattern string or None.
    """
    dist = driver_state.get("distribution", {})
    if not dist:
        return None

    flow_w = dist.get("FLOW", 0.0)
    breadth_w = dist.get("BREADTH", 0.0)
    macro_w = dist.get("MACRO", 0.0)
    vol_w = dist.get("VOLATILITY", 0.0)

    # FLOW and BREADTH both high → broad-based flow
    if flow_w > 0.35 and breadth_w > 0.35:
        return "broadsweep: flow+breadth both elevated"
    # FLOW high, BREADTH low → concentration (potential risk-off)
    if flow_w > 0.40 and breadth_w < 0.20:
        if macro_w > 0.30:
            return "risk_off: flow concentrating into safe-haven (macro↑)"
        return "concentration: flow↑ breadth↓ — money narrowing"
    # BREADTH high, FLOW low → speculative rotation
    if breadth_w > 0.40 and flow_w < 0.20:
        return "speculative_spread: breadth↑ flow↓ — money diffusing"
    # Volatility dominant → panic / uncertainty
    if vol_w > 0.35:
        if flow_w > 0.30:
            return "panic_flow: volatility↑ with flow↑ — possible capitulation"
        return "uncertainty: volatility dominating — no clear flow"
    # MACRO elevated → safe-haven rotation
    if macro_w > 0.35 and flow_w < 0.20:
        return "safe_haven_rotation: macro↑ flow↓ — capital leaving equities"

    return None


def _risk_tone_mismatch(
    narrative: dict,
    driver_state: dict,
    hazard_rate: float,
    regime_status: str = "RANGING",
) -> bool:
    """True if narrative risk tone contradicts actual market risk signals.

    Uses regime, entropy, volatility, and hazard as ground truth
    for the appropriate risk tone, then compares with narrative tone.
    """
    tone = _narrative_risk_tone(narrative)
    dist = driver_state.get("distribution", {})
    vol_w = dist.get("VOLATILITY", 0.0)
    entropy = driver_state.get("entropy", 0.5)

    # ── Determine if there is objective reason for risk-off ──────
    danger_signals = (
        vol_w > 0.25
        or hazard_rate > 0.8
        or entropy > 1.3
        or regime_status == "CRISIS"
    )

    # Narrative is risk-on but danger signals are present → false safety
    if tone > 0.6 and danger_signals:
        return True

    # Narrative is risk-off but no danger signals → false alarm
    if tone < 0.4 and not danger_signals:
        return True

    return False


def assess_drift(
    snapshot: dict,
    prev_ets: Optional[float] = None,
) -> dict:
    """Evaluate cognitive drift for a single snapshot.

    Args:
        snapshot: A snapshot dict containing 'narrative_vi', 'driver_state',
                  'explain_validation', and optionally 'hazard_rate'.
        prev_ets: ETS from previous snapshot for trend detection.

    Returns:
        Drift assessment dict with:
            drift_score:       [0, 1] 0 = no drift, 1 = critical drift
            drift_status:      'NONE' | 'LOW' | 'MEDIUM' | 'HIGH'
            drift_sources:     list of detected drift types
            narrative_truth_gap: human-readable description of misalignment
            flow_rotation:     detected flow rotation pattern or None
            risk_tone_mismatch: bool
    """
    narrative = snapshot.get("narrative_vi", {})
    driver_state = snapshot.get("driver_state", {})
    ev = snapshot.get("explain_validation", {})
    hazard_rate = snapshot.get("hazard_rate", 0.0)

    # ── Component scores ────────────────────────────────────────
    ets = ev.get("ets_score", 0.0)
    explained = ev.get("explained_driver", "UNKNOWN")
    correct = ev.get("correct_driver", "UNKNOWN")
    ets_status = ev.get("status", "misaligned")

    # 1. ETS drift
    ets_drift = max(0.0, 1.0 - (ets / 0.5)) if ets < 0.5 else 0.0
    if prev_ets is not None and prev_ets > ets + 0.1:
        ets_drift = min(1.0, ets_drift + 0.3)  # declining ETS is extra bad

    # 2. Dominance drift
    dominance_drift = 0.0
    if ets_status == "misaligned":
        dominance_drift = 0.7
        dominant = driver_state.get("dominant", "UNKNOWN")
        if explained == "UNKNOWN" and dominant != "UNKNOWN":
            dominance_drift = 0.4  # unknown is less bad than wrong

    # 3. Risk tone drift
    regime_status = snapshot.get("regime_status", "RANGING")
    tone_mismatch = _risk_tone_mismatch(narrative, driver_state, hazard_rate, regime_status)
    risk_drift = 0.6 if tone_mismatch else 0.0

    # 4. Flow rotation drift
    rotation = _detect_flow_rotation(driver_state)
    flow_drift = 0.0
    if rotation and "risk_off" in rotation:
        # If risk-off detected, check if narrative mentions risk
        text = " ".join(str(v) for v in narrative.values()).lower()
        mentions_risk = any(kw in text for kw in RISK_KEYWORDS)
        if not mentions_risk:
            flow_drift = 0.5  # narrative misses risk rotation

    drift_score = max(ets_drift, dominance_drift, risk_drift, flow_drift)

    # ── Status classification ────────────────────────────────────
    if drift_score < 0.15:
        status = "NONE"
    elif drift_score < 0.35:
        status = "LOW"
    elif drift_score < 0.60:
        status = "MEDIUM"
    else:
        status = "HIGH"

    # ── Error narrative ──────────────────────────────────────────
    sources = []
    if ets_drift > 0.3:
        sources.append("ETS_DRIFT")
    if dominance_drift > 0.3:
        sources.append("DOMINANCE_MISMATCH")
    if risk_drift > 0.3:
        sources.append("RISK_TONE_MISMATCH")
    if flow_drift > 0.3:
        sources.append("FLOW_ROTATION_BLIND")

    explained_vi = DRIVER_VI_LOWER.get(explained, explained)
    correct_vi = DRIVER_VI_LOWER.get(correct, correct)
    rotation_vi = rotation or "không xác định"

    gap = None
    if dominance_drift > 0.3:
        gap = f"narrative cho rằng {explained_vi} dẫn dắt nhưng thực tế {correct_vi} đang chi phối"
    elif risk_drift > 0.3:
        gap = "narrative đánh giá rủi ro trái ngược với tín hiệu hazard/thị trường"
    elif flow_drift > 0.3:
        gap = f"narrative không nhận diện được luân chuyển dòng tiền: {rotation_vi}"

    return {
        "drift_score": round(drift_score, 4),
        "drift_status": status,
        "drift_sources": sources,
        "narrative_truth_gap": gap,
        "flow_rotation": rotation,
        "risk_tone_mismatch": tone_mismatch,
    }
