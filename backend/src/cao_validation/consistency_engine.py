"""CAO Trust Bridge — Consistency Engine (Statistical Core).

Evaluates 3 dimensions of shadow-vs-real consistency per decision:
1. Attribution stability — engine contribution ranking maintained?
2. ΔAlpha sign consistency — does shadow predict same direction as reality?
3. Sensitivity invariance — when engine X is ablated, does the sensitivity match?

NOT accuracy. This is CAUSAL AGREEMENT measurement.
"""
import sys
import math
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
    ConsistencyScore, EngineContribution, TrustHistoryPoint,
)


CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]


# ====================================================================
# CORE: Attribution Stability
# ====================================================================

def _kendall_tau(rank_a: list[int], rank_b: list[int]) -> float:
    """Kendall rank correlation coefficient between two rankings.

    Measures how well the engine contribution ranking is preserved
    between shadow and real attribution.
    1.0 = identical ranking, 0.0 = independent, -1.0 = inverse.
    """
    n = len(rank_a)
    if n < 2:
        return 0.0
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a_diff = rank_a[i] - rank_a[j]
            b_diff = rank_b[i] - rank_b[j]
            if a_diff * b_diff > 0:
                concordant += 1
            elif a_diff * b_diff < 0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return 0.0
    return (concordant - discordant) / total


def compute_attribution_stability(
    shadow_contributions: dict[str, float],
    real_contributions: dict[str, float],
) -> tuple[float, list[EngineContribution]]:
    """Compare engine contribution ranking between shadow and real.

    Args:
        shadow_contributions: {engine: contribution} from shadow CAO
        real_contributions: {engine: contribution} from telemetry attribution

    Returns:
        (kendall_tau, [EngineContribution, ...])
    """
    engines = [e for e in CANONICAL_ENGINES
               if e in shadow_contributions and e in real_contributions]
    if len(engines) < 2:
        return 0.0, []
    shadow_sorted = sorted(engines, key=lambda e: shadow_contributions[e], reverse=True)
    real_sorted = sorted(engines, key=lambda e: real_contributions[e], reverse=True)
    shadow_rank = {e: i for i, e in enumerate(shadow_sorted)}
    real_rank = {e: i for i, e in enumerate(real_sorted)}
    rank_list_a = [shadow_rank[e] for e in engines]
    rank_list_b = [real_rank[e] for e in engines]
    kendall = _kendall_tau(rank_list_a, rank_list_b)
    contributions = []
    for eng in engines:
        s = shadow_contributions[eng]
        r = real_contributions[eng]
        contributions.append(EngineContribution(
            engine=eng,
            shadow_contribution=s,
            real_contribution=r,
            contribution_delta=round(abs(s - r), 4),
            rank_shadow=shadow_rank[eng],
            rank_real=real_rank[eng],
            rank_flipped=shadow_rank[eng] != real_rank[eng],
        ))
    return round(kendall, 4), contributions


# ====================================================================
# CORE: ΔAlpha Sign Consistency
# ====================================================================

def compute_sign_consistency(shadow_delta_alpha: float, real_delta_alpha: float) -> float:
    """Check if shadow and real agree on ΔAlpha direction.

    1.0 = same sign, 0.0 = opposite sign.
    """
    if shadow_delta_alpha * real_delta_alpha > 0:
        return 1.0
    if shadow_delta_alpha == 0 and real_delta_alpha == 0:
        return 1.0
    return 0.0


# ====================================================================
# CORE: Sensitivity Invariance
# ====================================================================

def compute_sensitivity_invariance(
    shadow_attr_perturbations: list[dict],
    ablation_confidence_deltas: list[tuple[str, float]],
) -> float:
    """Compare shadow attribution perturbation vs real ablation sensitivity.

    When engine X is ablated, the change in shadow attribution contribution
    should match the change in real decision confidence.

    Args:
        shadow_attr_perturbations: list of dicts with engine, baseline_contribution,
            ablated_contribution, contribution_delta
        ablation_confidence_deltas: list of (engine, confidence_delta) from ablation

    Returns:
        sensitivity score [0, 1]: 1.0 = perfect invariance match
    """
    if not shadow_attr_perturbations or not ablation_confidence_deltas:
        return 0.5
    shadow_map = {p["engine"]: p["contribution_delta"]
                  for p in shadow_attr_perturbations
                  if isinstance(p, dict)}
    real_map = dict(ablation_confidence_deltas)
    common = set(shadow_map.keys()) & set(real_map.keys())
    if not common:
        return 0.5
    diffs = []
    for eng in common:
        s = abs(shadow_map[eng])
        r = abs(real_map[eng])
        if max(s, r) == 0:
            diffs.append(1.0)
        else:
            diffs.append(1.0 - abs(s - r) / max(s, r))
    return round(sum(diffs) / len(diffs), 4)


# ====================================================================
# OVERALL CONSISTENCY
# ====================================================================

def compute_consistency_score(
    decision_id: str,
    regime: str,
    shadow_contributions: dict[str, float],
    real_contributions: dict[str, float],
    shadow_delta_alpha: float,
    real_delta_alpha: float,
    shadow_perturbations: list[dict] = None,
    ablation_deltas: list[tuple[str, float]] = None,
) -> ConsistencyScore:
    """Compute full consistency score for one decision.

    overall = weighted average of:
        - attribution_stability (0.4)
        - sign_consistency (0.35)
        - sensitivity_invariance (0.25)
    """
    attribution_stab, engine_contribs = compute_attribution_stability(
        shadow_contributions, real_contributions,
    )
    sign_cons = compute_sign_consistency(shadow_delta_alpha, real_delta_alpha)
    sensitivity = compute_sensitivity_invariance(
        shadow_perturbations or [], ablation_deltas or [],
    )
    overall = round(
        0.40 * attribution_stab + 0.35 * sign_cons + 0.25 * sensitivity,
        4,
    )
    return ConsistencyScore(
        decision_id=decision_id,
        regime=regime,
        overall=overall,
        attribution_stability=attribution_stab,
        sign_consistency=sign_cons,
        sensitivity_invariance=sensitivity,
        engine_contributions=engine_contribs,
        delta_alpha_shadow=shadow_delta_alpha,
        delta_alpha_real=real_delta_alpha,
        delta_alpha_agreement=shadow_delta_alpha * real_delta_alpha >= 0,
    )


# ====================================================================
# BATCH CONSISTENCY
# ====================================================================

def compute_batch_consistency(
    scores: list[ConsistencyScore],
    regime: str = None,
) -> dict:
    """Aggregate consistency statistics over a batch of scores."""
    if not scores:
        return {
            "mean_consistency": 0.0,
            "variance": 0.0,
            "min": 0.0,
            "max": 0.0,
            "count": 0,
            "regime": regime or "ALL",
        }
    vals = [s.overall for s in scores
            if (regime is None or s.regime == regime)]
    if not vals:
        return {
            "mean_consistency": 0.0,
            "variance": 0.0,
            "min": 0.0,
            "max": 0.0,
            "count": 0,
            "regime": regime or "ALL",
        }
    n = len(vals)
    mean = sum(vals) / n
    variance = sum((v - mean) ** 2 for v in vals) / n if n > 1 else 0.0
    return {
        "mean_consistency": round(mean, 4),
        "variance": round(variance, 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "count": n,
        "regime": regime or "ALL",
    }
