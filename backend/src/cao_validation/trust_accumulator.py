"""CAO Trust Bridge — Trust Accumulator.

Accumulates regime-weighted confidence over time.
Confidence is NOT sample count — it is a composite metric:

    confidence_t = Σ(consistency × regime_weight × stability_factor) / t

Where:
    - consistency = causal agreement score [0, 1]
    - regime_weight = per-regime multiplier (TRENDING=1.0, RANGING=0.8, CRISIS=0.5)
    - stability_factor = inverse variance of recent consistency scores
"""

import json
import logging
import sys
from collections import defaultdict
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

from src.cao_validation.models import (
    ConsistencyScore,
    TrustHistoryPoint,
    TrustState,
)

REGIME_WEIGHTS = {
    "TRENDING": 1.0,
    "RANGING": 0.80,
    "CRISIS": 0.50,
}

DEFAULT_REGIME = "RANGING"
STABILITY_WINDOW = 20


class TrustAccumulator:
    """Regime-aware confidence accumulation system.

    NOT sample counting — composite weighted confidence.
    Maintains per-regime state (never global).
    """

    def __init__(self, storage=None):
        self._history: dict[str, list[TrustHistoryPoint]] = defaultdict(list)
        self._states: dict[str, TrustState] = {}
        self._storage = storage
        self._load_persisted()

    def update(
        self,
        score: ConsistencyScore,
        data_integrity_score: float = 1.0,
    ) -> TrustState:
        """Update trust state with a new consistency score.

        Args:
            score: ConsistencyScore from consistency engine
            data_integrity_score: DIS ∈ [0, 1] from Data Quality Monitor.
                1.0 = clean pipeline (no modulation).
                < 1.0 reduces effective consistency → slower trust accumulation.

        Returns:
            Updated TrustState for the score's regime
        """
        regime = score.regime if score.regime in REGIME_WEIGHTS else DEFAULT_REGIME
        regime_weight = REGIME_WEIGHTS.get(regime, 0.5)
        stability_factor = self._compute_stability_factor(regime, score.overall)
        dis = max(0.0, min(1.0, data_integrity_score))
        point = TrustHistoryPoint(
            decision_id=score.decision_id,
            consistency=score.overall * dis,  # DIS modulates effective consistency
            regime=regime,
            regime_weight=regime_weight,
            stability_factor=stability_factor,
            timestamp="",
        )
        self._history[regime].append(point)
        state = self._recompute_state(regime)
        state.data_integrity_score = dis
        self._states[regime] = state
        self._persist_state(regime, state)
        return state

    def get_state(self, regime: str | None = None) -> dict[str, TrustState]:
        """Get current trust state(s).

        Args:
            regime: if None, return all regimes

        Returns:
            {regime: TrustState}
        """
        if regime:
            s = self._states.get(regime)
            return {regime: s} if s else {}
        return dict(self._states)

    def get_history(self, regime: str | None = None, limit: int = 100) -> list[TrustHistoryPoint]:
        """Get trust accumulation history."""
        if regime:
            return self._history.get(regime, [])[-limit:]
        all_points = []
        for pts in self._history.values():
            all_points.extend(pts)
        return sorted(all_points, key=lambda p: p.timestamp)[-limit:]

    def reset(self, regime: str | None = None):
        """Reset trust state for a regime (or all if None)."""
        if regime:
            self._history[regime] = []
            self._states.pop(regime, None)
        else:
            self._history.clear()
            self._states.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_stability_factor(self, regime: str, current: float) -> float:
        """Stability factor = inverse variance of recent consistency scores.

        Higher variance → lower stability_factor → less weight on new samples.
        """
        recent = [p.consistency for p in self._history[regime][-STABILITY_WINDOW:]]
        if len(recent) < 3:
            return 1.0
        mean = sum(recent) / len(recent)
        variance = sum((v - mean) ** 2 for v in recent) / len(recent)
        if variance < 0.001:
            return 1.0
        return round(min(1.0, 0.1 / variance), 4)

    def _recompute_state(self, regime: str) -> TrustState:
        """Recompute TrustState for a regime from history."""
        points = self._history[regime]
        if not points:
            return TrustState(
                regime=regime,
                total_samples=0,
                mean_consistency=0.0,
                consistency_variance=0.0,
                confidence=0.0,
                drift_score=0.0,
                structural_shift=False,
                distribution_equivalent=False,
            )
        consistencies = [p.consistency for p in points]
        n = len(consistencies)
        mean_c = sum(consistencies) / n
        variance = sum((v - mean_c) ** 2 for v in consistencies) / n if n > 1 else 0.0
        weighted_scores = [p.consistency * p.regime_weight * p.stability_factor for p in points]
        total_weight = sum(p.regime_weight * p.stability_factor for p in points)
        confidence = (sum(weighted_scores) / total_weight) if total_weight > 0 else 0.0
        recent = consistencies[-min(n, STABILITY_WINDOW) :]
        drift = sum(abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))) / len(recent) if len(recent) > 1 else 0.0
        return TrustState(
            regime=regime,
            total_samples=n,
            mean_consistency=round(mean_c, 4),
            consistency_variance=round(variance, 4),
            confidence=round(confidence, 4),
            drift_score=round(drift, 4),
            structural_shift=drift > 0.30,
            distribution_equivalent=False,
        )

    def _persist_state(self, regime: str, state: TrustState):
        """Persist trust state to storage (if available)."""
        if not self._storage:
            return
        try:
            self._storage.save_belief_value(
                f"trust_state_{regime}",
                json.dumps(
                    {
                        "confidence": state.confidence,
                        "mean_consistency": state.mean_consistency,
                        "total_samples": state.total_samples,
                        "drift_score": state.drift_score,
                        "data_integrity_score": state.data_integrity_score,
                    }
                ),
            )
        except Exception as e:
            logger.warning("[TRUST] Persist failed for %s: %s", regime, e)

    def _load_persisted(self):
        """Load persisted trust state from storage (if available)."""
        if not self._storage:
            return
        for regime in REGIME_WEIGHTS:
            try:
                raw = self._storage.get_belief_value(f"trust_state_{regime}")
                if raw:
                    data = json.loads(raw)
                    self._states[regime] = TrustState(
                        regime=regime,
                        total_samples=data.get("total_samples", 0),
                        mean_consistency=data.get("mean_consistency", 0.0),
                        consistency_variance=0.0,
                        confidence=data.get("confidence", 0.0),
                        drift_score=data.get("drift_score", 0.0),
                        structural_shift=False,
                        distribution_equivalent=False,
                        data_integrity_score=data.get("data_integrity_score", 1.0),
                    )
            except Exception:
                pass


# Singleton
_accumulator: TrustAccumulator = None


def get_accumulator() -> TrustAccumulator:
    """Get or create the singleton TrustAccumulator."""
    global _accumulator
    if _accumulator is None:
        try:
            from src.shadow_cao.storage import get_belief_value, save_belief_value

            _accumulator = TrustAccumulator(
                storage=type(
                    "Store",
                    (),
                    {
                        "get_belief_value": staticmethod(get_belief_value),
                        "save_belief_value": staticmethod(save_belief_value),
                    },
                )()
            )
        except Exception:
            _accumulator = TrustAccumulator()
    return _accumulator
