"""Shadow CAO — Data models (NO synthetic outcomes, only ablation)"""
import sys
from dataclasses import dataclass
from pathlib import Path


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


@dataclass
class ShadowDecisionLog:
    """Logged at decision time — real engine scores + market state only."""
    decision_id: str
    timestamp: str
    posture: str
    risk_level: str
    confidence: float
    engine_scores: dict[str, float]
    decision_weights: dict[str, float]
    market_regime: str
    regime_score: float
    vnindex_level: float


@dataclass
class AblationResult:
    """Result of removing one engine's signal (perturb attribution, NOT market)."""
    engine_removed: str
    baseline_action: str
    baseline_confidence: float
    ablated_action: str
    ablated_confidence: float
    action_changed: bool
    confidence_delta: float
    decision_flip: bool


@dataclass
class ShadowOutcomeLog:
    """Logged at evaluation time — REAL outcome + ablated counterfactuals."""
    decision_id: str
    horizon_days: int
    actual_outcome: float
    actual_success: bool
    ablations: list[AblationResult]


@dataclass
class EngineAblationProfile:
    """Per-engine statistics accumulated over time (no weight update)."""
    engine: str
    total_decisions: int
    flip_count: int
    avg_confidence_delta: float
    action_change_ratio: float
    stability_score: float


@dataclass
class BeliefState:
    """Accumulated belief statistics — NOT used for weight updates."""
    engine_profiles: dict[str, EngineAblationProfile]
    regime_entropy_trace: list[float]
    stability_index: float
    total_decisions_logged: int
    total_outcomes_logged: int


@dataclass
class AttributionPerturbation:
    """Perturbed attribution — same market outcome, re-calculated contribution."""
    decision_id: str
    horizon_days: int
    engine: str
    signal_at_decision: float
    baseline_contribution: float
    ablated_contribution: float
    contribution_delta: float
    baseline_tp: float
    ablated_tp: float
    baseline_fp: float
    ablated_fp: float


@dataclass
class ShadowAttributionSummary:
    """Dry-run attribution result (never written to production)."""
    decision_id: str
    horizon_days: int
    engine: str
    contribution: float
    true_positive: float
    false_positive: float
    correlation: float
    accuracy: float
