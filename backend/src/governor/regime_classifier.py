"""regime_classifier.py — HMM 3-State Regime Classifier (First Principles).

LAW-010 (Regime Invariance Principle):
  "Moi bang chung tai chinh khong duoc danh gia gia tri tuyet doi;
   bat buoc chuan hoa theo phan phoi cua Regime tai thoi diem quan sat."

  Score(x) = F(x | Regime_t)

Architecture (First Principles):
  1. Stationary Feature Extraction:
     - Δr_ON: First difference of interbank ON rates (removes trend)
     - Z-score OMO: Standardized OMO proxy (monetary policy stance)
     - USD/VND_Dev_MA90: Deviation from 90-day MA (mean-reverting FX)
  2. 3-State Gaussian HMM:
     - K=3 states: EXPANSION, NORMAL, CONTRACTION
     - covariance_type="full" for capturing correlations
     - reg_covar=1e-6 for overfitting protection
  3. Probability Smoothing:
     - Exponential moving average (EMA) of probability vectors
     - Temperature scaling for softer transitions
  4. Output: Fuzzy probability vector P(Regime) ∈ [0,1]^3

Overfitting Protection:
  - K=3 fixed (no model selection needed)
  - reg_covar regularization on covariance matrices
  - BIC monitoring for model complexity
  - Minimum 60 samples for stable fit

Boundary Continuity:
  - EMA smoothing prevents abrupt probability jumps
  - Temperature scaling softens hard boundaries
  - Fuzzy weighting ensures smooth transitions

Usage:
  classifier = RegimeClassifier(db_path)
  result = classifier.classify(target_date="2026-08-03")
  print(result.probabilities)  # {'EXPANSION': 0.1, 'NORMAL': 0.75, 'CONTRACTION': 0.15}
"""

import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
from numpy.linalg import LinAlgError

if TYPE_CHECKING:
    from hmmlearn.hmm import GaussianHMM

logger = logging.getLogger(__name__)


def _hydrate_path() -> Path:
    """Path Hydrator v2.1: Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()

# ── Regime Labels ─────────────────────────────────────────────────────
REGIME_LABELS = ["EXPANSION", "NORMAL", "CONTRACTION"]

# ── Regime-Specific Historical P10/P90 Bounds ─────────────────────────
# Derived from VN interbank history across multiple cycles (2011-2026).
REGIME_BOUNDS = {
    "EXPANSION": {"p10": 2.5, "p90": 4.8},  # cheap money era
    "NORMAL": {"p10": 3.5, "p90": 6.2},  # neutral cycle
    "CONTRACTION": {"p10": 4.5, "p90": 9.5},  # tightening / crisis
}

# ── Fallback when HMM unavailable ─────────────────────────────────────
DEFAULT_REGIME_PROBS = {"EXPANSION": 0.2, "NORMAL": 0.6, "CONTRACTION": 0.2}

# ── HMM Parameters ────────────────────────────────────────────────────
HMM_N_STATES = 3
HMM_MIN_SAMPLES = 60  # minimum for stable HMM fit
HMM_MAX_ITER = 100
HMM_RANDOM_STATE = 42
HMM_REG_COVAR = 1e-2  # min_covar: floor on covariance diagonal to prevent overfitting

# ── Probability Smoothing Parameters ──────────────────────────────────
EMA_ALPHA = 0.1  # EMA smoothing factor (lower = smoother)
TEMPERATURE = 1.5  # temperature scaling (higher = softer)
SMOOTHING_WINDOW = 5  # rolling window for probability averaging

# ── Stationary Feature Parameters ─────────────────────────────────────
DIFF_LAG = 1  # first difference lag
MA_WINDOW = 90  # moving average window for USD/VND
ZSCORE_WINDOW = 60  # z-score standardization window


@dataclass
class RegimeResult:
    """Result of regime classification."""

    regime: str  # Most likely regime label
    probabilities: dict  # P(EXPANSION), P(NORMAL), P(CONTRACTION)
    interbank_avg_90d: float  # 90-day rolling average
    confidence: float  # max probability (higher = more certain)
    hmm_fitted: bool  # whether HMM was actually fitted
    features_used: list[str] | None = None  # stationary features used
    bic_score: float | None = None  # BIC score for model quality


class RegimeClassifier:
    """Classify interest rate regime using 3-state Gaussian HMM.

    First Principles Architecture:
      1. Extract stationary features from macro history
      2. Fit 3-state HMM with regularization
      3. Smooth probability vectors via EMA
      4. Return fuzzy regime probabilities

    LAW-10: All observations are evaluated conditional on regime:
      Score(x) = F(x | P(Regime_k))
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")

    # ── Data Fetching ─────────────────────────────────────────────────

    def _fetch_macro_series(self, variable: str, target_date: str | None = None, limit: int = 1250) -> np.ndarray:
        """Fetch a macro variable time series as numpy array (oldest first)."""
        conn = sqlite3.connect(self.db_path)
        try:
            if target_date:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? AND date <= ? ORDER BY date DESC LIMIT ?",
                    (variable, target_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT ?",
                    (variable, limit),
                ).fetchall()
            values = [r[0] for r in rows if r[0] is not None]
            return np.array(values[::-1])  # oldest first
        except sqlite3.Error:
            return np.array([])
        finally:
            conn.close()

    def _fetch_interbank_history(self, target_date: str | None = None, limit: int = 1250) -> np.ndarray:
        """Fetch interbank ON rates."""
        return self._fetch_macro_series("INTERBANK_ON", target_date, limit)

    def _fetch_usdvnd_history(self, target_date: str | None = None, limit: int = 1250) -> np.ndarray:
        """Fetch USD/VND exchange rates."""
        return self._fetch_macro_series("USD_VND", target_date, limit)

    # ── Stationary Feature Extraction ─────────────────────────────────

    def _compute_delta_r(self, interbank: np.ndarray) -> np.ndarray:
        """Compute Δr_ON: first difference of interbank rates.

        Removes trend from non-stationary interest rate series.
        Δr_t = r_t - r_{t-1}
        """
        if len(interbank) < DIFF_LAG + 1:
            return np.array([])
        return np.diff(interbank, n=DIFF_LAG)

    def _compute_zscore_omo(self, interbank: np.ndarray) -> np.ndarray:
        """Compute Z-score of OMO proxy from interbank rates.

        OMO proxy = -(interbank - 3.0) * 10000
        Z-score standardizes to mean=0, std=1 over rolling window.
        """
        if len(interbank) < ZSCORE_WINDOW:
            return np.array([])

        # OMO proxy: negative relationship with interbank
        omo_proxy = -(interbank - 3.0) * 10000

        # Rolling z-score
        zscores = np.full_like(omo_proxy, np.nan)
        for i in range(ZSCORE_WINDOW, len(omo_proxy)):
            window = omo_proxy[i - ZSCORE_WINDOW : i]
            mean = np.mean(window)
            std = np.std(window)
            if std > 1e-10:
                zscores[i] = (omo_proxy[i] - mean) / std
            else:
                zscores[i] = 0.0

        # Fill initial NaN with 0
        zscores[:ZSCORE_WINDOW] = 0.0
        return zscores

    def _compute_usdvnd_deviation(self, usdvnd: np.ndarray) -> np.ndarray:
        """Compute USD/VND_Dev_MA90: deviation from 90-day moving average.

        Mean-reverting feature: positive when USD/VND > MA90 (depreciation),
        negative when USD/VND < MA90 (appreciation).
        """
        if len(usdvnd) < MA_WINDOW:
            return np.array([])

        deviations = np.full_like(usdvnd, np.nan)
        for i in range(MA_WINDOW, len(usdvnd)):
            ma90 = np.mean(usdvnd[i - MA_WINDOW : i])
            if ma90 > 1e-10:
                deviations[i] = (usdvnd[i] - ma90) / ma90
            else:
                deviations[i] = 0.0

        # Fill initial NaN with 0
        deviations[:MA_WINDOW] = 0.0
        return deviations

    def _extract_stationary_features(self, target_date: str | None = None) -> tuple[np.ndarray, list[str]]:
        """Extract stationary features for HMM input.

        Returns:
            features: (n_samples, n_features) array
            feature_names: list of feature names
        """
        interbank = self._fetch_interbank_history(target_date, limit=1250)
        usdvnd = self._fetch_usdvnd_history(target_date, limit=1250)

        features = []
        feature_names = []

        # Feature 1: Δr_ON (first difference)
        delta_r = self._compute_delta_r(interbank)
        if len(delta_r) > 0:
            features.append(delta_r)
            feature_names.append("delta_r_on")

        # Feature 2: Z-score OMO proxy
        zscore_omo = self._compute_zscore_omo(interbank)
        if len(zscore_omo) > 0:
            # Align length with delta_r
            if len(features) > 0:
                min_len = min(len(delta_r), len(zscore_omo))
                features[-1] = features[-1][-min_len:]
                features.append(zscore_omo[-min_len:])
            else:
                features.append(zscore_omo)
            feature_names.append("zscore_omo")

        # Feature 3: USD/VND deviation from MA90
        usdvnd_dev = self._compute_usdvnd_deviation(usdvnd)
        if len(usdvnd_dev) > 0 and len(features) > 0:
            # Align all features to common length
            min_len = min(len(features[0]), len(usdvnd_dev))
            features = [f[-min_len:] for f in features]
            features.append(usdvnd_dev[-min_len:])
            feature_names.append("usdvnd_dev_ma90")

        if not features:
            return np.array([]), []

        # Stack features into (n_samples, n_features) array
        X = np.column_stack(features)

        # Remove any rows with NaN
        valid_mask = ~np.any(np.isnan(X), axis=1)
        X = X[valid_mask]

        return X, feature_names

    # ── HMM Fitting ───────────────────────────────────────────────────

    def _fit_hmm(self, X: np.ndarray) -> tuple[object | None, np.ndarray | None, float | None]:
        """Fit 3-state Gaussian HMM on stationary features.

        Returns:
            model: fitted GaussianHMM or None
            means_sorted: sorted state means or None
            bic: Bayesian Information Criterion or None
        """
        if len(X) < HMM_MIN_SAMPLES:
            logger.warning(
                "[REGIME_CLASSIFIER] Insufficient data (%d < %d) for HMM",
                len(X),
                HMM_MIN_SAMPLES,
            )
            return None, None, None

        try:
            from hmmlearn.hmm import GaussianHMM

            model = GaussianHMM(
                n_components=HMM_N_STATES,
                covariance_type="full",
                n_iter=HMM_MAX_ITER,
                random_state=HMM_RANDOM_STATE,
                min_covar=HMM_REG_COVAR,  # overfitting protection
            )
            model.fit(X)

            # Compute BIC for model quality monitoring
            bic = model.bic(X)

            # Sort states by mean of first feature (Δr_ON)
            # Lowest Δr_ON = EXPANSION (low rate changes)
            # Highest Δr_ON = CONTRACTION (high rate changes)
            means = model.means_[:, 0]  # first feature means
            order = np.argsort(means)

            # Bypass property setters to avoid validation during reorder
            model.means_ = model.means_[order]
            model._covars_ = model._covars_[order]  # type: ignore[attr-defined]
            if hasattr(model, "startprob_"):
                model.startprob_ = model.startprob_[order]
            if hasattr(model, "transmat_"):
                model.transmat_ = model.transmat_[order][:, order]

            return model, means[order], bic

        except (ValueError, RuntimeError, LinAlgError) as e:
            logger.warning("[REGIME_CLASSIFIER] HMM fit failed: %s", e)
            return None, None, None

    # ── Probability Smoothing ─────────────────────────────────────────

    def _apply_temperature_scaling(self, probs: np.ndarray, temperature: float = TEMPERATURE) -> np.ndarray:
        """Apply temperature scaling to soften probability distribution.

        Higher temperature → softer distribution (more uniform).
        Lower temperature → harder distribution (more peaked).
        """
        if temperature <= 0:
            return probs

        # Scale logits by temperature
        logits = np.log(probs + 1e-10) / temperature
        scaled = np.exp(logits)
        return cast(np.ndarray, scaled / np.sum(scaled))

    def _smooth_probability_vector(self, current_probs: np.ndarray, history: list[np.ndarray] | None = None) -> np.ndarray:
        """Smooth probability vector using EMA and rolling average.

        Prevents abrupt jumps at regime boundaries.
        """
        if history is None or len(history) == 0:
            return current_probs

        # EMA smoothing
        ema = current_probs
        for prev_probs in reversed(history[-SMOOTHING_WINDOW:]):
            ema = EMA_ALPHA * ema + (1 - EMA_ALPHA) * prev_probs

        # Rolling average
        recent = history[-SMOOTHING_WINDOW:] + [current_probs]
        rolling = np.mean(recent, axis=0)

        # Blend EMA and rolling average
        smoothed = 0.7 * ema + 0.3 * rolling

        # Normalize to sum to 1
        total = np.sum(smoothed)
        if total > 1e-10:
            smoothed = smoothed / total
        else:
            smoothed = current_probs

        return cast(np.ndarray, smoothed)

    # ── Main Classification ───────────────────────────────────────────

    def classify(
        self,
        target_date: str | None = None,
        prob_history: list[np.ndarray] | None = None,
    ) -> RegimeResult:
        """Classify current regime and return fuzzy probabilities.

        Args:
            target_date: PIT date for backtest safety
            prob_history: optional history of probability vectors for smoothing

        Returns:
            RegimeResult with fuzzy probability vector

        First Principles:
          1. Extract stationary features
          2. Fit HMM with regularization
          3. Compute posterior probabilities
          4. Smooth via EMA + temperature scaling
          5. Return P(Regime_k) ∈ [0,1]^3
        """
        # Extract stationary features
        X, feature_names = self._extract_stationary_features(target_date)

        # Compute avg_90d from RAW interbank (not from Δr features)
        interbank = self._fetch_interbank_history(target_date, limit=1250)
        avg_90d = (
            float(np.mean(interbank[-90:]))
            if len(interbank) >= 90
            else (float(np.mean(interbank)) if len(interbank) > 0 else 0.0)
        )

        # Fallback: use raw interbank if no features
        if len(X) == 0:
            regime_probs = _heuristic_classify(avg_90d)
            regime = max(regime_probs, key=lambda k: regime_probs.get(k, 0.0))
            return RegimeResult(
                regime=regime,
                probabilities=regime_probs,
                interbank_avg_90d=avg_90d,
                confidence=max(regime_probs.values()),
                hmm_fitted=False,
                features_used=[],
                bic_score=None,
            )

        # Fit HMM
        model, means, bic = self._fit_hmm(X)
        if model is None:
            regime_probs = _heuristic_classify(avg_90d)
            regime = max(regime_probs, key=lambda k: regime_probs.get(k, 0.0))
            return RegimeResult(
                regime=regime,
                probabilities=regime_probs,
                interbank_avg_90d=avg_90d,
                confidence=max(regime_probs.values()),
                hmm_fitted=False,
                features_used=feature_names,
                bic_score=bic,
            )

        try:
            # Compute posterior probabilities for all observations
            posteriors = cast("GaussianHMM", model).predict_proba(X)
            raw_probs = posteriors[-1]  # latest time step

            # Apply temperature scaling
            scaled_probs = self._apply_temperature_scaling(raw_probs)

            # Smooth probability vector
            smoothed_probs = self._smooth_probability_vector(scaled_probs, prob_history)

            # Build result
            regime_probs = {REGIME_LABELS[i]: float(smoothed_probs[i]) for i in range(HMM_N_STATES)}
            regime = max(regime_probs, key=lambda k: regime_probs.get(k, 0.0))
            confidence = float(np.max(smoothed_probs))

            return RegimeResult(
                regime=regime,
                probabilities=regime_probs,
                interbank_avg_90d=avg_90d,
                confidence=confidence,
                hmm_fitted=True,
                features_used=feature_names,
                bic_score=bic,
            )

        except (ValueError, RuntimeError, AttributeError, IndexError) as e:
            logger.warning("[REGIME_CLASSIFIER] HMM predict failed: %s", e)
            regime_probs = _heuristic_classify(avg_90d)
            regime = max(regime_probs, key=lambda k: regime_probs.get(k, 0.0))
            return RegimeResult(
                regime=regime,
                probabilities=regime_probs,
                interbank_avg_90d=avg_90d,
                confidence=max(regime_probs.values()),
                hmm_fitted=False,
                features_used=feature_names,
                bic_score=bic,
            )


def _heuristic_classify(avg_90d: float) -> dict:
    """Heuristic regime classification from 90-day average.

    Used as fallback when HMM cannot be fitted.
    Returns fuzzy probabilities that taper at boundaries.
    """
    probs = {"EXPANSION": 0.0, "NORMAL": 0.0, "CONTRACTION": 0.0}

    if avg_90d <= 3.0:
        probs["EXPANSION"] = 1.0
    elif avg_90d <= 3.5:
        # Taper from EXPANSION to NORMAL
        t = (avg_90d - 3.0) / 0.5
        probs["EXPANSION"] = 1.0 - t
        probs["NORMAL"] = t
    elif avg_90d <= 5.0:
        probs["NORMAL"] = 1.0
    elif avg_90d <= 5.5:
        # Taper from NORMAL to CONTRACTION
        t = (avg_90d - 5.0) / 0.5
        probs["NORMAL"] = 1.0 - t
        probs["CONTRACTION"] = t
    elif avg_90d <= 7.5:
        probs["CONTRACTION"] = 1.0
    else:
        # Deep crisis
        probs["CONTRACTION"] = 1.0

    return probs


def classify_regime(
    target_date: str | None = None,
    db_path: str | None = None,
    prob_history: list[np.ndarray] | None = None,
) -> RegimeResult:
    """Convenience function."""
    return RegimeClassifier(db_path).classify(target_date, prob_history)
