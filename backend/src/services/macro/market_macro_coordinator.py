"""
market_macro_coordinator.py — Phase Classification via 4 Spectral Indicators.

Architecture: PTD Layer 2.5 (Coordinator Context)
  Inputs: TimeSeriesAligner (eigenvalue spread, spectral entropy, cross-asset rotation)
          BPIMM (KL divergence regime spread)
  Output: Phase classification label + confidence: SILENT_DISTRIBUTION_HEAD
          vs RE_ACCUMULATION_BOTTOM vs UNCERTAIN.

4 Spectral Indicators (2026-07-09 spec):
  1. Eigenvalue Spread Trajectory  — λ₁ trend, λ₁/λ₂ ratio trajectory
  2. Spectral Entropy Slope        — d(entropy)/dt over 20-session window
  3. Cross-asset Eigenvector Rotation — angle between VNINDEX and ES=F leading eigenvectors
  4. KL Divergence Regime Spread   — max_kl between NORMAL and STRESS components
"""

import logging
from collections import deque
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Classification Thresholds ────────────────────────────────────────

# Eigenvalue spread
LAMBDA_RATIO_BOTTOM_UPPER = 3.5  # ratio <= this + converging → bottom
LAMBDA_RATIO_DIST_LOWER = 5.0    # ratio >= this → distribution
LAMBDA_MAX_SURGE = 0.5           # λ₁ rising above this → distribution signal

# Spectral entropy slope (per session, over 20-window)
ENTROPY_SLOPE_BOTTOM = -0.005   # d(entropy)/dt < this → structure reforming
ENTROPY_SLOPE_DIST = 0.005      # d(entropy)/dt > this → fragmenting

# Cross-asset rotation (degrees)
ROTATION_ANGLE_DIST = 45.0      # angle > this → distribution
ROTATION_ANGLE_BOTTOM = 30.0    # angle < this → accumulation

# KL divergence
KL_CONVERGING = 1.0             # max_kl < this → regimes converging
KL_DIVERGING = 2.0              # max_kl > this → regimes diverging

# Weighted voting
VOTE_WEIGHTS = {
    "eigenvalue_spread": 0.30,
    "entropy_slope": 0.25,
    "cross_asset_rotation": 0.25,
    "kl_divergence": 0.20,
}
CLASSIFY_THRESHOLD = 0.60       # min weighted vote share to classify


class MarketMacroCoordinator:
    """Phase classification engine for PTD Governor context.

    Maintains rolling history of spectral indicators to compute
    trajectories and slopes. Classifies current phase after each
    ``update()`` call.
    """

    governor_confidence: float = 1.0

    def __init__(self, history_window: int = 20):
        self.window = history_window
        # Rolling history
        self._eigen_history: deque[dict] = deque(maxlen=history_window)
        self._entropy_history: deque[float] = deque(maxlen=history_window)
        self._rotation_history: deque[float] = deque(maxlen=history_window)
        self._kl_history: deque[float] = deque(maxlen=history_window)
        self._phase_label: str = "UNCERTAIN"
        self._phase_confidence: float = 0.0
        self._last_indicators: dict = {}

    # ── Public API ───────────────────────────────────────────────────

    def update(self,
               eigenvalue_data: Optional[dict] = None,
               cross_asset_angle: Optional[float] = None,
               kl_max: Optional[float] = None,
               covariance_inflated: bool = False,
               governor_confidence_override: Optional[float] = None) -> dict:
        """Compute 4 spectral indicators and classify phase.

        Parameters
        ----------
        eigenvalue_data : dict, optional
            Output from TimeSeriesAligner.compute_eigenvalues()
            or .eigenvalue_spread(). Must contain at least
            ``lambda_max``, ``lambda_ratio``, ``spectral_entropy``.
        cross_asset_angle : float, optional
            Angle in degrees between VNINDEX and ES=F leading
            eigenvectors. Computed externally via
            ``compute_cross_asset_rotation()``.
        kl_max : float, optional
            Current max KL divergence from BPIMM (between NORMAL
            and STRESS components).
        covariance_inflated : bool, default False
            True if the correlation matrix was inflated due to
            is_stale = 1 rows in the macro data feed.
        governor_confidence_override : float, optional
            Direct override for governor_confidence (e.g. set to 0.0
            when Covariance Inflation Protocol is active).

        Returns
        -------
        dict with keys:
            phase_label, confidence, indicators (4 sub-dicts),
            vote_tally, governor_confidence, covariance_inflated
        """
        ind = {}

        # 1. Eigenvalue spread trajectory
        if eigenvalue_data:
            self._eigen_history.append(eigenvalue_data)
            self._entropy_history.append(eigenvalue_data.get("spectral_entropy", 0.0))
            ind["eigenvalue_spread"] = self._compute_eigen_trajectory()
            ind["entropy_slope"] = self._compute_entropy_slope()
        else:
            ind["eigenvalue_spread"] = {"vote": 0}
            ind["entropy_slope"] = {"vote": 0}

        # 2. Cross-asset eigenvector rotation
        if cross_asset_angle is not None:
            self._rotation_history.append(cross_asset_angle)
            ind["cross_asset_rotation"] = self._compute_rotation_vote(cross_asset_angle)
        else:
            ind["cross_asset_rotation"] = {"vote": 0}

        # 3. KL divergence regime spread
        if kl_max is not None:
            self._kl_history.append(kl_max)
            ind["kl_divergence"] = self._compute_kl_vote(kl_max)
        else:
            ind["kl_divergence"] = {"vote": 0}

        # 4. Weighted vote → classification
        tally = self._weighted_vote(ind)

        # Apply governance confidence override (Covariance Inflation → confidence 0)
        if governor_confidence_override is not None:
            self.governor_confidence = governor_confidence_override
        elif covariance_inflated:
            self.governor_confidence = 0.0
        else:
            self.governor_confidence = 1.0

        phase = self._resolve_phase(tally)

        self._last_indicators = ind
        self._phase_label = phase["label"]
        self._phase_confidence = phase["confidence"]

        return {
            "phase_label": self._phase_label,
            "confidence": round(self._phase_confidence, 4),
            "indicators": ind,
            "vote_tally": tally,
            "governor_confidence": round(self.governor_confidence, 4),
            "covariance_inflated": covariance_inflated,
        }

    def reset(self) -> None:
        """Clear all history."""
        self._eigen_history.clear()
        self._entropy_history.clear()
        self._rotation_history.clear()
        self._kl_history.clear()
        self._phase_label = "UNCERTAIN"
        self._phase_confidence = 0.0
        self._last_indicators = {}

    @property
    def phase_label(self) -> str:
        return self._phase_label

    @property
    def phase_confidence(self) -> float:
        return self._phase_confidence

    def set_governor_confidence(self, level: float) -> None:
        """Force governor confidence to a specific level (e.g. 0.0 on stale)."""
        self.governor_confidence = round(max(0.0, min(1.0, level)), 4)

    # ── Indicator 1: Eigenvalue Spread Trajectory ───────────────────

    def _compute_eigen_trajectory(self) -> dict:
        """Analyze λ₁/λ₂ trend and λ₁ velocity.

        Vote: -1 = distribution, +1 = bottom, 0 = neutral.
        """
        if len(self._eigen_history) < 3:
            return {"vote": 0, "reason": "insufficient_history"}

        current = self._eigen_history[-1]
        prev = self._eigen_history[-2]
        first = self._eigen_history[0]

        lambda_max_now = current.get("lambda_max", 0.0)
        lambda_ratio_now = current.get("lambda_ratio", 1.0)
        lambda_ratio_prev = prev.get("lambda_ratio", 1.0)
        lambda_ratio_start = first.get("lambda_ratio", 1.0)

        # λ₁ velocity: first difference of lambda_max
        lambda_velocity = lambda_max_now - self._eigen_history[-2].get("lambda_max", 0.0)

        vote = 0
        reasons = []

        # Distribution signals
        if lambda_ratio_now > LAMBDA_RATIO_DIST_LOWER and lambda_velocity > 0.01:
            vote -= 1
            reasons.append(f"ratio={lambda_ratio_now:.1f}>5.0 + λ↑")
        elif lambda_ratio_now > LAMBDA_RATIO_DIST_LOWER:
            vote -= 1
            reasons.append(f"ratio={lambda_ratio_now:.1f}>5.0")

        # Bottom signals
        ratio_trend = lambda_ratio_now - lambda_ratio_start
        if (lambda_ratio_now <= LAMBDA_RATIO_BOTTOM_UPPER
                and ratio_trend < -0.1
                and lambda_velocity < 0.01):
            vote += 1
            reasons.append(f"ratio={lambda_ratio_now:.1f} converging (Δ={ratio_trend:+.2f})")
        elif lambda_ratio_now <= LAMBDA_RATIO_BOTTOM_UPPER:
            vote += 0  # neutral — ratio low but not yet converging
            reasons.append(f"ratio={lambda_ratio_now:.1f} low, awaiting convergence")

        return {
            "vote": vote,
            "lambda_max": round(lambda_max_now, 4),
            "lambda_ratio": round(lambda_ratio_now, 4),
            "lambda_velocity": round(lambda_velocity, 4),
            "ratio_trend": round(ratio_trend, 4),
            "reasons": reasons,
        }

    # ── Indicator 2: Spectral Entropy Slope ─────────────────────────

    def _compute_entropy_slope(self) -> dict:
        """Compute d(entropy)/dt via linear regression over window.

        Vote: -1 = distribution (entropy rising), +1 = bottom (entropy falling).
        """
        if len(self._entropy_history) < 5:
            return {"vote": 0, "reason": "insufficient_history"}

        x = np.arange(len(self._entropy_history))
        y = np.array(self._entropy_history)
        slope, _ = np.polyfit(x, y, 1)

        vote = 0
        label = "neutral"

        if slope > ENTROPY_SLOPE_DIST:
            vote = -1
            label = "fragmenting"
        elif slope < ENTROPY_SLOPE_BOTTOM:
            vote = 1
            label = "reforming"

        return {
            "vote": vote,
            "slope": round(float(slope), 6),
            "current_entropy": round(float(y[-1]), 4),
            "label": label,
        }

    # ── Indicator 3: Cross-asset Eigenvector Rotation ───────────────

    @staticmethod
    def compute_cross_asset_rotation(vn_corr: np.ndarray,
                                     combined_corr: np.ndarray) -> float:
        """Compute angle between leading eigenvectors of VN-only vs VN+ES=F.

        Parameters
        ----------
        vn_corr : np.ndarray (n_vn, n_vn)
            Correlation matrix of VNINDEX constituents only.
        combined_corr : np.ndarray (n_total, n_total)
            Correlation matrix of VNINDEX + ES=F + other global assets.

        Returns
        -------
        Angle in degrees [0, 90].

        Notes
        -----
        HOTFIX 2026-07-10: Handle n_vn=1 degeneracy. When VN subspace is
        1-dimensional (VNINDEX only), the leading eigenvector is trivially
        [1.0] and projection + renormalization always gives cos=1.0 → 0°.

        Fix: Use the VN loading on the combined eigenvector directly as
        the cosine (its absolute value), which gives meaningful angle via
        arccos(|evec_comb[0]|) = angle between VN and dominant Asia factor.
        """
        _, vecs_vn = np.linalg.eigh(vn_corr)
        _, vecs_comb = np.linalg.eigh(combined_corr)

        # Leading eigenvector (largest eigenvalue → last column)
        evec_vn = vecs_vn[:, -1]
        evec_comb = vecs_comb[:, -1]

        n_vn = vn_corr.shape[0]

        if n_vn == 1:
            # 1D subspace: eigenvector = [1.0]. The projection is just the
            # VN loading (first element) on the combined eigenvector.
            # cos = |evec_comb[0]|, angle = arccos(cos) ∈ [0, 90].
            cos_angle = float(np.clip(abs(evec_comb[0]), 0.0, 1.0))
            if cos_angle >= 1.0:
                return 0.0
            angle_deg = float(np.degrees(np.arccos(cos_angle)))
            return round(angle_deg, 2)

        # Multi-dimensional VN subspace: project combined eigenvector
        # onto VN dimension and compute rotation angle.
        evec_comb_vn = evec_comb[:n_vn]
        evec_comb_vn = evec_comb_vn / max(np.linalg.norm(evec_comb_vn), 1e-10)
        evec_vn = evec_vn / max(np.linalg.norm(evec_vn), 1e-10)

        cos_angle = float(np.clip(np.dot(evec_vn, evec_comb_vn), -1.0, 1.0))
        angle_deg = float(np.degrees(np.arccos(abs(cos_angle))))

        return round(angle_deg, 2)

    def _compute_rotation_vote(self, angle_deg: float) -> dict:
        """Vote: -1 = distribution (angle widening), +1 = bottom (angle narrowing)."""
        vote = 0
        label = "neutral"

        if angle_deg > ROTATION_ANGLE_DIST:
            vote = -1
            label = "diverging"
        elif angle_deg < ROTATION_ANGLE_BOTTOM:
            vote = 1
            label = "converging"

        return {
            "vote": vote,
            "angle_deg": angle_deg,
            "label": label,
        }

    # ── Indicator 4: KL Divergence Regime Spread ────────────────────

    def _compute_kl_vote(self, kl_max: float) -> dict:
        """Vote: -1 = diverging regimes, +1 = converging regimes."""
        vote = 0
        label = "neutral"

        if kl_max > KL_DIVERGING:
            vote = -1
            label = "diverging"
        elif kl_max < KL_CONVERGING:
            vote = 1
            label = "converging"

        return {
            "vote": vote,
            "kl_max": round(kl_max, 4),
            "label": label,
        }

    # ── Weighted Voting → Phase Resolution ──────────────────────────

    def _weighted_vote(self, indicators: dict) -> dict:
        """Compute weighted distribution vs bottom score.

        Returns dict with:
            dist_weight:  sum of weights where vote == -1
            bottom_weight: sum of weights where vote == +1
            net: dist_weight - bottom_weight (positive = distribution)
        """
        dist_w = 0.0
        bottom_w = 0.0
        details = []

        for name, weight in VOTE_WEIGHTS.items():
            ind = indicators.get(name, {})
            vote = ind.get("vote", 0)
            if vote < 0:
                dist_w += weight
                details.append(f"{name}: DIST(-1) w={weight:.2f}")
            elif vote > 0:
                bottom_w += weight
                details.append(f"{name}: BOTTOM(+1) w={weight:.2f}")
            else:
                details.append(f"{name}: NEUTRAL(0) w={weight:.2f}")

        total = dist_w + bottom_w
        if total < 1e-6:
            return {"net": 0.0, "dist_weight": 0.0, "bottom_weight": 0.0, "details": details}

        return {
            "net": round(dist_w - bottom_w, 4),
            "dist_weight": round(dist_w, 4),
            "bottom_weight": round(bottom_w, 4),
            "details": details,
        }

    def _resolve_phase(self, tally: dict) -> dict:
        """Map weighted vote to final classification."""
        net = tally["net"]
        dist_w = tally["dist_weight"]
        bottom_w = tally["bottom_weight"]
        max_w = max(dist_w, bottom_w)

        if max_w < CLASSIFY_THRESHOLD:
            return {"label": "UNCERTAIN", "confidence": round(max_w, 4)}

        if net > 0 and dist_w >= CLASSIFY_THRESHOLD:
            return {"label": "SILENT_DISTRIBUTION_HEAD", "confidence": round(dist_w, 4)}
        elif net < 0 and bottom_w >= CLASSIFY_THRESHOLD:
            return {"label": "RE_ACCUMULATION_BOTTOM", "confidence": round(bottom_w, 4)}
        else:
            return {"label": "UNCERTAIN", "confidence": round(max_w, 4)}
