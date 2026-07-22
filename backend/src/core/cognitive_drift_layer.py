"""
cognitive_drift_layer.py — Drift UI Layer (100% Vietnamese)

Sits on top of engine/drift_prevention.py (kernel, English).
Transforms kernel drift output → pure Vietnamese UI schema.

Architecture:
    assess_drift() [kernel/EN] → cognitive_drift_layer.py [UI/VN]

Usage:
    from core.cognitive_drift_layer import assess_drift_vi

    result_vi = assess_drift_vi(snapshot, prev_ets)
    # result_vi == {
    #     "đánh_giá_drift": {
    #         "mức_độ_drift": "thấp",
    #         "nguồn_drift": ["sai lệch độ chính xác giải thích"],
    #         "khoảng_cách_nhận_thức": "...",
    #         "luân_chuyển_dòng_tiền": "...",
    #         "sai_lệch_rủi_ro": False,
    #     }
    # }
"""

from typing import Optional

from backend.src.core.cognitive_schema import drift_to_vi
from backend.src.engine.drift_prevention import assess_drift


def assess_drift_vi(
    snapshot: dict,
    prev_ets: Optional[float] = None,
) -> dict:
    """Evaluate cognitive drift and return pure Vietnamese UI schema.

    Args:
        snapshot: A snapshot dict containing 'narrative_vi', 'driver_state',
                  'explain_validation', and optionally 'hazard_rate'.
        prev_ets: ETS from previous snapshot for trend detection.

    Returns:
        Pure Vietnamese UI schema:
            {
                "đánh_giá_drift": {
                    "mức_độ_drift": str,
                    "nguồn_drift": list[str],
                    "khoảng_cách_nhận_thức": str | None,
                    "luân_chuyển_dòng_tiền": str | None,
                    "sai_lệch_rủi_ro": bool,
                }
            }
    """
    raw = assess_drift(snapshot, prev_ets)
    return drift_to_vi(raw)


def build_drift_narrative(drift_vi: dict) -> str:
    """Build a concise Vietnamese sentence from drift assessment.

    Args:
        drift_vi: The pure Vietnamese drift dict from assess_drift_vi().

    Returns:
        One-sentence Vietnamese summary of drift state.
    """
    assessment = drift_vi.get("đánh_giá_drift", {})
    level = assessment.get("mức_độ_drift", "không xác định")
    sources = assessment.get("nguồn_drift", [])
    rotation = assessment.get("luân_chuyển_dòng_tiền")
    risk_mismatch = assessment.get("sai_lệch_rủi_ro", False)

    parts = [f"Mức độ drift: {level}."]

    if sources:
        parts.append(f"Nguồn: {', '.join(sources)}.")
    if rotation:
        parts.append(f"Luân chuyển dòng tiền: {rotation}.")
    if risk_mismatch:
        parts.append("Có sai lệch nhận thức rủi ro.")
    else:
        parts.append("Nhận thức rủi ro phù hợp.")

    return " ".join(parts)
