"""Shadow CAO — Integration hooks into Telemetry and Decision Tensor.

These hooks are called from the production pipeline but NEVER write to production tables.
Shadow CAO operates in a separate DB namespace (shadow_cao.db).

Hardening guarantees (see hardening.py):
    - Crash-proof: exceptions NEVER propagate to production
    - Async event bus: hooks run non-blocking, never block production
    - Timestamp validation: temporal alignment enforced
    - Replay engine: historical validation available
"""

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

from src.cao_readiness.gate_c_regime_stability import run_regime_stability_test
from src.shadow_cao.attribution import batch_dry_run_from_logs, run_dry_run_attribution
from src.shadow_cao.belief import compute_stability_index, persist_stability_trace, update_engine_profiles
from src.shadow_cao.hardening import ReplayEngine, SafeHookWrapper, ShadowEventBus, safe_hook, start_event_bus
from src.shadow_cao.logger import record_from_snapshot

# ====================================================================
# Initialize event bus at module load (daemon thread, non-blocking)
# ====================================================================
_event_bus: ShadowEventBus = start_event_bus()
_safe_hook: SafeHookWrapper = SafeHookWrapper(_event_bus)


def _register_event_handlers():
    """Register async event handlers on the bus."""
    _event_bus.register("decision_recorded", _async_on_decision_recorded)
    _event_bus.register("outcome_evaluated", _async_on_outcome_evaluated)


def _async_on_decision_recorded(snapshot: dict):
    """Async handler for decision_recorded events (runs in background thread)."""
    _raw_on_decision_recorded(snapshot)


def _async_on_outcome_evaluated(
    decision_id: str,
    horizon_days: int,
    realized_return: float,
    success: bool,
    engine_scores: dict,
):
    """Async handler for outcome_evaluated events (runs in background thread)."""
    _raw_on_outcome_evaluated(decision_id, horizon_days, realized_return, success, engine_scores)


# Register handlers
_register_event_handlers()


# ====================================================================
# RAW HOOKS (wrapped with safe_hook for crash protection)
# ====================================================================


@safe_hook("on_decision_recorded")
def _raw_on_decision_recorded(snapshot: dict) -> bool:
    """Core hook: called after a decision is recorded in telemetry.

    Captures the decision into Shadow CAO (REAL data only).
    Runs engine ablation (decision-space perturbation only).
    """
    from src.shadow_cao.storage import initialize_shadow_database

    initialize_shadow_database()
    entry = record_from_snapshot(snapshot)
    if entry is None:
        logger.warning("[SHADOW_CAO] Failed to capture decision %s", snapshot.get("decision_id", "unknown"))
        return False
    return True


@safe_hook("on_outcome_evaluated")
def _raw_on_outcome_evaluated(
    decision_id: str,
    horizon_days: int,
    realized_return: float,
    success: bool,
    engine_scores: dict,
) -> bool:
    """Core hook: called after an outcome is evaluated in telemetry.

    Runs dry-run attribution (perturbed attribution with REAL market outcome).
    Never writes to production engine_attribution table.
    """
    from src.shadow_cao.storage import (
        get_ablations_for_decision,
        initialize_shadow_database,
        save_outcome_log,
    )

    initialize_shadow_database()
    save_outcome_log(decision_id, horizon_days, realized_return, success)
    ablations_raw = get_ablations_for_decision(decision_id)
    from src.shadow_cao.models import AblationResult

    ablations = (
        [
            AblationResult(
                engine_removed=a["engine_removed"],
                baseline_action=a["baseline_action"],
                baseline_confidence=a["baseline_confidence"],
                ablated_action=a["ablated_action"],
                ablated_confidence=a["ablated_confidence"],
                action_changed=bool(a["action_changed"]),
                confidence_delta=a["confidence_delta"],
                decision_flip=bool(a["decision_flip"]),
            )
            for a in ablations_raw
        ]
        if ablations_raw
        else None
    )
    run_dry_run_attribution(decision_id, horizon_days, engine_scores, realized_return, ablations)
    return True


# ====================================================================
# PUBLIC HOOK API (production-safe entry points)
# ====================================================================


def on_decision_recorded(snapshot: dict) -> bool:
    """ASYNC: called after a decision is recorded in telemetry.

    Posts event to async bus and returns immediately.
    NEVER blocks production pipeline.
    NEVER propagates exceptions.
    """
    _safe_hook.post_decision(snapshot)
    return True


def on_outcome_evaluated(
    decision_id: str,
    horizon_days: int,
    realized_return: float,
    success: bool,
    engine_scores: dict,
) -> bool:
    """ASYNC: called after an outcome is evaluated in telemetry.

    Posts event to async bus and returns immediately.
    NEVER blocks production pipeline.
    NEVER propagates exceptions.
    """
    _safe_hook.post_outcome(
        decision_id,
        horizon_days,
        realized_return,
        success,
        engine_scores,
    )
    return True


def on_attribution_complete(decision_id: str, horizon_days: int):
    """Hook: called after production attribution is complete.

    Currently a no-op — placeholder for future shadow attribution analysis.
    """
    pass


# ====================================================================
# DAILY BATCH (runs in its own context, not via event bus)
# ====================================================================


def daily_shadow_tick() -> dict:
    """Daily shadow CAO processing.

    1. Batch dry-run attribution for all pending decisions
    2. Update engine profiles
    3. Re-check readiness gates
    4. Log stability trace

    This is the main batch pipeline for Shadow CAO.
    """
    logger.info("[SHADOW_CAO] Daily tick started")
    processed = batch_dry_run_from_logs()
    profiles = update_engine_profiles()
    stability = compute_stability_index()
    try:
        regime_report = run_regime_stability_test()
        current_entropy = regime_report.current_entropy
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 bước daily tick lỗi không dừng các bước khác
        logger.warning("[SHADOW_CAO] Regime check failed in daily tick: %s", e)
        current_entropy = None
    persist_stability_trace(stability, current_entropy)
    from src.cao_readiness import run_readiness_check

    try:
        verdict = run_readiness_check(verbose=False)
        logger.info(
            "[SHADOW_CAO] Readiness re-check: overall_pass=%s | A=%s B=%s C=%s",
            verdict.overall_pass,
            verdict.gates[0].status,
            verdict.gates[1].status,
            verdict.gates[2].status,
        )
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 bước daily tick lỗi không dừng các bước khác
        logger.warning("[SHADOW_CAO] Readiness re-check failed: %s", e)
        verdict = None
    summary = {
        "processed": processed,
        "profiles": {e: p.total_decisions for e, p in profiles.items()},
        "stability": stability,
        "entropy": current_entropy,
        "readiness_pass": verdict.overall_pass if verdict else None,
    }
    logger.info("[SHADOW_CAO] Daily tick complete: %s", summary)
    return summary


# ====================================================================
# REPLAY (historical validation, not production path)
# ====================================================================


def run_replay(limit: int = 200) -> list[dict]:
    """Replay historical snapshots through Shadow CAO for validation.

    Safe to run anytime — never affects production or current state.
    """
    engine = ReplayEngine()
    return engine.batch_replay_from_telemetry(limit=limit)
