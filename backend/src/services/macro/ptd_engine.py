"""
ptd_engine.py — PTD Integration Engine (Conditioning Gate).

Architecture: PTD Layer 4 (Governor Interface)
  Pipeline: TimeSeriesAligner → BPIMM → PhaseTransitionDetector → Governor

Output: MacroState with narrative, confidence, regime weights, position sizing penalty.

Position sizing penalty (from architectural spec 2026-07-09):
  σ²_mixture = Σ w_i·(σ²_i + μ²_i) - (Σ w_i·μ_i)²
  Penalty = λ · σ²_mixture / (Σ w_i·σ²_i)
  S_effective = S_base / (1 + Penalty)
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .bp_imm import BPIMM, BP_IMM_Output, GaussianComponent
from .time_series_aligner import TimeSeriesAligner
from .phase_transition_detector import PhaseTransitionDetector, NarrativeOutput

logger = logging.getLogger(__name__)

# Default: 7-dim observation = 7-dim driver space
# [Liquidity, Inflation, Growth, Energy, Risk, AI_Capex, Trust]
N_DRIVERS = 7
LAMBDA_PENALTY = 1.0  # position sizing penalty scaling

# ── Driver impact weights per asset class ──
# Positive value = component with this driver direction hurts the asset class
#   Impact > threshold → component is "downside" for this asset class
IMPACT_RISK_ON = np.array([0.20, 0.05, -0.40, 0.15, 0.35, -0.30, -0.10])
IMPACT_DEFENSIVE = np.array([0.30, 0.10, -0.25, 0.10, 0.25, -0.05, -0.35])
# Key: defensive (gold) benefits from high Liquidity stress, high Risk, low Trust

IMPACT_THRESHOLD = 0.10  # minimum impact score to qualify as downside component


@dataclass
class MacroState:
    """Final output for Governor consumption."""
    dominant_narrative: str
    narrative_probs: dict[str, float]
    governor_confidence: float
    novelty_flag: bool
    novelty_label: Optional[str]
    mahalanobis_distance: float
    driver_vector: np.ndarray
    driver_labels: list[str]
    regime_weights: np.ndarray
    regime_labels: list[str]
    n_regime_components: int
    position_penalty: float
    position_scalar: float  # S_effective / S_base
    risk_on_scalar: float   # position scalar for high-beta (Tech)
    defensive_scalar: float # position scalar for defensive (Gold/STB@MA50)
    spectral_stress: str
    mixture_moments: dict


class PTDEngine:
    """
    Full PTD pipeline orchestrator.

    Usage:
        engine = PTDEngine()
        state = engine.step(observation_vector)
        governor.decide(state)
    """

    def __init__(self, n_drivers: int = N_DRIVERS, n_regimes: int = 4):
        self.n = n_drivers
        self.aligner = TimeSeriesAligner()
        self.bp_imm = BPIMM(n_drivers=n_drivers, n_regimes=n_regimes)
        self.ptd = PhaseTransitionDetector()
        self._step_count = 0

    # ── Main Pipeline ─────────────────────────────────────────────────

    def step(self, observation: np.ndarray,
             aligner_features: Optional[dict] = None) -> MacroState:
        """
        Single pipeline step.

        Parameters
        ----------
        observation : np.ndarray (n_drivers,)
            Feature vector from macro data (e.g., aligned driver proxies).
        aligner_features : dict, optional
            Additional features from TimeSeriesAligner (eigenvalues, etc.)
            used for spectral stress computation.

        Returns
        -------
        MacroState with full narrative + risk context.
        """
        self._step_count += 1
        obs = np.asarray(observation, dtype=float).ravel()
        assert obs.shape[0] == self.n, (
            f"Observation dim {obs.shape[0]} != {self.n}"
        )

        # 1. BP-IMM: track driver state
        imm_out: BP_IMM_Output = self.bp_imm.update(obs)

        # 2. PTD: narrative emergence
        narrative: NarrativeOutput = self.ptd.update(imm_out.mixture)

        # 3. Position sizing penalty (asymmetric downside semi-variance)
        penalty, risk_on_scalar, defensive_scalar = self._compute_asymmetric_penalty(imm_out)

        # 4. Spectral stress
        stress = self._spectral_stress(aligner_features)

        # 5. Mixture moments
        moments = self.bp_imm.get_mixture_moments()

        return MacroState(
            dominant_narrative=narrative.dominant_narrative,
            narrative_probs=narrative.narrative_probs,
            governor_confidence=narrative.governor_confidence,
            novelty_flag=narrative.novelty_flag,
            novelty_label=narrative.provisional_label,
            mahalanobis_distance=narrative.mahalanobis_distance,
            driver_vector=narrative.driver_vector,
            driver_labels=[
                "Liquidity", "Inflation", "Growth",
                "Energy", "Risk", "AI_Capex", "Trust",
            ],
            regime_weights=imm_out.regime_weights,
            regime_labels=["NORMAL", "STRESS", "LIQUIDITY", "RECOVERY"],
            n_regime_components=imm_out.n_active,
            position_penalty=round(penalty, 4),
            position_scalar=round(risk_on_scalar, 4),
            risk_on_scalar=round(risk_on_scalar, 4),
            defensive_scalar=round(defensive_scalar, 4),
            spectral_stress=stress,
            mixture_moments={
                "mean": [round(float(v), 4) for v in moments["mean"]],
                "n_components": moments["n_components"],
            },
        )

    def reset(self) -> None:
        """Reset engine to initial state."""
        self.bp_imm.reset()
        self.ptd.reset()
        self._step_count = 0

    # ── Position Sizing Penalty (Asymmetric Downside Semi-variance) ───

    @staticmethod
    def _compute_asymmetric_penalty(imm_out: BP_IMM_Output) -> tuple[float, float, float]:
        """
        Asymmetric downside semi-variance penalty per asset class.

        Rules (from architectural spec 2026-07-09):
          - Only extract variance from components with negative outlook
            (expected impact > threshold for the asset class)
          - Risk-on (Tech): penalized by components with high Risk + low Growth
          - Defensive (Gold/STB@MA50): NOT penalized — maintained at full buying power
          - Black swan component (5%) caps only high-beta, not entire portfolio

        Returns
        -------
        (avg_penalty, risk_on_scalar, defensive_scalar)
          avg_penalty: blended penalty for info display
          risk_on_scalar:  position scalar for high-beta (0-1)
          defensive_scalar: position scalar for defensive (always 1.0)
        """
        states = imm_out.mixture
        if not states:
            return 0.0, 1.0, 1.0

        w_sum = max(sum(s.weight for s in states), 1e-10)

        # Baseline: total weighted trace (denominator for penalty ratio)
        total_trace = sum(s.weight * float(np.trace(s.cov)) for s in states)

        if total_trace < 1e-10:
            return 0.0, 1.0, 1.0

        # Compute impact scores for each component
        risk_on_downside_trace = 0.0
        for s in states:
            impact = float(s.mean @ IMPACT_RISK_ON)
            if impact > IMPACT_THRESHOLD:
                # This component has negative outlook for risk-on assets
                risk_on_downside_trace += s.weight * float(np.trace(s.cov))

        # Penalty = fraction of total variance explained by downside components
        risk_on_penalty = LAMBDA_PENALTY * risk_on_downside_trace / max(total_trace, 1e-10)
        risk_on_scalar = 1.0 / (1.0 + risk_on_penalty)

        # Defensive: no penalty (full buying power preserved)
        defensive_scalar = 1.0

        # Blended penalty (for display only)
        avg_penalty = (risk_on_penalty + 0.0) / 2.0  # defensive has 0 penalty

        return avg_penalty, risk_on_scalar, defensive_scalar

    # ── Spectral Stress ───────────────────────────────────────────────

    @staticmethod
    def _spectral_stress(features: Optional[dict]) -> str:
        """Determine spectral stress level from TimeSeriesAligner features."""
        if features is None:
            return "UNKNOWN"
        lam = features.get("lambda_max", 0)
        ratio = features.get("lambda_ratio", 1)
        entropy = features.get("spectral_entropy", 1)
        if lam > 0.6 and ratio > 5.0:
            return "SYSTEMIC"
        if lam > 0.4 or ratio > 3.0:
            return "ELEVATED"
        if entropy > 0.8:
            return "DISPERSED"
        return "NORMAL"

    # ── Governor Decision Helper ──────────────────────────────────────

    @staticmethod
    def should_block_trading(state: MacroState) -> list[str]:
        """Governor veto gate: block specific asset classes."""
        conditions = []
        if state.novelty_flag and state.governor_confidence < 0.3:
            conditions.append("ALL:novelty+low_confidence")
        if state.dominant_narrative == "LIQUIDITY_CRUNCH":
            conditions.append("ALL:liquidity_crisis")
        if state.risk_on_scalar < 0.3:
            conditions.append("RISK_ON:high_position_penalty")
        return conditions

    @staticmethod
    def should_reduce_exposure(state: MacroState) -> dict[str, bool]:
        """Governor: asset-class-specific exposure reduction."""
        return {
            "risk_on": (
                state.novelty_flag and state.governor_confidence < 0.5
            ) or state.spectral_stress == "SYSTEMIC" or state.risk_on_scalar < 0.5,
            "defensive": False,  # never reduce defensive
        }
