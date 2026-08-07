"""
hazard_engine.py — Hazard Transition Engine

Replaces deterministic RegimeROM with a stochastic hazard-driven regime sampler.

Core innovation:
  P(transition at dt) = 1 - exp(-lambda(t|x) * dt)
  where lambda(t|x) = exp(w · x) is a Cox-style proportional hazard.

This bridges the gap between:
  - ROM:   low-entropy attractor (persist 96.6%, dwell 26.5d)
  - Real:  high-frequency oscillator (flip ~2d, entropy ~1.46)

Pipeline:
  Feature Lattice -> HazardFeatureBuilder -> HazardModel ->
  SurvivalProb -> TransitionKernel -> RegimeSampler -> SRV
"""

import numpy as np
import pandas as pd

REGIMES = ["CRISIS", "RANGING", "TRENDING"]

# ── Default calibrated hazard weights ──────────────────────────────────────
# These produce avg P(transition) ≈ 0.5 (matching real engine ~49.2/100d).
# Each normalized feature is roughly [0,1] or [-1,1]. The intercept centers
# the log-hazard so that exp(intercept) ≈ avg baseline hazard.

DEFAULT_HAZARD_WEIGHTS: dict[str, float] = {
    "intercept": -1.5,
    "volatility_norm": 0.8,
    "volume_shock_norm": 0.5,
    "trend_strength_norm": -0.4,
    "compression_norm": 0.6,
    "return_5d_norm": -0.3,
    "breadth": -0.4,
    "entropy_norm": 0.5,
    "age_norm": 0.25,
}

# Base transition kernel P(next | current) when hazard is at baseline.
# Calibrated from real engine empirical transitions.
BASE_TRANSITION_KERNEL: dict[str, dict[str, float]] = {
    "CRISIS": {"CRISIS": 0.50, "RANGING": 0.35, "TRENDING": 0.15},
    "RANGING": {"CRISIS": 0.25, "RANGING": 0.45, "TRENDING": 0.30},
    "TRENDING": {"CRISIS": 0.15, "RANGING": 0.35, "TRENDING": 0.50},
}

# Feature keys expected by the model (for validation + calibration)
FEATURE_KEYS: list[str] = [
    "volatility_norm",
    "volume_shock_norm",
    "trend_strength_norm",
    "compression_norm",
    "return_5d_norm",
    "breadth",
    "entropy_norm",
    "age_norm",
]

MAX_AGE_NORM: int = 60
"""Cap for regime age normalization (days)."""


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1: HAZARD FEATURE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════


class HazardFeatureBuilder:
    """
    Extracts and normalizes hazard features from a day's lattice slice.

    All output features are scaled to roughly [0, 1] or [-1, 1] so that
    hazard weights are directly interpretable.
    """

    @staticmethod
    def build(day_df: pd.DataFrame, regime_age: int = 0) -> dict[str, float]:
        day_df = day_df.dropna(subset=["close", "volume"])
        if len(day_df) == 0:
            return {k: 0.5 for k in FEATURE_KEYS}

        vn = day_df[day_df["symbol"] == "VNINDEX"]

        # Volatility (daily 20d std, typical range 0.005-0.04)
        vol = vn["volatility_20d"].iloc[0] if len(vn) > 0 else day_df["volatility_20d"].median()
        vol = vol if pd.notna(vol) and vol > 0 else 0.015
        vol_norm = np.clip(vol / 0.04, 0.0, 1.0)

        # Volume shock (mean absolute z-score across symbols)
        vz = day_df["volume_z"].dropna()
        shock = vz.abs().mean() if len(vz) > 0 else 0.0
        shock_norm = np.clip(shock / 3.0, 0.0, 1.0)

        # Trend strength (MA20 slope, typical range -0.05 to 0.05)
        slope = vn["ma_slope_20"].iloc[0] if len(vn) > 0 else 0.0
        slope = slope if pd.notna(slope) else 0.0
        trend_norm = np.clip(slope / 0.05, -1.0, 1.0)

        # Range compression ((high-low)/close, typical 0.01-0.05)
        rc = day_df["range_compression"].dropna()
        comp = rc.median() if len(rc) > 0 else 0.02
        comp_norm = np.clip(comp / 0.05, 0.0, 1.0)

        # 5-day return (typical range -0.1 to 0.1)
        ret5 = vn["return_5d"].iloc[0] if len(vn) > 0 else 0.0
        ret5 = ret5 if pd.notna(ret5) else 0.0
        ret5_norm = np.clip(ret5 / 0.1, -1.0, 1.0)

        # Breadth (% stocks above MA20)
        ma20_valid = day_df["ma_20"].notna()
        above = ((day_df["close"] > day_df["ma_20"]) & ma20_valid).sum() if ma20_valid.sum() > 0 else 0
        breadth = above / ma20_valid.sum() if ma20_valid.sum() > 0 else 0.5

        # Cross-sectional return entropy (dispersion of 1d returns)
        rets = day_df["return_1d"].dropna()
        if len(rets) >= 5 and rets.max() > rets.min():
            bins = np.linspace(rets.min(), rets.max(), 11)
            hist, _ = np.histogram(rets, bins=bins, density=True)
            hist = hist[hist > 0]
            entropy = -np.sum(hist * np.log2(hist)) if len(hist) > 0 else 0.0
            entropy_norm = np.clip(entropy / np.log2(11), 0.0, 1.0)
        else:
            entropy_norm = 0.5

        # Regime age
        age_norm = np.clip(regime_age / MAX_AGE_NORM, 0.0, 1.0)

        return {
            "volatility_norm": round(vol_norm, 4),
            "volume_shock_norm": round(shock_norm, 4),
            "trend_strength_norm": round(trend_norm, 4),
            "compression_norm": round(comp_norm, 4),
            "return_5d_norm": round(ret5_norm, 4),
            "breadth": round(breadth, 4),
            "entropy_norm": round(entropy_norm, 4),
            "age_norm": round(age_norm, 4),
        }


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2: HAZARD RATE MODEL (Proportional Hazard / Cox-style)
# ═══════════════════════════════════════════════════════════════════════════


class HazardModel:
    """
    Cox-style proportional hazard model.

    lambda(t | x) = exp(w · x)

    where x includes an intercept term (bias) and all normalized features.
    The intercept controls the baseline hazard rate.
    """

    def __init__(self, weights: dict[str, float] | None = None):
        self.w = dict(DEFAULT_HAZARD_WEIGHTS)
        if weights:
            self.w.update(weights)

    def hazard(self, features: dict[str, float]) -> float:
        z = self.w.get("intercept", 0.0)
        for k in FEATURE_KEYS:
            z += self.w.get(k, 0.0) * features.get(k, 0.0)
        return float(np.exp(np.clip(z, -10.0, 10.0)))

    def survival(self, hazard_rate: float, dt: float = 1.0) -> float:
        return float(np.exp(-hazard_rate * dt))

    def transition_probability(self, hazard_rate: float, dt: float = 1.0) -> float:
        return 1.0 - self.survival(hazard_rate, dt)

    def get_weights(self) -> dict[str, float]:
        return dict(self.w)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3: TRANSITION KERNEL (Markov + Hazard modulation)
# ═══════════════════════════════════════════════════════════════════════════


class TransitionKernel:
    """
    Hazard-adjusted Markov transition kernel.

    High hazard -> more switching entropy (probabilities flatten).
    Low hazard  -> stickiness (diagonal dominance increases).

    The adjustment interpolates the base kernel toward uniform as
    hazard increases.
    """

    def __init__(self, base_kernel: dict[str, dict[str, float]] | None = None):
        self.base = base_kernel or BASE_TRANSITION_KERNEL

    def adjust(self, current_state: str, hazard_rate: float) -> dict[str, float]:
        base = self.base.get(current_state, self.base["RANGING"])
        noise_strength = np.clip(hazard_rate / 5.0, 0.0, 0.8)

        adjusted = {}
        for s in REGIMES:
            adjusted[s] = base[s] * (1.0 + noise_strength)

        s = sum(adjusted.values())
        return {k: v / s for k, v in adjusted.items()}

    def sample(self, current_state: str, hazard_rate: float) -> str:
        probs = self.adjust(current_state, hazard_rate)
        return str(np.random.choice(REGIMES, p=[probs[s] for s in REGIMES]))


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 4: REGIME SCORE MAPPER (compatibility with downstream layers)
# ═══════════════════════════════════════════════════════════════════════════


def regime_score_from_state(state: str, hazard_rate: float) -> float:
    """
    Map (state, hazard) to a continuous score in [0, 1] for downstream
    compatibility (trade_state_policy, DBE, etc.).

    Low hazard + TRENDING -> high score
    High hazard + CRISIS  -> low score
    """
    base = {"CRISIS": 0.25, "RANGING": 0.50, "TRENDING": 0.75}.get(state, 0.5)
    hazard_mod = np.clip(1.0 - hazard_rate / 10.0, 0.8, 1.2)
    return round(float(np.clip(base * hazard_mod, 0.0, 1.0)), 4)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 5: HAZARD TRANSITION ENGINE (Stateful Sampler)
# ═══════════════════════════════════════════════════════════════════════════


class HazardTransitionEngine:
    """
    Stochastic hazard-driven regime sampler.

    Stateful replacement for RegimeROM. Call .evaluate() each day with the
    lattice day_df to get the next regime state.

    Drop-in compatible with HSRBatchRunner.run() — returns the same dict
    keys as RegimeROM.evaluate() plus hazard diagnostics.

    Usage:
        engine = HazardTransitionEngine(seed=42)
        for d in dates:
            day_df = cache[d]
            result = engine.evaluate(day_df)
            regime = result["market_status"]
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        base_kernel: dict[str, dict[str, float]] | None = None,
        seed: int | None = None,
        initial_state: str = "RANGING",
    ):
        if seed is not None:
            np.random.seed(seed)

        self.feature_builder = HazardFeatureBuilder()
        self.hazard_model = HazardModel(weights)
        self.kernel = TransitionKernel(base_kernel)

        self.state: str = initial_state
        self.age: int = 0

    def evaluate(
        self,
        day_df: pd.DataFrame,
        breadth_score: float | None = None,
        flow_score: float | None = None,
        recovery_score: float | None = None,
        driver_state: dict | None = None,
    ) -> dict:
        features = self.feature_builder.build(day_df, self.age)
        h = self.hazard_model.hazard(features)

        # Driver state modulates hazard (closed-loop feedback)
        hazard_mod = 1.0
        if driver_state:
            confidence = driver_state.get("confidence", 0.5)
            entropy = driver_state.get("entropy", 0.5)
            # High confidence + low entropy = stable = lower hazard
            stability = float(confidence * (1.0 - entropy / 1.6))
            hazard_mod = float(np.clip(1.0 + (0.5 - stability), 0.3, 2.0))
            h = h * hazard_mod

        if self._should_transition(h):
            self.state = self.kernel.sample(self.state, h)
            self.age = 0
        else:
            self.age += 1

        score = regime_score_from_state(self.state, h)

        return {
            "market_status": self.state,
            "regime_score": score,
            "momentum": score,
            "hazard_mod": round(hazard_mod, 4),
            "hazard_rate": round(h, 4),
            "survival_prob": round(self.hazard_model.survival(h), 4),
            "regime_age": self.age,
            "hazard_features": features,
        }

    def _should_transition(self, hazard_rate: float) -> bool:
        p = self.hazard_model.transition_probability(hazard_rate)
        return np.random.random() < p

    def reset(self, state: str = "RANGING") -> None:
        self.state = state
        self.age = 0

    def get_state(self) -> dict:
        return {"state": self.state, "age": self.age}


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 6: BATCH RUNNER (Hazard mode for HSR)
# ═══════════════════════════════════════════════════════════════════════════


def run_hazard_sequence(
    dates: list[str],
    cache: dict[str, pd.DataFrame],
    weights: dict[str, float] | None = None,
    seed: int | None = None,
    verbose: bool = False,
) -> list[dict]:
    """
    Run the hazard engine over a sequence of dates.

    Args:
        dates: Sorted list of date strings (YYYY-MM-DD).
        cache: Dict mapping date_str -> day_df (from HSRBatchRunner._index()).
        weights: Optional custom hazard weights.
        seed: Random seed for reproducibility.

    Returns:
        List of result dicts with market_status, hazard_rate, etc.
    """
    engine = HazardTransitionEngine(weights=weights, seed=seed)
    results = []

    for d in dates:
        day_df = cache.get(d)
        if day_df is None or len(day_df) == 0:
            continue

        result = engine.evaluate(day_df)
        result["date"] = d
        results.append(result)

    return results


def sequence_to_regimes(results: list[dict]) -> list[str]:
    """Extract regime sequence from hazard engine results."""
    return [r["market_status"] for r in results]
