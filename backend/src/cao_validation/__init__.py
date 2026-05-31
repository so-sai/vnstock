"""CAO Trust Bridge — Statistical Validation Layer.

The transition gate between Shadow CAO (observability) and Live CAO (learning).

Not a threshold system. A distribution alignment validator
+ regime-aware promotion control system.

Core flow:
    Telemetry → Shadow CAO → Consistency Engine → Trust Accumulator
        → Distribution Comparator → Regime Matrix → Activation Gate → LIVE CAO

Promotion rules (ALL must pass per regime):
    1. Distribution equivalence: shadow ΔAlpha ≈ live ΔAlpha (KS test)
    2. Confidence threshold: regime-weighted consistency accumulation
    3. Sample sufficiency: minimum samples per regime
    4. Drift tolerance: structural shift detection

Usage:
    from src.cao_validation import run_full_validation
    report = run_full_validation()
    if report.overall_promotable:
        print("CAO can be promoted to LIVE")
"""
from src.cao_validation.activation_gate import run_full_validation, evaluate_promotion
from src.cao_validation.consistency_engine import compute_consistency_score, compute_batch_consistency
from src.cao_validation.trust_accumulator import get_accumulator
from src.cao_validation.regime_promotion_matrix import get_matrix
from src.cao_validation.shadow_live_comparator import run_distribution_tests, run_per_regime_tests

__all__ = [
    "run_full_validation",
    "evaluate_promotion",
    "compute_consistency_score",
    "compute_batch_consistency",
    "get_accumulator",
    "get_matrix",
    "run_distribution_tests",
    "run_per_regime_tests",
]
