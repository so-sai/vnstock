from .models import (
    DirectionalBiasReport,
    DirectionPersistenceReport,
    TransitionStateCode,
    TransitionTriggerReport,
    TransitionTypeCode,
)

_DBE_SIGN_MAP: dict[str, float] = {
    "BULLISH": 1.0,
    "TRANSITIONAL": 1.0,
    "NEUTRAL": 0.0,
    "FRACTURED": 0.0,
    "BEARISH": -1.0,
}

BREWING_BY_FLICKER_TRESHOLD = 0.5
BREWING_BY_STABILITY_THRESHOLD = 0.4
TRANSITION_LOOKBACK = 10
MAX_TTL_HISTORY = 20

_ttl_history: list[dict] = []
_prev_regime: str = ""
_prev_trend_quality: str = ""


def _sign(code: str) -> float:
    return _DBE_SIGN_MAP.get(code, 0.0)


def compute_transition_trigger(
    dpl: DirectionPersistenceReport,
    dbe: DirectionalBiasReport,
    regime_status: str = "UNKNOWN",
) -> TransitionTriggerReport:
    global _prev_regime, _prev_trend_quality

    transition_type: TransitionTypeCode = "NONE"
    trigger_confidence = 0.0

    _sign(dbe.bias_code)
    current_trend = dpl.trend_quality_code
    current_regime = regime_status

    if _prev_regime:
        if _prev_trend_quality == "FLICKERING" and current_trend in ("PERSISTENT", "TRANSITIONAL"):
            transition_type = "FLICKER_TO_TREND"
            trigger_confidence = 0.8 if current_trend == "PERSISTENT" else 0.6

        elif _prev_trend_quality == "PERSISTENT" and current_trend in ("FLICKERING", "TRANSITIONAL"):
            transition_type = "TREND_TO_FLICKER"
            trigger_confidence = 0.85 if current_trend == "FLICKERING" else 0.55

        if _prev_regime != current_regime:
            transition_type = "REGIME_SHIFT"
            trigger_confidence = 0.75

    _prev_regime = current_regime
    _prev_trend_quality = current_trend

    _ttl_history.append(
        {
            "transition_type": transition_type,
            "trigger_confidence": trigger_confidence,
        }
    )
    while len(_ttl_history) > MAX_TTL_HISTORY:
        _ttl_history.pop(0)

    transitions_24h = sum(
        1 for h in _ttl_history[-min(TRANSITION_LOOKBACK, len(_ttl_history)) :] if h["transition_type"] != "NONE"
    )

    if transition_type != "NONE":
        transition_state: TransitionStateCode = "TRIGGERED"
        trigger_confidence = min(1.0, trigger_confidence * (0.5 + 0.5 * dpl.dbe_stability_score))
    elif (
        dpl.flicker_risk_code == "HIGH" and dpl.dbe_stability_score < BREWING_BY_FLICKER_TRESHOLD
    ) or dpl.dbe_stability_score < BREWING_BY_STABILITY_THRESHOLD:
        transition_state = "BREWING"
    else:
        transition_state = "STABLE"

    return TransitionTriggerReport(
        transition_state=transition_state,
        transition_type=transition_type,
        trigger_confidence=round(trigger_confidence, 3),
        transitions_24h=transitions_24h,
    )


def clear_ttl_history() -> None:
    global _prev_regime, _prev_trend_quality
    _ttl_history.clear()
    _prev_regime = ""
    _prev_trend_quality = ""


def peek_ttl_history() -> list[dict]:
    return list(_ttl_history)
