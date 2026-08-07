"""Telemetry Layer — Decision Snapshot, Outcome, Attribution (Sprint 1 + 2)"""

import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


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


class DecisionSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision_id: str
    timestamp: datetime
    posture: str
    risk_level: str
    confidence: float
    dominant_signal: str
    vnindex_level: float
    opportunity_symbols: list[str] = []
    holdings_health: str | None = None
    market_regime: str | None = None
    decision_weights: str | None = None
    engine_scores: str | None = None


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision_id: str
    horizon_days: int
    vnindex_entry: float = 0.0
    vnindex_exit: float = 0.0
    vnindex_return: float
    benchmark_return: float
    success: bool


class MarketOutcome(BaseModel):
    """Market data over the evaluation horizon (Sprint 2 canonical)."""

    model_config = ConfigDict(populate_by_name=True)

    decision_id: str
    horizon_days: int
    asset_return: float
    benchmark_return: float
    alpha_return: float
    realized_volatility: float
    regime_shift: bool
    sector_rotation: str
    liquidity_phase: str
    gold_change_pct: float


class EngineAttribution(BaseModel):
    """Per-engine contribution (Sprint 2 canonical — TP/FP decomposed)."""

    model_config = ConfigDict(populate_by_name=True)

    decision_id: str
    horizon_days: int
    engine: str
    signal_at_decision: float
    contribution: float
    true_positive_contribution: float
    false_positive_contribution: float
    direction: str
    correlation: float
    accuracy: float
    precision: float


class EnginePerformanceView(BaseModel):
    """Rolling performance summary per engine (Sprint 2 canonical)."""

    model_config = ConfigDict(populate_by_name=True)

    engine: str
    window_days: int
    accuracy: float
    precision: float
    avg_contribution: float
    stability: float
    decisions_count: int
    last_updated: str


class DecisionAttributionSummary(BaseModel):
    """Full attribution for UI (Sprint 2 canonical — Vietnamese reasoning)."""

    model_config = ConfigDict(populate_by_name=True)

    decision_id: str
    horizon_days: int
    outcome_label: str
    primary_reason_vi: str
    secondary_reasons_vi: list[str] = []
    engine_scorecard: dict[str, float]
    dominant_engine: str
    total_return: float
    alpha_return: float
    confidence_recalibration: float
