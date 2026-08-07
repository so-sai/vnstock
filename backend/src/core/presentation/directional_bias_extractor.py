from .models import BiasDriver, DirectionalBiasCode, DirectionalBiasReport

FLOW_SCORE_MIN = 0.0
FLOW_SCORE_MAX = 1.0

STRUCTURE_LCR_HIGH = 35.0
STRUCTURE_LCR_PENALTY = -0.10
STRUCTURE_BDI_DIVERGE = -0.10
STRUCTURE_BDI_REVERSE = 0.05

BREADTH_LOW = 30.0
BREADTH_HIGH = 55.0
BREADTH_PENALTY = -0.20
BREADTH_BONUS = 0.05

REGIME_TRENDING = 0.10
REGIME_CRISIS = -0.25
TRADE_ACTIVE_BONUS = 0.05
TRADE_PROHIBITED_PENALTY = -0.10

SSI_LOW = 0.35
SSI_HIGH = 0.70
SSI_DAMPEN = 0.60
SSI_AMPLIFY = 1.20

TRADE_CAP_PROHIBITED = 0.45
TRADE_CAP_RESTRICTED = 0.55

FRACTURED_CONFIDENCE_THRESHOLD = 0.35


def _flow_impact(flow_bias_score: float) -> float:
    return flow_bias_score * 0.9 - 0.5


def _structure_impact(lcr_pct: float, bdi_signal: str) -> float:
    adj = 0.0
    if lcr_pct > STRUCTURE_LCR_HIGH:
        adj += STRUCTURE_LCR_PENALTY
    if bdi_signal == "PHAN_KY_DUONG":
        adj += STRUCTURE_BDI_DIVERGE
    elif bdi_signal == "PHAN_KY_AM":
        adj += STRUCTURE_BDI_REVERSE
    return adj


def _breadth_impact(breadth_health: float) -> float:
    if breadth_health < BREADTH_LOW:
        return BREADTH_PENALTY
    if breadth_health >= BREADTH_HIGH:
        return BREADTH_BONUS
    return 0.0


def _regime_impact(regime_status: str, trade_state_level: str) -> float:
    adj = 0.0
    if regime_status == "TRENDING":
        adj += REGIME_TRENDING
    elif regime_status == "CRISIS":
        adj += REGIME_CRISIS
    if trade_state_level in ("ACTIVE", "AGGRESSIVE"):
        adj += TRADE_ACTIVE_BONUS
    elif trade_state_level in ("RESTRICTED", "PROHIBITED"):
        adj += TRADE_PROHIBITED_PENALTY
    return adj


def _ssi_multiplier(ssi_score: float) -> float:
    if ssi_score < SSI_LOW:
        return SSI_DAMPEN
    if ssi_score > SSI_HIGH:
        return SSI_AMPLIFY
    return 1.0


def _trade_state_cap(trade_state_level: str, score: float) -> float:
    if trade_state_level == "PROHIBITED":
        return min(score, TRADE_CAP_PROHIBITED)
    if trade_state_level == "RESTRICTED":
        return min(score, TRADE_CAP_RESTRICTED)
    return score


def _is_fractured(drivers: list[BiasDriver], bias_score: float, confidence: float) -> bool:
    non_zero = [d for d in drivers if abs(d.impact) > 0.02 and d.source not in ("SSI", "TRADE_STATE")]
    if len(non_zero) < 2:
        return False
    positive_sum = sum(d.impact for d in non_zero if d.impact > 0)
    negative_sum = sum(abs(d.impact) for d in non_zero if d.impact < 0)
    if positive_sum > 0.05 and negative_sum > 0.05:
        net = positive_sum - negative_sum
        total = positive_sum + negative_sum
        if abs(net) / total < 0.35:
            return True
    return confidence < FRACTURED_CONFIDENCE_THRESHOLD and bias_score < 0.6 and bias_score > 0.15


def _classify_bias(score: float, confidence: float, drivers: list[BiasDriver]) -> DirectionalBiasCode:
    if _is_fractured(drivers, score, confidence):
        return "FRACTURED"
    if score > 0.65:
        return "BULLISH"
    if score > 0.45:
        return "TRANSITIONAL"
    if score > 0.25:
        return "NEUTRAL"
    return "BEARISH"


def _compute_confidence(ssi_score: float, drivers: list[BiasDriver]) -> float:
    non_zero = [d for d in drivers if abs(d.impact) > 0.01 and d.source != "SSI"]
    if len(non_zero) >= 2:
        signs = [1 if d.impact > 0 else -1 for d in non_zero]
        alignment = sum(signs) / len(signs)
        coherence = abs(alignment)
    else:
        coherence = 1.0
    return ssi_score * 0.6 + coherence * 0.4


def _dominant_force(drivers: list[BiasDriver]) -> str:
    candidates = [(abs(d.impact), d.source) for d in drivers if d.source != "SSI"]
    if not candidates:
        return "NONE"
    best = max(candidates, key=lambda x: x[0])
    return best[1] if best[0] > 0.02 else "NONE"


def compute_directional_bias(
    *,
    regime_status: str = "UNKNOWN",
    trade_state_level: str = "PROHIBITED",
    breadth_health: float = 0.0,
    lcr_pct: float = 30.0,
    bdi_signal: str = "CAN_BANG",
    flow_bias_score: float = 0.0,
    flow_label: str = "UNKNOWN",
    ssi_score: float = 0.5,
    dcl_verdict: str = "NO_TRADE",
    dcl_score: float = 0.0,
    compensations_triggered: int = 0,
) -> DirectionalBiasReport:
    flow_adj = _flow_impact(flow_bias_score)
    structure_adj = _structure_impact(lcr_pct, bdi_signal)
    breadth_adj = _breadth_impact(breadth_health)
    regime_adj = _regime_impact(regime_status, trade_state_level)

    raw_score = 0.5 + flow_adj + structure_adj + breadth_adj + regime_adj
    raw_score = max(0.0, min(1.0, raw_score))

    multiplier = _ssi_multiplier(ssi_score)
    ssi_impact = raw_score * (multiplier - 1.0)

    bias_score = raw_score * multiplier
    bias_score = max(0.0, min(1.0, bias_score))

    capped_score = _trade_state_cap(trade_state_level, bias_score)
    trade_cap_impact = capped_score - bias_score

    final_score = round(capped_score, 3)

    drivers = [
        BiasDriver(source="FLOW", impact=round(flow_adj, 4)),
        BiasDriver(source="STRUCTURE", impact=round(structure_adj, 4)),
        BiasDriver(source="BREADTH", impact=round(breadth_adj, 4)),
        BiasDriver(source="REGIME", impact=round(regime_adj, 4)),
        BiasDriver(source="SSI", impact=round(ssi_impact, 4)),
        BiasDriver(source="TRADE_STATE", impact=round(trade_cap_impact, 4)),
    ]

    confidence = _compute_confidence(ssi_score, drivers)
    bias_confidence = round(min(1.0, max(0.0, confidence)), 3)

    bias_code = _classify_bias(final_score, bias_confidence, drivers)
    dominant = _dominant_force(drivers)

    return DirectionalBiasReport(
        bias_code=bias_code,
        bias_strength=final_score,
        bias_confidence=bias_confidence,
        bias_drivers=drivers,
        dominant_force=dominant,
        label_vi="",
        color="",
        summary_vi="",
    )
