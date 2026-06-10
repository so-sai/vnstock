"""Telemetry Layer — Capture, Outcome, Attribution (Sprint 1 + 2 canonical)"""

from src.telemetry.models import (
    DecisionSnapshot, OutcomeRecord, MarketOutcome,
    EngineAttribution, EnginePerformanceView, DecisionAttributionSummary,
)
from src.telemetry.storage import (
    initialize_telemetry_database,
    save_snapshot,
    save_outcome,
    get_snapshot,
    get_pending_decisions,
    get_outcomes,
    get_all_snapshots,
    get_snapshot_stats,
    get_attributions,
    get_attribution_summary,
    get_engine_performance,
    save_market_outcome,
)
from src.telemetry.recorder import record_decision
from src.telemetry.evaluator import evaluate_single, evaluate_pending, run_telemetry_evaluation
from src.telemetry.prediction_registry import (
    log_predictions,
    update_outcomes,
    get_registry_stats,
    get_raw_entries,
    run_registry_update,
)
from src.telemetry.attribution import (
    decompose_attribution,
    update_engine_performance,
    run_attribution_for_outcomes,
    generate_summary_vi,
)

__all__ = [
    "DecisionSnapshot", "OutcomeRecord", "MarketOutcome",
    "EngineAttribution", "EnginePerformanceView", "DecisionAttributionSummary",
    "initialize_telemetry_database",
    "save_snapshot", "save_outcome",
    "get_snapshot", "get_pending_decisions",
    "get_outcomes", "get_all_snapshots", "get_snapshot_stats",
    "get_attributions", "get_attribution_summary", "get_engine_performance",
    "save_market_outcome",
    "record_decision",
    "evaluate_single", "evaluate_pending", "run_telemetry_evaluation",
    "decompose_attribution", "update_engine_performance",
    "run_attribution_for_outcomes", "generate_summary_vi",
]
