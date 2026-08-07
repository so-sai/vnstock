from .models import (
    DirectionalBiasReport,
    DirectionPersistenceReport,
    FlickerRiskCode,
    TrendQualityCode,
)

_DBE_SIGN_MAP: dict[str, float] = {
    "BULLISH": 1.0,
    "TRANSITIONAL": 1.0,
    "NEUTRAL": 0.0,
    "FRACTURED": 0.0,
    "BEARISH": -1.0,
}

FLIP_CROSS_WEIGHT = 1.0
FLIP_TRANSITION_WEIGHT = 0.4

ENTROPY_HIGH = 0.35
ENTROPY_MEDIUM = 0.15

STABILITY_HIGH = 0.75
STABILITY_MEDIUM = 0.45

STRENGTH_DECAY = 0.4

W_PERSISTENCE = 0.50
W_STRENGTH = 0.30
W_STABILITY = 0.20

MAX_HISTORY = 20
MIN_WINDOW = 3

_dbe_history: list[dict] = []


def _push_history(dbe: DirectionalBiasReport) -> int:
    _dbe_history.append(
        {
            "bias_code": dbe.bias_code,
            "bias_strength": dbe.bias_strength,
            "dominant_force": dbe.dominant_force,
        }
    )
    while len(_dbe_history) > MAX_HISTORY:
        _dbe_history.pop(0)
    return len(_dbe_history)


def _sign(code: str) -> float:
    return _DBE_SIGN_MAP.get(code, 0.0)


def _weights(window: int) -> list[float]:
    return [STRENGTH_DECAY ** (window - 1 - i) for i in range(window)]


def _weighted_flip_count(signs: list[float]) -> float:
    weighted = 0.0
    for i in range(1, len(signs)):
        p, c = signs[i - 1], signs[i]
        if p * c < 0:
            weighted += FLIP_CROSS_WEIGHT
        elif (p == 0.0 and c != 0.0) or (c == 0.0 and p != 0.0):
            weighted += FLIP_TRANSITION_WEIGHT
    return weighted


def _strength_persistence(strengths: list[float]) -> float:
    n = len(strengths)
    if n < 2:
        return 0.5
    w = _weights(n)
    total_w = sum(w)
    sum(s * wt for s, wt in zip(strengths, w)) / total_w
    recent = strengths[-min(3, n) :]
    recent_avg = sum(recent) / len(recent)
    overall_avg = sum(strengths) / n
    if overall_avg == 0:
        return 1.0 if recent_avg == 0 else 0.5
    deviation = abs(recent_avg - overall_avg) / overall_avg
    return max(0.0, 1.0 - deviation)


def _strength_stability(strengths: list[float]) -> float:
    n = len(strengths)
    if n < 2:
        return 1.0
    mean = sum(strengths) / n
    if mean == 0:
        return 1.0
    variance = sum((s - mean) ** 2 for s in strengths) / n
    cv = math.sqrt(variance) / mean
    return max(0.0, 1.0 - min(1.0, cv))


def _classify_trend(
    persistence_score: float, flips: float, max_possible: float, n: int
) -> tuple[TrendQualityCode, FlickerRiskCode]:
    entropy = flips / max_possible if max_possible > 0 else 0.0

    if entropy > ENTROPY_HIGH:
        return "FLICKERING", "HIGH"
    if persistence_score >= STABILITY_HIGH and entropy <= ENTROPY_MEDIUM:
        return "PERSISTENT", "LOW"
    if persistence_score >= STABILITY_MEDIUM:
        return "TRANSITIONAL", "MEDIUM"
    return "FLICKERING", "HIGH"


import math


def compute_direction_persistence(
    dbe: DirectionalBiasReport,
) -> DirectionPersistenceReport:
    n = _push_history(dbe)

    if n < MIN_WINDOW:
        return DirectionPersistenceReport(
            trend_quality_code="TRANSITIONAL",
            flicker_risk_code="MEDIUM",
            dbe_stability_score=round(dbe.bias_confidence, 3),
            windows_available=n,
        )

    signs = [_sign(h["bias_code"]) for h in _dbe_history]
    strengths = [h["bias_strength"] for h in _dbe_history]

    weighted_flips = _weighted_flip_count(signs)
    max_possible = (n - 1) * FLIP_CROSS_WEIGHT

    persistence = _strength_persistence(strengths)
    stability = _strength_stability(strengths)

    persistence_score = (
        (1.0 - min(1.0, weighted_flips / max_possible)) * W_PERSISTENCE + persistence * W_STRENGTH + stability * W_STABILITY
    )
    persistence_score = round(min(1.0, max(0.0, persistence_score)), 3)

    trend_code, flicker_code = _classify_trend(persistence_score, weighted_flips, max_possible, n)

    return DirectionPersistenceReport(
        trend_quality_code=trend_code,
        flicker_risk_code=flicker_code,
        dbe_stability_score=persistence_score,
        windows_available=n,
    )


def peek_history() -> list[dict]:
    return list(_dbe_history)


def clear_history() -> None:
    _dbe_history.clear()
