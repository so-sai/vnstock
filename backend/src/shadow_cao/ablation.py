"""Shadow CAO — Engine Ablation Simulator.

ABLATION = remove one engine's signal, recompute decision distribution.
This is NOT synthetic data generation. Market outcome is NEVER perturbed.

Principle:
    Data is fixed (market truth is frozen)
    Only attribution is perturbed
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.shadow_cao.models import AblationResult, ShadowDecisionLog

CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]

DEFAULT_WEIGHTS = {
    "regime": 0.25, "heat": 0.20, "signal": 0.15,
    "dampener": 0.10, "memory": 0.08, "liquidity": 0.12,
    "sector": 0.05, "breakout": 0.05,
}

ALL_ACTIONS = ["ENTER", "SCALE_IN", "HOLD", "REDUCE", "EXIT", "STAND_DOWN"]


def _compute_action_and_confidence(
    scores: dict[str, float],
    weights: dict[str, float] = None,
) -> tuple[str, float]:
    """Compute action and confidence from weighted engine scores.

    This mirrors decision_tensor_v2 scoring logic but is a standalone
    deterministic function — no market state needed, only engine scores.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS
    total = 0.0
    weight_sum = 0.0
    for eng, w in weights.items():
        val = scores.get(eng)
        if val is not None and isinstance(val, (int, float)):
            total += w * val
            weight_sum += w
    if weight_sum == 0:
        return "HOLD", 0.0
    avg = total / weight_sum
    if avg >= 0.70:
        action = "ENTER"
    elif avg >= 0.55:
        action = "SCALE_IN"
    elif avg >= 0.40:
        action = "HOLD"
    elif avg >= 0.20:
        action = "REDUCE"
    elif avg > 0.0:
        action = "EXIT"
    else:
        action = "STAND_DOWN"
    return action, round(avg, 4)


def run_decision_ablation(
    entry: ShadowDecisionLog,
    weights: dict[str, float] = None,
    engine_names: list[str] = None,
) -> list[AblationResult]:
    """Run ablation on a single decision.

    For each engine: zero its score, recompute action + confidence.
    Compare with baseline to determine causal capacity.

    This perturb the DECISION SPACE only — market outcome is NEVER changed.
    """
    if engine_names is None:
        engine_names = CANONICAL_ENGINES
    if weights is None:
        weights = entry.decision_weights or DEFAULT_WEIGHTS
    scores = dict(entry.engine_scores)
    if not scores:
        logger.warning("[SHADOW_CAO] No engine scores for %s, skipping ablation", entry.decision_id)
        return []
    baseline_action, baseline_conf = _compute_action_and_confidence(scores, weights)
    results = []
    for engine in engine_names:
        if engine not in scores:
            continue
        ablated_scores = dict(scores)
        ablated_scores[engine] = 0.0
        ablated_action, ablated_conf = _compute_action_and_confidence(ablated_scores, weights)
        action_changed = baseline_action != ablated_action
        confidence_delta = round(abs(baseline_conf - ablated_conf), 4)
        decision_flip = _is_decision_flip(baseline_action, ablated_action)
        results.append(AblationResult(
            engine_removed=engine,
            baseline_action=baseline_action,
            baseline_confidence=baseline_conf,
            ablated_action=ablated_action,
            ablated_confidence=ablated_conf,
            action_changed=action_changed,
            confidence_delta=confidence_delta,
            decision_flip=decision_flip,
        ))
    return results


def _is_decision_flip(baseline: str, ablated: str) -> bool:
    """A 'flip' is a binary change: ENTER/SCALE_IN ↔ EXIT/STAND_DOWN or vice versa."""
    offensive = {"ENTER", "SCALE_IN"}
    defensive = {"EXIT", "STAND_DOWN"}
    return (baseline in offensive and ablated in defensive) or (
        baseline in defensive and ablated in offensive
    )


def run_batch_ablation(
    entries: list[ShadowDecisionLog],
    weights: dict[str, float] = None,
) -> dict[str, list[AblationResult]]:
    """Run ablation on multiple decisions. Returns {decision_id: [ablation_results]}."""
    results = {}
    for entry in entries:
        ablated = run_decision_ablation(entry, weights)
        if ablated:
            results[entry.decision_id] = ablated
    return results
