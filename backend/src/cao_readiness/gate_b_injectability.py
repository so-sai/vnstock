"""
Gate B — Counterfactual Injectability Test
===========================================
For each engine, simulate removing its signal (set to neutral/zero)
and check whether the decision distribution changes measurably.

If removing an engine does not change the decision → it is a decorative signal
with no causal capacity → CAO would learn noise.
"""

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
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
from src.cao_readiness.models import (
    CounterfactualResult,
    GateResult,
    InjectabilityReport,
)
from src.telemetry.storage import get_all_snapshots

CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]
ACTION_CHANGE_THRESHOLD = 0.10


def _parse_engine_scores(snapshot: dict) -> dict:
    raw = snapshot.get("engine_scores")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError, TypeError:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _compute_decision_from_scores(
    scores: dict[str, float],
    weights: dict[str, float] | None = None,
) -> tuple[str, float]:
    if weights is None:
        weights = {
            "regime": 0.25,
            "heat": 0.20,
            "signal": 0.15,
            "dampener": 0.10,
            "memory": 0.08,
            "liquidity": 0.12,
            "sector": 0.05,
            "breakout": 0.05,
        }
    total = 0.0
    weight_sum = 0.0
    for eng, w in weights.items():
        if eng in scores and scores.get(eng) is not None:
            total += w * scores[eng]
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


def _run_single_counterfactual(
    snapshot: dict,
    removed_engine: str,
    weights: dict[str, float] | None = None,
) -> CounterfactualResult | None:
    scores = _parse_engine_scores(snapshot)
    if removed_engine not in scores:
        return None
    snapshot.get("posture", "HOLD")
    float(snapshot.get("confidence", 50)) / 100.0
    baseline_action, baseline_score = _compute_decision_from_scores(scores, weights)
    removed_scores = dict(scores)
    removed_scores[removed_engine] = 0.0
    removed_action, removed_score = _compute_decision_from_scores(removed_scores, weights)
    action_changed = baseline_action != removed_action
    conf_delta = abs(baseline_score - removed_score)
    injectable = action_changed or conf_delta >= ACTION_CHANGE_THRESHOLD
    return CounterfactualResult(
        engine=removed_engine,
        baseline_action=baseline_action,
        baseline_confidence=baseline_score,
        removed_action=removed_action,
        removed_confidence=removed_score,
        action_changed=action_changed,
        confidence_delta=round(conf_delta, 4),
        injectable=injectable,
    )


def run_injectability_test(
    snapshots: list[dict] | None = None,
    engine_names: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> InjectabilityReport:
    if engine_names is None:
        engine_names = CANONICAL_ENGINES
    if snapshots is None:
        try:
            snapshots = get_all_snapshots(limit=200)
        except ImportError, AttributeError, TypeError, KeyError:
            snapshots = []
    if not snapshots:
        return InjectabilityReport(
            passed=False,
            results=[],
            decorative_engines=list(engine_names),
            avg_confidence_delta=0.0,
            verdict="NO_DATA: no snapshots available for counterfactual analysis",
        )
    engine_results: dict[str, list[CounterfactualResult]] = {e: [] for e in engine_names}
    for snap in snapshots:
        scores = _parse_engine_scores(snap)
        if not scores:
            continue
        for eng in engine_names:
            result = _run_single_counterfactual(snap, eng, weights)
            if result is not None:
                engine_results[eng].append(result)
    aggregated = []
    decorative = []
    total_delta = 0.0
    count = 0
    for eng in engine_names:
        results = engine_results.get(eng, [])
        if not results:
            decorative.append(eng)
            continue
        sum(1 for r in results if r.action_changed)
        avg_conf_delta = sum(r.confidence_delta for r in results) / len(results)
        injectable_ratio = sum(1 for r in results if r.injectable) / len(results)
        is_decorative = injectable_ratio < 0.15
        representative = results[len(results) // 2]
        representative.injectable = not is_decorative
        aggregated.append(representative)
        if is_decorative:
            decorative.append(eng)
        total_delta += avg_conf_delta * len(results)
        count += len(results)
    avg_delta = total_delta / count if count > 0 else 0.0
    passed = len(decorative) == 0
    if not passed:
        pct = len(decorative) / len(engine_names) * 100
        if pct <= 25:
            verdict = (
                f"WARN: {len(decorative)}/{len(engine_names)} engines appear decorative "
                f"({', '.join(decorative)}). avg Δconfidence={avg_delta:.4f}. "
                f"CAO should orthogonalize before learning."
            )
            passed = False
        else:
            verdict = (
                f"FAIL: {len(decorative)}/{len(engine_names)} engines are decorative "
                f"({', '.join(decorative)}). avg Δconfidence={avg_delta:.4f}. "
                f"CAO would learn noise. Must fix signal diversity first."
            )
    else:
        verdict = (
            f"All {len(engine_names)} engines pass injectability. avg Δconfidence={avg_delta:.4f} across {count} simulations."
        )
    logger.info(
        "[GATE_B] %s | decorative=%d/%d | avg_delta=%.4f",
        "PASS" if passed else "FAIL",
        len(decorative),
        len(engine_names),
        avg_delta,
    )
    return InjectabilityReport(
        passed=passed,
        results=aggregated,
        decorative_engines=decorative,
        avg_confidence_delta=avg_delta,
        verdict=verdict,
    )


def gate_b_check(
    snapshots: list[dict] | None = None,
) -> GateResult:
    report = run_injectability_test(snapshots)
    if report.passed:
        status = "PASS"
    elif len(report.decorative_engines) <= 2:
        status = "WARN"
    else:
        status = "FAIL"
    return GateResult(
        gate_name="B — Counterfactual Injectability",
        status=status,
        score=1.0 - (len(report.decorative_engines) / 8.0),
        threshold=0.15,
        message=report.verdict,
        details={
            "decorative_engines": report.decorative_engines,
            "avg_confidence_delta": report.avg_confidence_delta,
            "total_simulations": sum(len(r.results) if hasattr(r, "results") else 1 for r in report.results),
        },
    )
