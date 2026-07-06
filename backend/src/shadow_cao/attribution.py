"""Shadow CAO — Dry-run Attribution Engine.

Re-computes engine contribution under ablation conditions.
Same market outcome — perturbed attribution only.
Never writes to production engine_attribution table (separate namespace).
"""
import json
import logging
import math
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
from src.shadow_cao.models import AblationResult, AttributionPerturbation, ShadowAttributionSummary
from src.shadow_cao.storage import (
    get_all_decision_logs,
    initialize_shadow_database,
    save_attribution_perturbation,
)

CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 3 or len(y) < 3 or len(x) != len(y):
        return 0.0
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y))
    sum_x2 = sum(a * a for a in x)
    sum_y2 = sum(b * b for b in y)
    denom = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
    if denom == 0:
        return 0.0
    r = (n * sum_xy - sum_x * sum_y) / denom
    return max(-1.0, min(1.0, r))


def _compute_tp_fp(signal: float, market_return: float, decision_posture: str) -> tuple:
    """Mirrors telemetry/attribution.py _compute_tp_fp."""
    is_bullish = signal >= 0.5
    is_up = market_return > 0
    if is_bullish:
        if is_up:
            tp = min(signal, abs(market_return) * 2)
            fp = 0.0
        else:
            tp = 0.0
            fp = signal * abs(market_return)
    else:
        if not is_up:
            tp = min(1 - signal, abs(market_return) * 2)
            fp = 0.0
        else:
            tp = 0.0
            fp = (1 - signal) * abs(market_return)
    return round(tp, 4), round(fp, 4)


def compute_attribution_perturbation(
    decision_id: str,
    horizon_days: int,
    engine_scores: dict[str, float],
    market_return: float,
    ablation: AblationResult = None,
) -> list[AttributionPerturbation]:
    """Compute baseline + ablated attribution for one decision.

    Market return is REAL (from production telemetry), NEVER synthetic.
    Only engine contribution decomposition is perturbed.
    """
    if not engine_scores:
        return []
    results = []
    for engine in CANONICAL_ENGINES:
        signal = engine_scores.get(engine, 0.0)
        baseline_tp, baseline_fp = _compute_tp_fp(signal, market_return, ablation.baseline_action if ablation else "HOLD")
        baseline_contribution = round(baseline_tp - baseline_fp, 4)
        ablated_signal = 0.0 if (ablation and engine == ablation.engine_removed) else signal
        ablated_tp, ablated_fp = _compute_tp_fp(ablated_signal, market_return, ablation.ablated_action if ablation else "HOLD")
        ablated_contribution = round(ablated_tp - ablated_fp, 4)
        delta = round(baseline_contribution - ablated_contribution, 4)
        results.append(AttributionPerturbation(
            decision_id=decision_id,
            horizon_days=horizon_days,
            engine=engine,
            signal_at_decision=signal,
            baseline_contribution=baseline_contribution,
            ablated_contribution=ablated_contribution,
            contribution_delta=delta,
            baseline_tp=baseline_tp,
            ablated_tp=ablated_tp,
            baseline_fp=baseline_fp,
            ablated_fp=ablated_fp,
        ))
    return results


def run_dry_run_attribution(
    decision_id: str,
    horizon_days: int,
    engine_scores: dict,
    market_return: float,
    ablations: list[AblationResult] = None,
) -> list[ShadowAttributionSummary]:
    """Dry-run attribution for a single decision+horizon.

    Runs in shadow namespace — never writes to production engine_attribution table.
    """
    try:
        initialize_shadow_database()
    except Exception:
        pass
    summaries = []
    for ablation in (ablations or []):
        perturbations = compute_attribution_perturbation(
            decision_id, horizon_days, engine_scores, market_return, ablation
        )
        for p in perturbations:
            save_attribution_perturbation(
                p.decision_id, p.horizon_days, p.engine,
                p.signal_at_decision,
                p.baseline_contribution, p.ablated_contribution, p.contribution_delta,
                p.baseline_tp, p.ablated_tp, p.baseline_fp, p.ablated_fp,
            )
            summaries.append(ShadowAttributionSummary(
                decision_id=p.decision_id,
                horizon_days=p.horizon_days,
                engine=p.engine,
                contribution=p.ablated_contribution,
                true_positive=p.ablated_tp,
                false_positive=p.ablated_fp,
                correlation=0.0,
                accuracy=0.0,
            ))
    return summaries


def batch_dry_run_from_logs() -> int:
    """Batch process all shadow decision logs that have outcomes.

    Runs in shadow namespace only — never touches production tables.
    Returns count of processed decisions.
    """
    from src.telemetry.storage import get_outcomes
    try:
        initialize_shadow_database()
    except Exception:
        pass
    from src.shadow_cao.storage import (
        get_ablations_for_decision,
        save_outcome_log,
    )
    shadow_logs = get_all_decision_logs(limit=500)
    processed = 0
    for log in shadow_logs:
        did = log["decision_id"]
        raw_scores = log["engine_scores"]
        engine_scores = {}
        if raw_scores:
            if isinstance(raw_scores, str):
                try:
                    engine_scores = json.loads(raw_scores)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(raw_scores, dict):
                engine_scores = raw_scores
        if not engine_scores:
            continue
        outcomes = get_outcomes(did)
        if not outcomes:
            continue
        ablations = get_ablations_for_decision(did)
        ablation_objs = []
        for a in ablations:
            ablation_objs.append(AblationResult(
                engine_removed=a["engine_removed"],
                baseline_action=a["baseline_action"],
                baseline_confidence=a["baseline_confidence"],
                ablated_action=a["ablated_action"],
                ablated_confidence=a["ablated_confidence"],
                action_changed=bool(a["action_changed"]),
                confidence_delta=a["confidence_delta"],
                decision_flip=bool(a["decision_flip"]),
            ))
        for outcome in outcomes:
            horizon = outcome["horizon_days"]
            market_return = outcome["vnindex_return"]
            success = bool(outcome["success"])
            save_outcome_log(did, horizon, market_return, success)
            run_dry_run_attribution(
                did, horizon, engine_scores, market_return,
                ablation_objs if ablation_objs else None,
            )
            processed += 1
    return processed
