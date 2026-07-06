"""CAO Trust Bridge — Regime Promotion Matrix.

Per-regime promotion thresholds (NOT global).
Different strictness levels because market is non-stationary.

Key principle:
    CAO must be HARDER to promote in crisis, not easier.
"""
import logging
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

from src.cao_validation.models import RegimeThreshold

# ====================================================================
# DEFAULT PROMOTION MATRIX
# ====================================================================
# Rationale:
#   TRENDING: market has clear direction → CAO signal easier to validate
#   RANGING: more noise → need higher consistency to trust
#   CRISIS: regime instability → highest bar (or freeze)
#
# Distribution equivalence p-value: minimum p to not reject H0
# (that shadow and live distributions are the same)

DEFAULT_PROMOTION_MATRIX: dict[str, RegimeThreshold] = {
    "TRENDING": RegimeThreshold(
        regime="TRENDING",
        required_consistency=0.70,
        required_samples=80,
        strictness="medium",
        distribution_equivalence_p=0.10,
    ),
    "RANGING": RegimeThreshold(
        regime="RANGING",
        required_consistency=0.78,
        required_samples=120,
        strictness="high",
        distribution_equivalence_p=0.15,
    ),
    "CRISIS": RegimeThreshold(
        regime="CRISIS",
        required_consistency=0.85,
        required_samples=50,
        strictness="very_high",
        distribution_equivalence_p=0.20,
    ),
}

MAX_DRIFT_SCORES = {
    "TRENDING": 0.15,
    "RANGING": 0.10,
    "CRISIS": 0.05,
}

CONFIDENCE_THRESHOLDS = {
    "TRENDING": 0.65,
    "RANGING": 0.72,
    "CRISIS": 0.80,
}


# ====================================================================
# MATRIX ACCESS
# ====================================================================

class RegimePromotionMatrix:
    """Regime-aware promotion threshold manager.

    NOT a static config — supports frozen regimes, dynamic adjustment.
    """

    def __init__(self, matrix: dict[str, RegimeThreshold] = None):
        self._matrix = matrix or dict(DEFAULT_PROMOTION_MATRIX)
        self._frozen_regimes: set[str] = set()

    def get_threshold(self, regime: str) -> RegimeThreshold:
        """Get promotion threshold for a regime.

        If regime is frozen, returns a threshold that cannot be met.
        """
        if regime in self._frozen_regimes:
            return RegimeThreshold(
                regime=regime,
                required_consistency=1.5,
                required_samples=10**6,
                strictness="freeze",
                distribution_equivalence_p=0.99,
            )
        return self._matrix.get(
            regime,
            RegimeThreshold("UNKNOWN", 0.80, 100, "high", 0.10),
        )

    def freeze_regime(self, regime: str):
        """Freeze a regime — CAO cannot promote in this regime.

        Used when:
            - regime entropy is too high
            - market structure shifts detected
            - data quality issues
        """
        self._frozen_regimes.add(regime)
        logger.info("[PROMO_MATRIX] Frozen regime: %s", regime)

    def unfreeze_regime(self, regime: str):
        """Unfreeze a previously frozen regime."""
        self._frozen_regimes.discard(regime)

    def is_frozen(self, regime: str) -> bool:
        return regime in self._frozen_regimes

    def get_max_drift(self, regime: str) -> float:
        return MAX_DRIFT_SCORES.get(regime, 0.10)

    def get_confidence_threshold(self, regime: str) -> float:
        return CONFIDENCE_THRESHOLDS.get(regime, 0.70)

    def update_threshold(self, regime: str, **kwargs):
        """Dynamically adjust a threshold (use sparingly)."""
        if regime in self._matrix:
            current = self._matrix[regime]
            for k, v in kwargs.items():
                if hasattr(current, k):
                    setattr(current, k, v)
            logger.info("[PROMO_MATRIX] Updated %s: %s", regime, kwargs)

    @property
    def frozen_regimes(self) -> list[str]:
        return sorted(self._frozen_regimes)

    @property
    def matrix(self) -> dict[str, RegimeThreshold]:
        return dict(self._matrix)


# Singleton
_matrix: RegimePromotionMatrix = None


def get_matrix() -> RegimePromotionMatrix:
    global _matrix
    if _matrix is None:
        _matrix = RegimePromotionMatrix()
    return _matrix
