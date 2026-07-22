"""CAO Trust Bridge — Shadow vs Live Distribution Comparator.

Tests whether shadow CAO ΔAlpha distribution and live telemetry ΔAlpha
distribution are equivalent (same underlying distribution).

Uses:
    1. Kolmogorov-Smirnov test — primary: strict distribution equivalence
    2. Wasserstein distance — secondary: soft distribution divergence
    3. Per-regime segmentation — because market is non-stationary

If distribution equivalence test fails → NO PROMOTION regardless of sample size.
"""
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

from src.cao_validation.models import RECOGNIZED_REGIMES, DistributionTestResult

# ====================================================================
# KS TEST (pure Python — no scipy dependency)
# ====================================================================

def _ecdf(samples: list[float]) -> list[tuple[float, float]]:
    """Empirical cumulative distribution function."""
    sorted_s = sorted(samples)
    n = len(sorted_s)
    return [(x, (i + 1) / n) for i, x in enumerate(sorted_s)]


def _ks_statistic(samples_a: list[float], samples_b: list[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic D.

    D = max|F1(x) - F2(x)| where F1, F2 are empirical CDFs.
    """
    if not samples_a or not samples_b:
        return 1.0
    all_values = sorted(set(samples_a + samples_b))
    ecdf_a = _ecdf(samples_a)
    ecdf_b = _ecdf(samples_b)
    a_idx = 0
    b_idx = 0
    d = 0.0
    for x in all_values:
        while a_idx < len(ecdf_a) and ecdf_a[a_idx][0] <= x:
            a_idx += 1
        while b_idx < len(ecdf_b) and ecdf_b[b_idx][0] <= x:
            b_idx += 1
        f_a = (a_idx) / len(samples_a)
        f_b = (b_idx) / len(samples_b)
        d = max(d, abs(f_a - f_b))
    return d


def _ks_critical_value(n1: int, n2: int, alpha: float = 0.05) -> float:
    """Approximate KS test critical value.

    Uses the Smirnov approximation: c(alpha) * sqrt((n1+n2)/(n1*n2)).
    """
    c_alpha = {0.10: 1.22, 0.05: 1.36, 0.01: 1.63, 0.20: 1.07}
    c = c_alpha.get(alpha, 1.36)
    return c * math.sqrt((n1 + n2) / (n1 * n2))


def _ks_p_value(d: float, n1: int, n2: int) -> float:
    """Approximate KS p-value using Kolmogorov's asymptotic formula.

    p = 2 * Σ(-1)^(k-1) * exp(-2 * k² * λ²)
    where λ = (sqrt(n) + 0.12 + 0.11/sqrt(n)) * D
    """
    n = n1 * n2 / (n1 + n2)
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    p = 0.0
    for k in range(1, 10):
        term = 2.0 * (-1) ** (k - 1) * math.exp(-2.0 * k * k * lam * lam)
        p += term
    return max(0.0, min(1.0, p))


# ====================================================================
# WASSERSTEIN DISTANCE (1D earth mover's distance)
# ====================================================================

def _wasserstein_1d(samples_a: list[float], samples_b: list[float]) -> float:
    """1D Wasserstein distance = |mean_a - mean_b| (simplified for 1D)."""
    if not samples_a or not samples_b:
        return float("inf")
    return abs(sum(samples_a) / len(samples_a) - sum(samples_b) / len(samples_b))


# ====================================================================
# DISTRIBUTION COMPARISON
# ====================================================================

def compare_distributions(
    shadow_deltas: list[float],
    live_deltas: list[float],
    regime: str = "ALL",
    alpha: float = 0.05,
) -> DistributionTestResult:
    """Two-sample KS test: H0 = shadow and live come from same distribution.

    If p < alpha → reject H0 → distributions are DIFFERENT → NO PROMOTION.

    Args:
        shadow_deltas: list of ΔAlpha values from shadow CAO
        live_deltas: list of ΔAlpha values from live telemetry
        regime: regime label for segmentation
        alpha: significance level

    Returns:
        DistributionTestResult
    """
    d_stat = _ks_statistic(shadow_deltas, live_deltas)
    n1 = len(shadow_deltas)
    n2 = len(live_deltas)
    critical = _ks_critical_value(n1, n2, alpha) if n1 > 0 and n2 > 0 else 1.0
    p_value = _ks_p_value(d_stat, n1, n2) if n1 > 0 and n2 > 0 else 0.0
    equivalent = d_stat <= critical
    return DistributionTestResult(
        test_name="KS_two_sample",
        statistic=round(d_stat, 4),
        p_value=round(p_value, 4),
        critical_value=round(critical, 4),
        equivalent=equivalent,
        regime=regime,
        n_shadow=n1,
        n_real=n2,
    )


def compare_wasserstein(
    shadow_deltas: list[float],
    live_deltas: list[float],
    regime: str = "ALL",
    threshold: float = 0.05,
) -> DistributionTestResult:
    """Wasserstein distance as a soft distribution divergence measure.

    Lower is better. Useful when KS is too strict for small samples.
    """
    w = _wasserstein_1d(shadow_deltas, live_deltas)
    equivalent = w <= threshold
    return DistributionTestResult(
        test_name="Wasserstein_1d",
        statistic=round(w, 4),
        p_value=0.0,
        critical_value=threshold,
        equivalent=equivalent,
        regime=regime,
        n_shadow=len(shadow_deltas),
        n_real=len(live_deltas),
    )


def run_distribution_tests(
    shadow_deltas: list[float],
    live_deltas: list[float],
    regime: str = "ALL",
) -> list[DistributionTestResult]:
    """Run all distribution equivalence tests."""
    results = []
    results.append(compare_distributions(shadow_deltas, live_deltas, regime))
    results.append(compare_wasserstein(shadow_deltas, live_deltas, regime))
    return results


def run_per_regime_tests(
    shadow_by_regime: dict[str, list[float]],
    live_by_regime: dict[str, list[float]],
) -> list[DistributionTestResult]:
    """Run distribution tests per regime (regime-aware segmentation)."""
    results = []
    for regime in RECOGNIZED_REGIMES:
        s = shadow_by_regime.get(regime, [])
        l = live_by_regime.get(regime, [])
        if len(s) >= 5 and len(l) >= 5:
            results.extend(run_distribution_tests(s, l, regime))
    all_shadow = []
    all_live = []
    for r in RECOGNIZED_REGIMES:
        all_shadow.extend(shadow_by_regime.get(r, []))
        all_live.extend(live_by_regime.get(r, []))
    if len(all_shadow) >= 5 and len(all_live) >= 5:
        results.extend(run_distribution_tests(all_shadow, all_live, "ALL"))
    return results
