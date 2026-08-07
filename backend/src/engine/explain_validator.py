"""
explain_validator.py — Causal alignment between Control Plane and Cognitive Plane.

Measures whether the Vietnamese narrative (explain_layer) accurately reflects
the true driver dominance distribution.

Pure function, no state, no side effects.

This is the "truth meter" for the cognitive plane:
  ETS = weight of the explained driver in the true distribution
  0.0 = explain is completely wrong / hallucinating
  1.0 = explain perfectly matches a fully-dominant driver
"""


# ── Vietnamese keyword → driver key mapping ──────────────────────
# Must match DRIVER_VI in explain_layer.py to ensure bidirectional consistency

VI_KEYWORDS: dict[str, str] = {
    "dòng tiền": "FLOW",
    "độ rộng": "BREADTH",
    "cấu trúc": "STRUCTURE",
    "biến động": "VOLATILITY",
    "đà tăng": "MOMENTUM",
    "vĩ mô": "MACRO",
}

VI_PHRASES: dict[str, str] = {
    "dòng tiền đang dẫn dắt": "FLOW",
    "dòng tiền đang chi phối": "FLOW",
    "dòng tiền đang vượt trội": "FLOW",
    "độ rộng thị trường đang dẫn dắt": "BREADTH",
    "độ rộng thị trường đang chi phối": "BREADTH",
    "độ rộng thị trường đang vượt trội": "BREADTH",
    "cấu trúc thị trường đang dẫn dắt": "STRUCTURE",
    "cấu trúc thị trường đang chi phối": "STRUCTURE",
    "cấu trúc thị trường đang vượt trội": "STRUCTURE",
    "biến động đang dẫn dắt": "VOLATILITY",
    "biến động đang chi phối": "VOLATILITY",
    "đà tăng đang dẫn dắt": "MOMENTUM",
    "đà tăng đang chi phối": "MOMENTUM",
    "yếu tố vĩ mô đang dẫn dắt": "MACRO",
    "yếu tố vĩ mô đang chi phối": "MACRO",
}


def reconstruct_driver_weights(driver_state: dict) -> dict[str, float]:
    """Extract ground-truth driver dominance distribution.

    The `distribution` field in driver_state IS the causal weight
    (softmax-normalized shares of each driver in the control system).
    """
    return dict(driver_state.get("distribution", {}))


def extract_explained_driver(narrative: dict) -> str | None:
    """Parse the Vietnamese narrative to determine which driver is described as dominant.

    Scans the narrative fields for Vietnamese driver keywords.
    Returns the driver key (e.g. 'FLOW') or None if undetermined.
    """
    lực_dẫn_dắt = narrative.get("lực_dẫn_dắt", "")
    lý_do = narrative.get("lý_do", "")
    text = (lực_dẫn_dắt + " " + lý_do).lower()

    # Try exact phrase match first (more specific)
    for phrase, driver in VI_PHRASES.items():
        if phrase in text:
            return driver

    # Fall back to keyword match
    for keyword, driver in VI_KEYWORDS.items():
        if keyword in text:
            return driver

    return None


def alignment_score(
    explained_driver: str | None,
    true_weights: dict[str, float],
) -> float:
    """Explain Truth Score (ETS).

    The weight of the explained driver in the true distribution.

    0.0 → the narrative is talking about a driver that has no influence
    0.5 → the explained driver has moderate influence
    1.0 → the explained driver is fully dominant
    """
    if explained_driver is None or not true_weights:
        return 0.0
    return true_weights.get(explained_driver, 0.0)


def validate_explanation(snapshot: dict) -> dict:
    """Main entry point: validate narrative against ground-truth driver state.

    Args:
        snapshot: A single snapshot dict containing
            'narrative_vi' (from explain_layer) and
            'driver_state' (from driver_normalizer).

    Returns:
        Validation dict with:
            ets_score:      Explain Truth Score [0, 1]
            correct_driver: The true dominant driver key
            explained_driver: Which driver the narrative refers to
            status:         'aligned' | 'misaligned'
    """
    narrative = snapshot.get("narrative_vi", {})
    driver_state = snapshot.get("driver_state", {})

    true_weights = reconstruct_driver_weights(driver_state)
    true_dominant = driver_state.get("dominant", "UNKNOWN")
    explained_driver = extract_explained_driver(narrative)
    ets = alignment_score(explained_driver, true_weights)

    is_aligned = explained_driver == true_dominant

    return {
        "ets_score": round(ets, 4),
        "correct_driver": true_dominant,
        "explained_driver": explained_driver or "UNKNOWN",
        "status": "aligned" if is_aligned else "misaligned",
    }
