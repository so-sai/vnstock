"""CAO Trust Bridge — Data models for statistical validation layer.

This is NOT a threshold system. It is a distribution alignment validator
+ regime-aware promotion control system.
"""
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


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


# ====================================================================
# REGIME DEFINITIONS
# ====================================================================

RECOGNIZED_REGIMES = ["TRENDING", "RANGING", "CRISIS"]
STRICTNESS_LEVELS = ["medium", "high", "very_high", "freeze"]


@dataclass
class RegimeThreshold:
    """Per-regime promotion threshold (NOT global)."""
    regime: str
    required_consistency: float
    required_samples: int
    strictness: str
    distribution_equivalence_p: float


# ====================================================================
# CONSISTENCY DATA
# ====================================================================

@dataclass
class EngineContribution:
    """Per-engine contribution comparison."""
    engine: str
    shadow_contribution: float
    real_contribution: float
    contribution_delta: float
    rank_shadow: int
    rank_real: int
    rank_flipped: bool


@dataclass
class ConsistencyScore:
    """Fine-grained consistency breakdown for one decision."""
    decision_id: str
    regime: str
    overall: float
    attribution_stability: float
    sign_consistency: float
    sensitivity_invariance: float
    engine_contributions: list[EngineContribution]
    delta_alpha_shadow: float
    delta_alpha_real: float
    delta_alpha_agreement: bool


# ====================================================================
# DISTRIBUTION EQUIVALENCE
# ====================================================================

@dataclass
class DistributionTestResult:
    """Result of distribution equivalence test."""
    test_name: str
    statistic: float
    p_value: float
    critical_value: float
    equivalent: bool
    regime: str
    n_shadow: int
    n_real: int


# ====================================================================
# TRUST STATE
# ====================================================================

@dataclass
class TrustHistoryPoint:
    """Single point in trust accumulation history."""
    decision_id: str
    consistency: float
    regime: str
    regime_weight: float
    stability_factor: float
    timestamp: str


@dataclass
class TrustState:
    """Current trust state per regime (NOT global — regime-aware)."""
    regime: str
    total_samples: int
    mean_consistency: float
    consistency_variance: float
    confidence: float
    drift_score: float
    structural_shift: bool
    distribution_equivalent: bool
    data_integrity_score: float = 1.0


# ====================================================================
# PROMOTION CONTEXT
# ====================================================================

@dataclass
class PromotionContext:
    """Full context for promotion decision."""
    regime: str
    confidence: float
    threshold: float
    consistency: float
    required_consistency: float
    sample_count: int
    required_samples: int
    distribution_match: bool
    drift_score: float
    max_drift: float
    strictness: str

    @property
    def consistency_met(self) -> bool:
        return self.consistency >= self.required_consistency

    @property
    def samples_met(self) -> bool:
        return self.sample_count >= self.required_samples

    @property
    def confidence_met(self) -> bool:
        return self.confidence >= self.threshold

    @property
    def drift_met(self) -> bool:
        return self.drift_score <= self.max_drift


@dataclass
class PromotionVerdict:
    """Final promotion decision."""
    can_promote: bool
    regime: str
    gates: dict[str, bool]
    failures: list[str]
    message: str
    context: PromotionContext


# ====================================================================
# VALIDATION REPORT
# ====================================================================

@dataclass
class CABValidationReport:
    """Full CAO Trust Bridge validation report."""
    timestamp: str
    regime_states: list[TrustState]
    promotion_verdicts: list[PromotionVerdict]
    consistency_scores: list[ConsistencyScore]
    distribution_tests: list[DistributionTestResult]
    overall_promotable: bool
    summary: str
