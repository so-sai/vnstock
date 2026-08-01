"""
driver_normalizer.py — Thin aggregation layer.

Projects existing engine outputs into unified Driver State Vector D_t.

This is NOT an engine. It is a LENS + NORMALIZER.
No state, no memory, no prediction — pure function over engine outputs.

1:1 engine → driver mapping:
  BREADTH    ← breadth_engine.health_score_ma20 (0-100)
  FLOW       ← capital_flow_forecast.flow_bias_score (0-1)
  STRUCTURE  ← market_structure.lcr_pct (0-100, inverted)
  VOLATILITY ← regime_engine.details.v_score (0.2-1.0, inverted)
  MOMENTUM   ← regime_engine.details.t_score (0/0.6/1.0)
  MACRO      ← gold_regime.velocity (0-1)
"""

from dataclasses import dataclass

import numpy as np

DRIVER_KEYS = ["BREADTH", "FLOW", "STRUCTURE", "VOLATILITY", "MOMENTUM", "MACRO"]


@dataclass
class DriverState:
    """Projection of existing engine signals into unified control space.

    Fields:
        scores: Raw normalized scores [0,1] per driver (what each engine says).
        distribution: Softmax-normalized dominance distribution (who is in control).
        dominant: Driver with highest distribution share (argmax).
        confidence: Distribution share of dominant driver.
        entropy: Shannon entropy of distribution — low = one driver owns the system.
        sharpness: Gap between dominant and second-place driver.
    """

    scores: dict[str, float]
    distribution: dict[str, float]
    dominant: str
    confidence: float
    entropy: float
    sharpness: float


def compute_driver_scores(
    breadth_health: float | None = None,
    flow_bias: float | None = None,
    lcr_pct: float | None = None,
    v_score: float | None = None,
    t_score: float | None = None,
    macro_signal: float | None = None,
) -> dict[str, float]:
    """Map raw engine outputs to normalized driver scores [0,1].

    Each parameter is the raw output from its respective engine.
    Returns a dict of {DRIVER_KEY: normalized_score} for available signals.
    """
    scores = {}

    if breadth_health is not None:
        scores["BREADTH"] = np.clip(breadth_health / 100.0, 0.0, 1.0)

    if flow_bias is not None:
        scores["FLOW"] = np.clip(flow_bias, 0.0, 1.0)

    if lcr_pct is not None:
        # High LCR = tight leadership = fragile structure = low driver score
        scores["STRUCTURE"] = np.clip(1.0 - lcr_pct / 100.0, 0.0, 1.0)

    if v_score is not None:
        # v_score: 0.2 = high vol, 1.0 = calm. Invert: high vol = high driver score
        scores["VOLATILITY"] = np.clip(1.0 - v_score, 0.0, 1.0)

    if t_score is not None:
        scores["MOMENTUM"] = np.clip(t_score, 0.0, 1.0)

    if macro_signal is not None:
        scores["MACRO"] = np.clip(macro_signal, 0.0, 1.0)

    return scores


def normalize_drivers(scores: dict[str, float]) -> DriverState:
    """Project raw scores into DriverState via softmax.

    Pure function — no side effects, no state.
    """
    if not scores:
        return DriverState(
            scores={},
            distribution={},
            dominant="UNKNOWN",
            confidence=0.0,
            entropy=0.0,
            sharpness=0.0,
        )

    keys = list(scores.keys())
    values = np.array([scores[k] for k in keys], dtype=np.float64)

    # Softmax with numerical stability
    exp_v = np.exp(values - np.max(values))
    dist = exp_v / exp_v.sum()

    distribution = {k: float(v) for k, v in zip(keys, dist)}

    sorted_idx = np.argsort(dist)[::-1]
    dominant = keys[int(sorted_idx[0])]
    confidence = float(dist[sorted_idx[0]])
    sharpness = float(dist[sorted_idx[0]] - dist[sorted_idx[1]]) if len(dist) > 1 else 1.0

    # Shannon entropy
    p = dist[dist > 0]
    entropy = float(-np.sum(p * np.log2(p))) if len(p) > 0 else 0.0

    return DriverState(
        scores=scores,
        distribution=distribution,
        dominant=dominant,
        confidence=confidence,
        entropy=entropy,
        sharpness=sharpness,
    )


def driver_state_from_engine_outputs(
    breadth_health: float | None = None,
    flow_bias: float | None = None,
    lcr_pct: float | None = None,
    v_score: float | None = None,
    t_score: float | None = None,
    macro_signal: float | None = None,
) -> DriverState:
    """One-shot: engine outputs → DriverState.

    This is the main entry point for HSR snapshot patching.
    Accepts whatever signals are available (optional), normalizes, and returns.
    """
    scores = compute_driver_scores(
        breadth_health=breadth_health,
        flow_bias=flow_bias,
        lcr_pct=lcr_pct,
        v_score=v_score,
        t_score=t_score,
        macro_signal=macro_signal,
    )
    return normalize_drivers(scores)
