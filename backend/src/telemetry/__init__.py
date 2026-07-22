"""Telemetry Layer — Capture, Outcome, Attribution (Sprint 1 + 2 canonical)"""

from src.telemetry.attribution import (
    decompose_attribution,
    generate_summary_vi,
    run_attribution_for_outcomes,
    update_engine_performance,
)
from src.telemetry.evaluator import evaluate_pending, evaluate_single, run_telemetry_evaluation
from src.telemetry.models import (
    DecisionAttributionSummary,
    DecisionSnapshot,
    EngineAttribution,
    EnginePerformanceView,
    MarketOutcome,
    OutcomeRecord,
)
from src.telemetry.prediction_registry import (
    get_raw_entries,
    get_registry_stats,
    log_predictions,
    run_registry_update,
    update_outcomes,
)
from src.telemetry.recorder import record_decision
from src.telemetry.storage import (
    get_all_snapshots,
    get_attribution_summary,
    get_attributions,
    get_engine_performance,
    get_outcomes,
    get_pending_decisions,
    get_snapshot,
    get_snapshot_stats,
    initialize_telemetry_database,
    save_market_outcome,
    save_outcome,
    save_snapshot,
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
