"""liquidity_recovery_index.py — Chi So Khoi Phuc Thanh Khoan (LRI) v3.

v3 CHANGELOG (Regime-Aware Bayesian Bound — 2026-08-04):
  Architecture:
    Macro History -> Epoch Detector -> Regime Classifier
    -> Historical Distribution -> Bayesian Bound Estimator -> S_Interbank

  S_Interbank: Regime-Aware Bayesian Bound
    - 4 regimes: LOW_RATE, NORMAL, HIGH_RATE, CRISIS
    - Each regime has its own P10/P90 distribution
    - Bayesian blending: alpha*Observed + beta*Historical + gamma*Policy
    - Policy prior: SBV operating corridor (3.0%-7.5%)
    - Graceful degradation: falls back to Policy when data insufficient

  S_USDVND: MA90 deviation (unchanged from v2)
  PIT: compute(target_date=None) — all queries date-bounded
  Degraded mode: logs [M4_DEGRADED_MODE] when data missing
"""

import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# WHY (Rule 2, namespace hygiene): RELATIVE imports — the absolute
# `from src.governor.*` form only resolves when `backend/` is on sys.path.
# Governor modules are invoked via multiple entry points (API, CLI replay,
# orchestration) where that guarantee does not hold. Relative imports are
# CWD-independent and cannot produce the silent ModuleNotFoundError that
# previously flattened the multi-factor replay to 0 trades.
from .regime_classifier import (
    REGIME_BOUNDS as HMM_REGIME_BOUNDS,
)
from .regime_classifier import (
    RegimeClassifier,
)


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
logger = logging.getLogger(__name__)

# ── Normalization thresholds ─────────────────────────────────────────
# Each component is normalized to [0, 1] where 1 = maximum liquidity (best).

# Interbank ON — Policy Corridor (SBV operating range 2016-2026):
# Used as Bayesian prior gamma when data-driven bounds are insufficient.
INTERBANK_LOW = 3.0  # <= 3% -> score 1.0 (plentiful liquidity)
INTERBANK_HIGH = 7.5  # >= 7.5% -> score 0.0 (tight liquidity crisis)

# USD/VND deviation from MA90: smaller = better
USDVND_DEVIATION_DENOM = 0.02  # 2% deviation -> score 0.0

# OMO net injection: positive = better (injecting liquidity)
OMO_HIGH = 50000.0  # > 50k bn VND net inject -> score 1.0
OMO_LOW = -50000.0  # < -50k bn VND net drain -> score 0.0

# FII net flow: positive = better (foreign buying)
FII_HIGH = 500.0  # > 500 bn VND net buy -> score 1.0
FII_LOW = -2000.0  # < -2000 bn VND net sell -> score 0.0

# Breadth: higher = better (broader participation)
BREADTH_LOW = 30.0  # < 30% -> score 0.0 (narrow)
BREADTH_HIGH = 70.0  # > 70% -> score 1.0 (broad)

# ── Bayesian blending weights ────────────────────────────────────────
# alpha: weight on observed data percentile (grows with sample size)
# beta:  weight on regime-specific historical distribution (fuzzy-weighted)
# gamma: weight on policy prior (SBV corridor)
BAYESIAN_ALPHA_WEIGHT = 0.50
BAYESIAN_GAMMA_WEIGHT = 0.20
MIN_OBS_FOR_DATA = 30
MIN_OBS_FULL_BAYESIAN = 250

# Fallback regime bounds when HMM unavailable (same as regime_classifier)
REGIME_BOUNDS = HMM_REGIME_BOUNDS


def _normalize(value: float, low: float, high: float, invert: bool = False) -> float:
    """Normalize value to [0, 1]. Invert=True for inverse indicators (lower=better)."""
    if value is None:
        return 0.5
    if invert:
        score = (high - value) / (high - low) if high != low else 0.5
    else:
        score = (value - low) / (high - low) if high != low else 0.5
    return max(0.0, min(1.0, score))


@dataclass
class LRIResult:
    """Ket qua tinh LRI."""

    lri: float
    s_interbank: float
    s_usdvnd: float
    s_omo: float
    s_fii: float
    s_breadth: float
    regime: str
    max_allocation_pct: float
    components_raw: dict
    degraded_components: list = field(default_factory=list)


class LiquidityRecoveryIndex:
    """Compute LRI from macro + market data.

    v3: Regime-Aware Bayesian Bound for S_Interbank.
    - Detects current interest rate regime (LOW_RATE/NORMAL/HIGH_RATE/CRISIS)
    - Each regime has its own P10/P90 distribution
    - Bayesian blending: observed data + regime historical + policy prior
    - Naturally adapts across economic decades without hard-coded constants

    Weights:
      S_Interbank: 30% — Phong vu bieu nhay nhat cua thanh khoan he thong
      S_USDVND:    25% — Ap luc rut von ngoai va can thiep SBV
      S_OMO:       20% — Hanh dong truc tiep cua Ngan hang Nha nuoc
      S_FII:       15% — Luc cau ngoai khoi
      S_Breadth:   10% — Su lan toa tren toan san chung khoan
    """

    WEIGHTS = {
        "interbank": 0.30,
        "usdvnd": 0.25,
        "omo": 0.20,
        "fii": 0.15,
        "breadth": 0.10,
    }

    AGGRESSIVE_THRESHOLD = 0.8
    PROBE_THRESHOLD = 0.3

    # ── Exponential Convex Penalty for S_Interbank (tail risk) ──────
    # WHY: S_Interbank saturates at 0.0 when INTERBANK_ON > P90, making
    #      the 30% interbank weight "dead weight" in LRI. The exponential
    #      penalty re-activates this channel for tail risk events (>8%).
    PENALTY_ACTIVATION_GAP = 2.0  # % above P90 to activate penalty
    PENALTY_MAXIMUM_GAP = 4.0  # % above P90 for maximum penalty
    MAX_PENALTY = 0.65  # Maximum negative S_Interbank (allows DEFENSIVE at ~10%)

    MA90_WINDOW = 90
    REGIME_WINDOW = 90  # 90-day rolling avg for regime detection
    EPOCH_WINDOW = 1250  # 5-year window for epoch baseline

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_latest_macro(self, variable: str, target_date: str | None = None) -> float | None:
        """Fetch latest value for a macro variable, optionally bounded by target_date."""
        conn = self._get_conn()
        try:
            if target_date:
                row = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                    (variable, target_date),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1",
                    (variable,),
                ).fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _fetch_ma90_usdvnd(self, target_date: str | None = None) -> float | None:
        """Fetch 90-day MA of USD/VND ending at target_date."""
        conn = self._get_conn()
        try:
            if target_date:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = 'USD_VND' AND date <= ? ORDER BY date DESC LIMIT ?",
                    (target_date, self.MA90_WINDOW),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = 'USD_VND' ORDER BY date DESC LIMIT ?",
                    (self.MA90_WINDOW,),
                ).fetchall()
            if len(rows) < 10:
                return None
            return float(sum(r[0] for r in rows) / len(rows))
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _fetch_interbank_values(self, target_date: str | None = None, limit: int = 1250) -> list:
        """Fetch interbank values for analysis."""
        conn = self._get_conn()
        try:
            if target_date:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = 'INTERBANK_ON' AND date <= ? ORDER BY date DESC LIMIT ?",
                    (target_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = 'INTERBANK_ON' ORDER BY date DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _classify_regime_fuzzy(self, target_date: str | None = None) -> tuple:
        """Classify regime using HMM and return regime posterior probabilities.

        Returns (regime_label, regime_probs_dict, hmm_fitted).
        """
        classifier = RegimeClassifier(self.db_path)
        result = classifier.classify(target_date)
        return result.regime, result.probabilities, result.hmm_fitted

    def _compute_bayesian_bounds_fuzzy(self, values: list, regime_probs: dict) -> tuple:
        """Compute Regime-Weighted Adaptive Bounds using regime probability vector.

        LAW-010: P90_eff = alpha * Observed + beta * sum(P(Regime_k) * Hist_k) + gamma * Policy

        regime_probs: {"EXPANSION": 0.1, "NORMAL": 0.75, "CONTRACTION": 0.15}
        """
        policy_p10, policy_p90 = INTERBANK_LOW, INTERBANK_HIGH

        n = len(values)
        if n < MIN_OBS_FOR_DATA:
            alpha = 0.0
        elif n >= MIN_OBS_FULL_BAYESIAN:
            alpha = BAYESIAN_ALPHA_WEIGHT
        else:
            alpha = BAYESIAN_ALPHA_WEIGHT * (n - MIN_OBS_FOR_DATA) / (MIN_OBS_FULL_BAYESIAN - MIN_OBS_FOR_DATA)

        beta = 1.0 - alpha - BAYESIAN_GAMMA_WEIGHT
        gamma = BAYESIAN_GAMMA_WEIGHT

        # Regime-weighted historical bounds (posterior-weighted, không phải fuzzy logic)
        regime_p10 = sum(regime_probs.get(r, 0.0) * REGIME_BOUNDS[r]["p10"] for r in REGIME_BOUNDS)
        regime_p90 = sum(regime_probs.get(r, 0.0) * REGIME_BOUNDS[r]["p90"] for r in REGIME_BOUNDS)

        if n < MIN_OBS_FOR_DATA:
            observed_p10 = regime_p10
            observed_p90 = regime_p90
        else:
            sorted_vals = sorted(values)
            observed_p10 = sorted_vals[int(len(sorted_vals) * 0.1)]
            observed_p90 = sorted_vals[int(len(sorted_vals) * 0.9)]

        p10 = alpha * observed_p10 + beta * regime_p10 + gamma * policy_p10
        p90 = alpha * observed_p90 + beta * regime_p90 + gamma * policy_p90

        if p10 >= p90:
            p10, p90 = (p10 + p90) / 2 - 0.1, (p10 + p90) / 2 + 0.1

        return round(p10, 2), round(p90, 2), alpha

    def _fetch_latest_foreign_flow(self, target_date: str | None = None) -> float | None:
        """Fetch latest 10-day cumulative foreign net flow (billion VND)."""
        conn = self._get_conn()
        try:
            if target_date:
                row = conn.execute(
                    "SELECT SUM(net_value) FROM ("
                    "  SELECT date, net_value FROM market_foreign_history "
                    "  WHERE symbol = 'TOTAL' AND date <= ? "
                    "  ORDER BY date DESC LIMIT 10"
                    ")",
                    (target_date,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT SUM(net_value) FROM ("
                    "  SELECT date, net_value FROM market_foreign_history "
                    "  WHERE symbol = 'TOTAL' "
                    "  ORDER BY date DESC LIMIT 10"
                    ")"
                ).fetchone()
            if row and row[0] is not None:
                return float(row[0])
            if target_date:
                row2 = conn.execute(
                    "SELECT SUM(net_value) FROM market_foreign_history "
                    "WHERE date = (SELECT MAX(date) FROM market_foreign_history WHERE date <= ?)",
                    (target_date,),
                ).fetchone()
            else:
                row2 = conn.execute(
                    "SELECT SUM(net_value) FROM market_foreign_history "
                    "WHERE date = (SELECT MAX(date) FROM market_foreign_history)"
                ).fetchone()
            return float(row2[0]) if row2 and row2[0] is not None else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _fetch_latest_breadth(self, target_date: str | None = None) -> float | None:
        """Fetch latest market breadth from regime_history."""
        conn = self._get_conn()
        try:
            if target_date:
                row = conn.execute(
                    "SELECT breadth_pct FROM regime_history WHERE date <= ? ORDER BY date DESC LIMIT 1",
                    (target_date,),
                ).fetchone()
            else:
                row = conn.execute("SELECT breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1").fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def compute(self, target_date: str | None = None) -> LRIResult:
        """Compute LRI from latest available data.

        Args:
            target_date: PIT cutoff date (YYYY-MM-DD). All data queries are
                bounded by date <= target_date. None = live (latest data).

        Returns LRIResult with composite score, component scores, regime,
        and degraded_components list.
        """
        degraded = []

        # Fetch raw data (PIT-bounded)
        interbank_on = self._fetch_latest_macro("INTERBANK_ON", target_date)
        usd_vnd = self._fetch_latest_macro("USD_VND", target_date)
        fii_flow = self._fetch_latest_foreign_flow(target_date)
        breadth = self._fetch_latest_breadth(target_date)

        # Degraded mode detection
        if interbank_on is None:
            degraded.append("INTERBANK")
        if usd_vnd is None:
            degraded.append("USDVND")

        # OMO: use INTERBANK_ON as proxy
        omo_proxy = -(interbank_on - 3.0) * 10000 if interbank_on is not None else None

        # ── S_USDVND: MA90 deviation (unchanged from v2) ─────────────
        usdvnd_ma90 = self._fetch_ma90_usdvnd(target_date)
        usdvnd_dev_pct = None
        if usd_vnd is not None and usdvnd_ma90 is not None and usdvnd_ma90 > 0:
            usdvnd_dev_pct = abs(usd_vnd - usdvnd_ma90) / usdvnd_ma90
        s_usdvnd = _normalize(0.5 if usdvnd_dev_pct is None else usdvnd_dev_pct, 0.0, USDVND_DEVIATION_DENOM, invert=True)

        # ── S_Interbank: Regime-Aware Bayesian Bound + Exp Penalty ──
        ib_values = self._fetch_interbank_values(target_date, limit=self.EPOCH_WINDOW)
        ib_regime, ib_regime_probs, ib_hmm_fitted = self._classify_regime_fuzzy(target_date)
        ib_p10, ib_p90, ib_alpha = self._compute_bayesian_bounds_fuzzy(ib_values, ib_regime_probs)

        if interbank_on is not None and interbank_on > ib_p90:
            # Exponential Convex Penalty zone: INTERBANK_ON > P90
            penalty_activation = ib_p90 + self.PENALTY_ACTIVATION_GAP
            penalty_maximum = ib_p90 + self.PENALTY_MAXIMUM_GAP

            if interbank_on > penalty_activation:
                t = min(1.0, (interbank_on - penalty_activation) / (penalty_maximum - penalty_activation))
                penalty = self.MAX_PENALTY * t * t  # convex (quadratic)
                s_interbank = -penalty
            else:
                # Dead zone: P90 < INTERBANK_ON <= activation
                s_interbank = 0.0
        else:
            s_interbank = _normalize(0.5 if interbank_on is None else interbank_on, ib_p10, ib_p90, invert=True)

        # ── Remaining components ─────────────────────────────────────
        s_omo = _normalize(0.5 if omo_proxy is None else omo_proxy, OMO_LOW, OMO_HIGH)
        s_fii = _normalize(0.5 if fii_flow is None else fii_flow, FII_LOW, FII_HIGH)
        s_breadth = _normalize(0.5 if breadth is None else breadth, BREADTH_LOW, BREADTH_HIGH)

        # Compute weighted LRI
        lri = (
            self.WEIGHTS["interbank"] * s_interbank
            + self.WEIGHTS["usdvnd"] * s_usdvnd
            + self.WEIGHTS["omo"] * s_omo
            + self.WEIGHTS["fii"] * s_fii
            + self.WEIGHTS["breadth"] * s_breadth
        )
        lri = round(max(0.0, min(1.0, lri)), 4)

        # Determine regime
        if lri >= self.AGGRESSIVE_THRESHOLD:
            alloc_regime = "AGGRESSIVE"
            max_alloc = 100.0
        elif lri >= self.PROBE_THRESHOLD:
            alloc_regime = "PROBE"
            max_alloc = lri * 100.0
        else:
            alloc_regime = "DEFENSIVE"
            max_alloc = 0.0

        # Degraded mode logging
        if degraded:
            logger.warning(
                "[M4_DEGRADED_MODE] Lack of %s data - Marginalizing LRI",
                "+".join(degraded),
            )

        return LRIResult(
            lri=lri,
            s_interbank=round(s_interbank, 4),
            s_usdvnd=round(s_usdvnd, 4),
            s_omo=round(s_omo, 4),
            s_fii=round(s_fii, 4),
            s_breadth=round(s_breadth, 4),
            regime=alloc_regime,
            max_allocation_pct=round(max_alloc, 2),
            degraded_components=degraded,
            components_raw={
                "interbank_on": interbank_on,
                "interbank_regime": ib_regime,
                "interbank_regime_probs": ib_regime_probs,
                "interbank_hmm_fitted": ib_hmm_fitted,
                "interbank_p10": ib_p10,
                "interbank_p90": ib_p90,
                "interbank_alpha": round(ib_alpha, 4),
                "usd_vnd": usd_vnd,
                "usdvnd_ma90": usdvnd_ma90,
                "usdvnd_deviation_pct": (round(usdvnd_dev_pct * 100, 4) if usdvnd_dev_pct is not None else None),
                "omo_proxy": omo_proxy,
                "fii_flow_10d": fii_flow,
                "breadth_pct": breadth,
            },
        )


def compute_lri(db_path: str | None = None, target_date: str | None = None) -> LRIResult:
    """Convenience function to compute LRI."""
    return LiquidityRecoveryIndex(db_path).compute(target_date)


if __name__ == "__main__":
    result = compute_lri()
    print("=" * 60)
    print("  LIQUIDITY RECOVERY INDEX (LRI) v3 — Regime-Aware Bayesian")
    print("=" * 60)
    print(f"  LRI Composite:  {result.lri:.4f}")
    print(f"  Regime:         {result.regime}")
    print(f"  Max Allocation: {result.max_allocation_pct:.1f}%")
    if result.degraded_components:
        print(f"  Degraded:       {', '.join(result.degraded_components)}")
    print("\n  Components:")
    raw = result.components_raw
    print(
        f"    S_Interbank:  {result.s_interbank:.4f}  "
        f"(raw: {raw['interbank_on']}%, regime: {raw['interbank_regime']}, "
        f"P10: {raw['interbank_p10']}%, P90: {raw['interbank_p90']}%, "
        f"alpha: {raw['interbank_alpha']})"
    )
    print(
        f"    S_USDVND:     {result.s_usdvnd:.4f}  "
        f"(raw: {raw['usd_vnd']}, MA90: {raw['usdvnd_ma90']:.0f}, "
        f"dev: {raw['usdvnd_deviation_pct']}%)"
    )
    print(f"    S_OMO:        {result.s_omo:.4f}  (proxy: {raw['omo_proxy']})")
    print(f"    S_FII:        {result.s_fii:.4f}  (raw: {raw['fii_flow_10d']})")
    print(f"    S_Breadth:    {result.s_breadth:.4f}  (raw: {raw['breadth_pct']})")
    print(
        f"\n  Weights: Interbank={LiquidityRecoveryIndex.WEIGHTS['interbank']:.0%} "
        f"USDVND={LiquidityRecoveryIndex.WEIGHTS['usdvnd']:.0%} "
        f"OMO={LiquidityRecoveryIndex.WEIGHTS['omo']:.0%} "
        f"FII={LiquidityRecoveryIndex.WEIGHTS['fii']:.0%} "
        f"Breadth={LiquidityRecoveryIndex.WEIGHTS['breadth']:.0%}"
    )
    print(f"{'=' * 60}")
