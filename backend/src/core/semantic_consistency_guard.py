"""
semantic_consistency_guard.py — Constraint validator, not a layer.

Checks 3 inconsistency types between:
  - ontology (cognitive_schema.py — "nghĩa hệ thống")
  - runtime outputs (narrative_vi, drift, driver_state — "nghĩa hiển thị")

Architecture:
  This is a CHECKPOINT, not an engine, layer, or module.
  It has no state, no side effects, no computation of new signals.

  3 checks:
    1. LABEL_MISMATCH    — ontology says X, narrative says Y
    2. CAUSAL_MISMATCH   — kernel says driver A, narrative says B
    3. STRUCTURAL_MISMATCH — drift level contradicts narrative tone
"""

from __future__ import annotations

from typing import Optional

from core.cognitive_schema import (
    DRIFT_STATUS_VI,
    DRIVER_VI_LOWER,
    FLOW_ROTATION_VI,
    RISK_KEYWORDS,
    SAFETY_KEYWORDS,
    VI_KEYWORDS,
    VI_PHRASES,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _extract_narrative_driver(narrative: dict) -> Optional[str]:
    """Extract which driver the narrative claims is dominant.

    Reuses the same keyword logic as explain_validator.
    Returns driver key (e.g. "FLOW") or None.
    """
    lực_dẫn_dắt = narrative.get("lực_dẫn_dắt", "")
    lý_do = narrative.get("lý_do", "")
    text = (lực_dẫn_dắt + " " + lý_do).lower()

    for phrase, driver in VI_PHRASES.items():
        if phrase in text:
            return driver

    for keyword, driver in VI_KEYWORDS.items():
        if keyword in text:
            return driver

    return None


def _narrative_tone(narrative: dict) -> float:
    """Estimate narrative risk tone. 0.0 = cautious, 1.0 = risk-on.

    Mirrors drift_prevention._narrative_risk_tone logic for comparison.
    """
    text = " ".join(str(v) for v in narrative.values()).lower()
    risk_count = sum(1 for kw in RISK_KEYWORDS if kw in text)
    safety_count = sum(1 for kw in SAFETY_KEYWORDS if kw in text)
    total = risk_count + safety_count
    if total == 0:
        return 0.5
    return safety_count / total


def _expected_tone_for_drift(drift_status: str) -> str:
    """What tone does the ontology expect given this drift level?"""
    if drift_status in ("HIGH", "MEDIUM"):
        return "cautious"  # system should be worried
    return "neutral_or_positive"  # system can be calm


def _expected_tone_for_regime(regime_status: str) -> str:
    """What tone does the ontology expect given this regime?"""
    if regime_status == "CRISIS":
        return "cautious"
    if regime_status == "TRENDING":
        return "neutral_or_positive"
    return "neutral_or_positive"


# ── Check functions ─────────────────────────────────────────────────────────


def _check_label_mismatch(
    narrative: dict,
    drift_result: dict,
) -> list[dict]:
    """Check 1: Does the narrative contradict the ontology's expected labels?

    Specifically: if flow_rotation maps to a specific VN description,
    does the narrative text contradict it?
    """
    violations = []

    rotation = (drift_result or {}).get("flow_rotation")
    if rotation and rotation in FLOW_ROTATION_VI:
        expected_meaning = FLOW_ROTATION_VI[rotation]

        # Broad sweep → narrative should not mention concentration
        if "lan tỏa" in expected_meaning:
            text = " ".join(str(v) for v in narrative.values()).lower()
            contradict = ["tập trung", "thu hẹp", "co cụm"]
            found = [w for w in contradict if w in text]
            if found:
                violations.append({
                    "type": "LABEL_MISMATCH",
                    "severity": 0.3,
                    "detail": (
                        f"flow_rotation ontology='{expected_meaning}' "
                        f"nhưng narrative chứa từ trái nghĩa: {', '.join(found)}"
                    ),
                })

        # Risk-off → narrative should mention risk
        if "rủi ro" in expected_meaning or "tháo chạy" in expected_meaning:
            text = " ".join(str(v) for v in narrative.values()).lower()
            if not any(kw in text for kw in RISK_KEYWORDS):
                violations.append({
                    "type": "LABEL_MISMATCH",
                    "severity": 0.4,
                    "detail": (
                        f"flow_rotation ontology='{expected_meaning}' "
                        f"narrative không chứa từ khóa rủi ro nào"
                    ),
                })

    return violations


def _check_causal_mismatch(
    narrative: dict,
    driver_state: dict,
) -> list[dict]:
    """Check 2: Does the narrative's claimed driver match the kernel's dominant driver?

    This is a runtime re-check of explain_validator alignment.
    """
    violations = []

    explained = _extract_narrative_driver(narrative)
    dominant = (driver_state or {}).get("dominant", "UNKNOWN")

    if explained is not None and explained != dominant:
        explained_vi = DRIVER_VI_LOWER.get(explained, explained)
        dominant_vi = DRIVER_VI_LOWER.get(dominant, dominant)
        violations.append({
            "type": "CAUSAL_MISMATCH",
            "severity": 0.6,
            "detail": (
                f"narrative nói {explained_vi} dẫn dắt "
                f"nhưng kernel xác định {dominant_vi} đang chi phối"
            ),
        })

    return violations


def _check_structural_mismatch(
    narrative: dict,
    drift_result: dict,
    regime_status: str,
) -> list[dict]:
    """Check 3: Does the drift level semantically match the narrative tone?

    - drift HIGH + narrative calm       → structural mismatch (false calm)
    - drift LOW + narrative panicked    → structural mismatch (false alarm)
    - CRISIS regime + narrative calm    → structural mismatch
    """
    violations = []

    tone = _narrative_tone(narrative)
    drift_status = (drift_result or {}).get("drift_status", "NONE")
    expected_tone_code = _expected_tone_for_drift(drift_status)
    regime_tone_code = _expected_tone_for_regime(regime_status)

    tone_label = "cautious" if tone < 0.4 else "risk_on"

    # High drift → narrative should be cautious
    if expected_tone_code == "cautious" and tone_label != "cautious":
        violations.append({
            "type": "STRUCTURAL_MISMATCH",
            "severity": 0.5,
            "detail": (
                f"drift_status={drift_status} ({DRIFT_STATUS_VI.get(drift_status, drift_status)}) "
                f"nhưng narrative ở trạng thái 'risk-on' (tone={tone:.2f}) — "
                f"kỳ vọng thận trọng hơn"
            ),
        })

    # Low drift → narrative should not be alarmist
    if expected_tone_code != "cautious" and tone_label == "cautious":
        violations.append({
            "type": "STRUCTURAL_MISMATCH",
            "severity": 0.4,
            "detail": (
                f"drift_status={drift_status} ({DRIFT_STATUS_VI.get(drift_status, drift_status)}) "
                f"nhưng narrative ở trạng thái 'thận trọng' (tone={tone:.2f}) — "
                f"có thể đang báo động giả"
            ),
        })

    # CRISIS regime → narrative MUST be cautious
    if regime_tone_code == "cautious" and tone_label != "cautious":
        violations.append({
            "type": "STRUCTURAL_MISMATCH",
            "severity": 0.7,
            "detail": (
                f"regime=CRISIS nhưng narrative ở trạng thái 'risk-on' (tone={tone:.2f}) "
                f"— sai lệch nhận thức rủi ro nghiêm trọng"
            ),
        })

    return violations


# ── Main entry point ────────────────────────────────────────────────────────


def check_semantic_consistency(snapshot: dict) -> dict:
    """Main checkpoint: evaluate semantic consistency of a snapshot.

    Args:
        snapshot: dict with keys:
            - narrative_vi:      from explain_layer
            - driver_state:      from driver_normalizer
            - drift_result:      from drift_prevention.assess_drift() or None
            - regime_status:     str or None

    Returns:
        {
            "semantic_consistency_score": 0.0 - 1.0,
            "violations": [],
            "drift_in_meaning": False,
            "mismatched_concepts": [],
        }

    Score:
        1.0 = perfectly consistent
        0.0 = completely inconsistent

    Violation severities:
        0.3 = label mismatch (cosmetic)
        0.4 = structural mismatch (moderate)
        0.5 = structural mismatch (significant)
        0.6 = causal mismatch (serious)
        0.7 = structural mismatch (critical)
    """
    narrative = snapshot.get("narrative_vi", {})
    driver_state = snapshot.get("driver_state", {})
    drift_result = snapshot.get("drift_result")
    regime_status = snapshot.get("regime_status", "UNKNOWN")

    violations: list[dict] = []

    violations.extend(_check_label_mismatch(narrative, drift_result))
    violations.extend(_check_causal_mismatch(narrative, driver_state))
    violations.extend(_check_structural_mismatch(narrative, drift_result, regime_status))

    # ── Score ────────────────────────────────────────────────────────
    if not violations:
        score = 1.0
    else:
        total_severity = sum(v["severity"] for v in violations)
        score = max(0.0, 1.0 - total_severity)

    mismatched = sorted({v["type"] for v in violations})

    # drift_in_meaning chỉ báo động khi cấu trúc sai nghiêm trọng hoặc nhiều sai đồng thời
    has_causal = any(v["severity"] >= 0.6 and v["type"] == "CAUSAL_MISMATCH" for v in violations)
    has_critical_structural = any(
        v["severity"] >= 0.7 and v["type"] == "STRUCTURAL_MISMATCH" for v in violations
    )
    multiple_structural = (
        sum(1 for v in violations if v["type"] == "STRUCTURAL_MISMATCH") >= 2
    )
    drift_in_meaning = has_causal or (has_critical_structural and multiple_structural)

    return {
        "semantic_consistency_score": round(score, 4),
        "violations": violations,
        "drift_in_meaning": drift_in_meaning,
        "mismatched_concepts": mismatched,
    }
