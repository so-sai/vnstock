"""
phase_transition_detector.py — Module 3: Phase Transition Detector.

Architecture: PTD Layer 3 (Narrative Emergence)
  Input:  Gaussian mixture from BP-IMM (driver space, 7 dimensions)
  Output: Narrative probabilities, novelty flag, governor confidence

Specification (frozen 2026-07-09, Hotfix #3 2026-07-09):
  Mahalanobis χ² test with α=0.01, df=7 → threshold = 18.475
  D_M² > 18.475 → novelty_flag = True → provisional template → confidence decay
  D_M² ≤ 18.475 → template matching with softmax → confidence normal
  Governor confidence: novelty_flag == True → decay exponentially
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2

from .bp_imm import GaussianComponent

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

# χ²(7) 99th percentile = 18.475 (α=0.01)
# Hotfix #3: comparison MUST be on D_M² (squared), not D_M linear
CHI2_THRESHOLD = float(chi2.ppf(0.99, 7))  # 18.475 — compared with D_M²

N_DRIVERS = 7
DRIVER_LABELS = [
    "Liquidity",
    "Inflation",
    "Growth",
    "Energy",
    "Risk",
    "AI_Capex",
    "Trust",
]

# ── Narrative Templates ──────────────────────────────────────────────
# Each template = (centroid_vector, covariance_diagonal)

NARRATIVE_TEMPLATES = {
    "NORMAL": {
        "centroid": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "var": np.array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]),
        "description": "Baseline — no dominant macro stress",
    },
    "LIQUIDITY_CRUNCH": {
        "centroid": np.array([0.85, -0.2, -0.5, 0.7, 0.85, 0.3, -0.3]),
        "var": np.array([0.15, 0.20, 0.20, 0.25, 0.15, 0.25, 0.20]),
        "description": "Liquidity scarcity — broad correlation spike, risk-off",
    },
    "INFLATION": {
        "centroid": np.array([0.3, 0.85, 0.2, 0.7, 0.3, 0.1, 0.2]),
        "var": np.array([0.20, 0.10, 0.15, 0.15, 0.20, 0.20, 0.15]),
        "description": "Inflation regime — commodity-driven, rates rising",
    },
    "AI_BOOM": {
        "centroid": np.array([-0.1, 0.1, 0.75, 0.2, -0.1, 0.85, 0.3]),
        "var": np.array([0.15, 0.15, 0.15, 0.20, 0.15, 0.10, 0.20]),
        "description": "AI expansion — growth acceleration, tech capex surge",
    },
    "ENERGY_SHOCK": {
        "centroid": np.array([0.3, 0.5, -0.3, 0.9, 0.5, 0.0, 0.0]),
        "var": np.array([0.20, 0.20, 0.20, 0.08, 0.20, 0.20, 0.15]),
        "description": "Energy supply shock — commodity spike, growth drag",
    },
}

NARRATIVE_NAMES = list(NARRATIVE_TEMPLATES.keys())


@dataclass
class NarrativeOutput:
    """Output from Phase Transition Detector."""

    narrative_probs: dict[str, float]
    dominant_narrative: str
    novelty_flag: bool
    mahalanobis_distance: float
    governor_confidence: float
    provisional_label: str | None
    driver_vector: np.ndarray


class PhaseTransitionDetector:
    """
    Phase Transition Detector — narrative emergence + novelty detection.

    Parameters
    ----------
    chi2_threshold : float
        D_M² threshold for novelty (default chi2.ppf(0.99, 7) = 18.475).
    confidence_decay_rate : float
        Exponential decay rate for governor confidence when novelty detected
        (default 0.3 per step).
    """

    def __init__(self, chi2_threshold: float = CHI2_THRESHOLD, confidence_decay_rate: float = 0.3):
        self.threshold = chi2_threshold  # compared with D_M²
        self.decay_rate = confidence_decay_rate

        # Build template matrices
        self.templates: dict[str, dict] = {}
        for name, t in NARRATIVE_TEMPLATES.items():
            self.templates[name] = {
                "mean": t["centroid"].copy(),
                "cov": np.diag(t["var"]),
                "cov_inv": np.diag(1.0 / t["var"]),
                "description": t["description"],
            }

        self._template_means = np.array([t["mean"] for t in self.templates.values()])
        self._template_names = list(self.templates.keys())
        self._n_templates = len(self._template_names)

        # Provisional template tracking
        self.provisionals: dict[str, dict] = {}
        self._provisional_counter = 0

        # Governor confidence
        self.governor_confidence: float = 1.0
        self._novelty_steps: int = 0

    # ── Public API ────────────────────────────────────────────────────

    def update(self, mixture: list[GaussianComponent]) -> NarrativeOutput:
        """
        Single step: compute driver vector → detect novelty → map narrative.

        Parameters
        ----------
        mixture : list[GaussianComponent]
            Output from BP-IMM update().

        Returns
        -------
        NarrativeOutput with probabilities, novelty, confidence.
        """
        # 1. Compute driver vector from mixture
        driver = self._mixture_to_driver(mixture)

        # 2. Compute Mahalanobis to each template
        maha_dist, closest_idx = self._mahalanobis_to_all(driver)
        closest_name = self._template_names[closest_idx]

        # 3. Novelty detection
        novelty_flag = maha_dist > self.threshold

        # 4. Compute narrative probabilities (softmax over Mahalanobis)
        narrative_probs = self._softmax_narrative(driver)

        # 5. Handle provisional templates
        provisional_label = None
        if novelty_flag:
            provisional_label = self._handle_novelty(driver, closest_name)
            self._novelty_steps += 1
        else:
            self._novelty_steps = 0

        # 6. Compute governor confidence
        self.governor_confidence = self._compute_confidence(novelty_flag)
        gov_conf = round(self.governor_confidence, 4)

        # 7. Determine dominant narrative
        if novelty_flag and provisional_label:
            dominant = provisional_label
        else:
            dominant = max(narrative_probs, key=narrative_probs.get)

        return NarrativeOutput(
            narrative_probs=narrative_probs,
            dominant_narrative=dominant,
            novelty_flag=novelty_flag,
            mahalanobis_distance=round(maha_dist, 4),
            governor_confidence=gov_conf,
            provisional_label=provisional_label,
            driver_vector=driver,
        )

    def reset(self) -> None:
        """Reset PTD to initial state."""
        self.provisionals.clear()
        self._provisional_counter = 0
        self.governor_confidence = 1.0
        self._novelty_steps = 0

    # ── Driver Computation ────────────────────────────────────────────

    @staticmethod
    def _mixture_to_driver(mixture: list[GaussianComponent]) -> np.ndarray:
        """Collapse mixture to single driver vector."""
        if not mixture:
            return np.zeros(N_DRIVERS)
        w_sum = max(sum(s.weight for s in mixture), 1e-10)
        driver = sum(s.weight * s.mean for s in mixture) / w_sum
        return driver

    # ── Mahalanobis Distance ──────────────────────────────────────────

    def _mahalanobis_sq(self, x: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray) -> float:
        """Squared Mahalanobis: D_M² = (x-μ)ᵀ Σ⁻¹ (x-μ)."""
        delta = x - mean
        return float(delta @ cov_inv @ delta)

    def _mahalanobis_to_all(self, x: np.ndarray) -> tuple[float, int]:
        """Mahalanobis to all templates, return (min_D_M², closest_idx)."""
        distances = np.array([self._mahalanobis_sq(x, t["mean"], t["cov_inv"]) for t in self.templates.values()])
        min_idx = int(np.argmin(distances))
        return float(distances[min_idx]), min_idx

    # ── Narrative Probabilities ───────────────────────────────────────

    def _softmax_narrative(self, x: np.ndarray) -> dict[str, float]:
        """Softmax over negative Mahalanobis distances (linear D_M)."""
        d2 = np.array([self._mahalanobis_sq(x, t["mean"], t["cov_inv"]) for t in self.templates.values()])
        d_lin = np.sqrt(np.maximum(d2, 1e-15))  # linear D_M for softmax
        # Negative distances so closer template → higher prob
        # Temperature scaling to avoid overconfidence
        scores = -d_lin
        scores -= scores.max()  # numerical stability
        exp_scores = np.exp(scores)
        probs = exp_scores / max(exp_scores.sum(), 1e-10)
        return dict(zip(self._template_names, probs))

    # ── Novelty Detection ─────────────────────────────────────────────

    def _handle_novelty(self, driver: np.ndarray, closest_name: str) -> str:
        """
        Handle novelty detection: create or update provisional template.

        Returns provisional label.
        """
        label = f"NOVEL_{self._provisional_counter}"
        self._provisional_counter += 1

        self.provisionals[label] = {
            "centroid": driver.copy(),
            "closest_template": closest_name,
            "creation_step": self._novelty_steps,
            "stability_score": 0.0,
            "observations": [driver.copy()],
        }

        d_m_sq = self._mahalanobis_to_all(driver)[0]
        d_m_lin = np.sqrt(max(d_m_sq, 1e-15))
        logger.info(
            "Novelty detected: D_M=%.2f (D_M²=%.2f > χ²=%.2f). "
            "Provisional '%s' created (closest: %s). "
            "Governor confidence decaying.",
            d_m_lin,
            d_m_sq,
            self.threshold,
            label,
            closest_name,
        )
        return label

    def update_provisional_template(self, label: str, driver: np.ndarray) -> None:
        """
        Update existing provisional template with new observation.

        Called externally when novelty_flag persists across steps.
        """
        if label not in self.provisionals:
            logger.warning("Provisional template '%s' not found.", label)
            return

        prov = self.provisionals[label]
        prov["observations"].append(driver.copy())

        n = len(prov["observations"])
        if n >= 20:
            # Stability check: mean cosine similarity over last 20 steps
            vectors = np.array(prov["observations"][-20:])
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-10)
            normalized = vectors / norms
            cos_sim = (normalized[:-1] * normalized[1:]).sum(axis=1).mean()
            prov["stability_score"] = round(float(cos_sim), 4)

    # ── Governor Confidence ───────────────────────────────────────────

    def _compute_confidence(self, novelty_flag: bool) -> float:
        """Exponential decay of governor confidence during novelty."""
        if novelty_flag:
            decay = np.exp(-self.decay_rate * self._novelty_steps)
            return max(decay, 0.1)  # floor at 0.1
        # Recovery when novelty clears
        recovery = self.governor_confidence + 0.05 * (1.0 - self.governor_confidence)
        return min(recovery, 1.0)

    # ── Template Information ──────────────────────────────────────────

    def list_templates(self) -> list[dict]:
        """List all defined templates with descriptions."""
        result = []
        for name, t in self.templates.items():
            result.append(
                {
                    "name": name,
                    "description": t["description"],
                    "mean": t["mean"].tolist(),
                }
            )
        for name, p in self.provisionals.items():
            result.append(
                {
                    "name": name,
                    "description": "Provisional (novelty)",
                    "closest": p["closest_template"],
                    "stability": p["stability_score"],
                    "observations": len(p["observations"]),
                }
            )
        return result
