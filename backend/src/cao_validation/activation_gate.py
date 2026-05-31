"""CAO Trust Bridge — Activation Gate (Final Promotion Decision).

The switch that determines: Shadow CAO → Live CAO.

Three conditions must ALL be met:
    1. Distribution equivalence — shadow ΔAlpha ≈ live ΔAlpha (KS test)
    2. Confidence threshold — regime-weighted consistency accumulation
    3. Sample sufficiency — minimum samples per regime

This is NOT a boolean rule engine. It is a statistical promotion control system.
"""
import sys
import json
import logging
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

from src.cao_validation.models import (
    PromotionContext, PromotionVerdict, TrustState,
    DistributionTestResult, CABValidationReport,
)
from src.cao_validation.regime_promotion_matrix import get_matrix
from src.cao_validation.trust_accumulator import get_accumulator
from src.cao_validation.shadow_live_comparator import run_per_regime_tests


def evaluate_promotion(
    regime: str,
    trust_state: TrustState,
    distribution_tests: list[DistributionTestResult] = None,
) -> PromotionVerdict:
    """Evaluate whether CAO can be promoted for a specific regime.

    Three gates:
        1. Distribution match (distribution_tests)
        2. Confidence threshold (trust_state.confidence)
        3. Sample sufficiency (trust_state.total_samples)

    Args:
        regime: market regime to evaluate
        trust_state: current trust state for this regime
        distribution_tests: results from shadow-live comparator

    Returns:
        PromotionVerdict with detailed gate-by-gate breakdown
    """
    matrix = get_matrix()
    threshold = matrix.get_threshold(regime)
    max_drift = matrix.get_max_drift(regime)
    confidence_threshold = matrix.get_confidence_threshold(regime)
    if distribution_tests is None:
        distribution_tests = []
    regime_tests = [t for t in distribution_tests if t.regime == regime]
    all_tests = [t for t in distribution_tests if t.regime == "ALL"]
    relevant_tests = regime_tests if regime_tests else all_tests
    dist_match = all(t.equivalent for t in relevant_tests) if relevant_tests else False
    ctx = PromotionContext(
        regime=regime,
        confidence=trust_state.confidence,
        threshold=confidence_threshold,
        consistency=trust_state.mean_consistency,
        required_consistency=threshold.required_consistency,
        sample_count=trust_state.total_samples,
        required_samples=threshold.required_samples,
        distribution_match=dist_match,
        drift_score=trust_state.drift_score,
        max_drift=max_drift,
        strictness=threshold.strictness,
    )
    integrity_ok = trust_state.data_integrity_score >= 0.50
    gates = {
        "distribution_equivalent": ctx.distribution_match,
        "consistency_met": ctx.consistency_met,
        "samples_met": ctx.samples_met,
        "confidence_met": ctx.confidence_met,
        "drift_met": ctx.drift_met,
        "not_frozen": not matrix.is_frozen(regime),
        "integrity_ok": integrity_ok,
    }
    failures = [name for name, met in gates.items() if not met]
    can_promote = all(gates.values())
    if can_promote:
        message = (
            f"PROMOTABLE [{regime}]: all {len(gates)} gates pass. "
            f"confidence={ctx.confidence:.3f}>={ctx.threshold}, "
            f"consistency={ctx.consistency:.3f}>={ctx.required_consistency}, "
            f"samples={ctx.sample_count}>={ctx.required_samples}, "
            f"drift={ctx.drift_score:.3f}<={ctx.max_drift}, "
            f"dis={trust_state.data_integrity_score:.3f}>=0.50"
        )
    else:
        message = (
            f"BLOCKED [{regime}]: {len(failures)}/{len(gates)} gates fail. "
            + ", ".join(failures)
        )
    logger.info("[CAO_GATE] %s", message)
    return PromotionVerdict(
        can_promote=can_promote,
        regime=regime,
        gates=gates,
        failures=failures,
        message=message,
        context=ctx,
    )


def run_full_validation(
    shadow_deltas_by_regime: dict[str, list[float]] = None,
    live_deltas_by_regime: dict[str, list[float]] = None,
) -> CABValidationReport:
    """Run full CAO Trust Bridge validation pipeline.

    0. If no data provided, load from shadow storage
    1. Run distribution equivalence tests (per regime)
    2. Get trust states from accumulator
    3. Evaluate promotion per regime
    4. Build report
    """
    from datetime import datetime
    from src.cao_validation.consistency_engine import compute_batch_consistency
    if shadow_deltas_by_regime is None or live_deltas_by_regime is None:
        shadow_deltas_by_regime, live_deltas_by_regime = _load_deltas_from_storage()
    dist_tests = run_per_regime_tests(shadow_deltas_by_regime, live_deltas_by_regime)
    accumulator = get_accumulator()
    regime_states = list(accumulator.get_state().values())
    if not regime_states:
        regime_states = [
            TrustState(regime=r, total_samples=0, mean_consistency=0.0,
                       consistency_variance=0.0, confidence=0.0,
                       drift_score=0.0, structural_shift=False,
                       distribution_equivalent=False)
            for r in ["TRENDING", "RANGING", "CRISIS"]
        ]
    verdicts = []
    for state in regime_states:
        v = evaluate_promotion(state.regime, state, dist_tests)
        verdicts.append(v)
    promotable = any(v.can_promote for v in verdicts)
    report = CABValidationReport(
        timestamp=datetime.now().isoformat(),
        regime_states=regime_states,
        promotion_verdicts=verdicts,
        consistency_scores=[],
        distribution_tests=dist_tests,
        overall_promotable=promotable,
        summary=_build_summary(verdicts, dist_tests),
    )
    _persist_report(report)
    return report


def _load_deltas_from_storage() -> tuple[dict, dict]:
    """Load ΔAlpha values from shadow and telemetry storage."""
    shadow_by_regime = {r: [] for r in ["TRENDING", "RANGING", "CRISIS"]}
    live_by_regime = {r: [] for r in ["TRENDING", "RANGING", "CRISIS"]}
    try:
        from src.shadow_cao.storage import get_shadow_connection
        with get_shadow_connection() as conn:
            perturbation_rows = conn.execute(
                "SELECT p.decision_id, p.contribution_delta, "
                "COALESCE(d.market_regime, 'RANGING') as regime "
                "FROM shadow_attribution_perturbations p "
                "LEFT JOIN shadow_decision_logs d ON p.decision_id = d.decision_id"
            ).fetchall()
        for r in perturbation_rows:
            regime = r["regime"] if r["regime"] in shadow_by_regime else "RANGING"
            shadow_by_regime[regime].append(r["contribution_delta"])
    except Exception as e:
        logger.warning("[VALIDATION] Could not load shadow perturbations: %s", e)
    try:
        from src.telemetry.storage import get_telemetry_connection
        with get_telemetry_connection() as conn:
            outcome_rows = conn.execute(
                "SELECT vnindex_return, benchmark_return FROM outcome_records"
            ).fetchall()
        live_deltas = [
            round(r["vnindex_return"] - r["benchmark_return"], 4)
            for r in outcome_rows
        ]
        for regime in live_by_regime:
            live_by_regime[regime] = live_deltas
    except Exception as e:
        logger.warning("[VALIDATION] Could not load live outcomes: %s", e)
    return shadow_by_regime, live_by_regime


def _build_summary(
    verdicts: list[PromotionVerdict],
    tests: list[DistributionTestResult],
) -> str:
    promotable = [v for v in verdicts if v.can_promote]
    blocked = [v for v in verdicts if not v.can_promote]
    dist_equiv = sum(1 for t in tests if t.equivalent)
    parts = [
        f"{len(promotable)}/{len(verdicts)} regimes promotable",
        f"{len(blocked)} blocked",
        f"{dist_equiv}/{len(tests)} distribution tests passed" if tests else "0 distribution tests",
    ]
    if promotable:
        regimes = ", ".join(v.regime for v in promotable)
        return f"PROMOTABLE [{regimes}]: " + " | ".join(parts)
    return "BLOCKED: " + " | ".join(parts)


def _persist_report(report: CABValidationReport):
    """Persist the validation report to shadow storage."""
    try:
        from src.shadow_cao.storage import save_belief_value
        save_belief_value(
            "cao_validation_report",
            json.dumps({
                "timestamp": report.timestamp,
                "overall_promotable": report.overall_promotable,
                "summary": report.summary,
                "verdicts": [
                    {"regime": v.regime, "can_promote": v.can_promote,
                     "failures": v.failures, "message": v.message}
                    for v in report.promotion_verdicts
                ],
            }, ensure_ascii=False),
        )
    except Exception as e:
        logger.warning("[VALIDATION] Report persist failed: %s", e)
