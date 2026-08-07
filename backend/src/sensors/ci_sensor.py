"""
ci_sensor.py — Crowding Index Sensor v3 (Layer 3: Positioning).

Three processing blocks:

1. Multi-source Normalization Engine (ECDF):
   COT (weekly) + OI (daily) + Funding Rate (8h) + ETF Flow (daily)
   → Empirical CDF per source → uniform [0, 1] scale.

2. Dual-Timestamp Protocol + Plateau Decay (2026-07-10 hotfix):
   COT uses valid_from (Friday 15:30 ET release) as decay anchor,
   NOT snapshot_date (Tuesday). Plateau: weight=1.0 for 3 days
   post-release, then linear decay to 0.70 floor by day 7.
   Prevents look-ahead bias and premature signal suppression.

3. Denominator Stabilization (Dynamic Rebalancing):
   Raw weights (COT=1.0, OI=0.6, ETF=0.4, FR=0.3) are rebalanced
   to always sum to 1.0, maintaining sigmoid center at 0.5.
   During forced liquidation divergence, COT plateau shrinks
   from 3 to 1 day to accelerate stale-data de-weighting.
"""

import logging
from collections import deque
from datetime import UTC, datetime

import numpy as np

logger = logging.getLogger(__name__)

# ── Default Parameters ─────────────────────────────────────────────────

# ECDF windows (periods)
COT_WINDOW = 52  # ~1 year weekly
OI_WINDOW = 252  # ~1 year daily
FR_WINDOW = 90  # ~30 days 8-hourly
ETF_WINDOW = 252  # ~1 year daily

# ── WEIGHT MATRIX REVERSAL (2026-07-10) ──
# Weights proportional to STRUCTURAL DEPTH, NOT sampling frequency.
# COT = 1.0 (institutional flows, hardest to unwind — anchor)
# OI  = 0.6 (open interest scale)
# ETF = 0.4 (institutional flow proxy)
# FR  = 0.3 (short-term leverage cost, noisiest)
COT_WEIGHT_BASE = 1.0
OI_WEIGHT_BASE = 0.6
FR_WEIGHT_BASE = 0.3
ETF_WEIGHT_BASE = 0.4

# ── DUAL-TIMESTAMP + PLATEAU DECAY (2026-07-10) ──
# COT decay anchored on valid_from (Friday 15:30 ET), NOT snapshot_date.
# Plateau: weight = COT_WEIGHT_BASE for 3 days after release.
# Linear decay: from day 4 to day 7, weight drops 15% → 0.85 of base.
COT_PLATEAU_DAYS = 3  # weight = base for first 3 days
COT_LINEAR_DECAY_DAYS = 4  # days 4-7: linear decay
COT_DECAY_FLOOR = 0.70  # minimum weight as fraction of base

# Dual-EWMA for COT smoothing
COT_EWMA_FAST = 0.30  # responsive to new COT print
COT_EWMA_SLOW = 0.05  # long-term trajectory

# Divergence-triggered lag adjustment
DIVERGENCE_THRESHOLD = 0.20  # |CI_fast - CI_slow| trigger

# ── Block 1: ECDF Normalizer ──────────────────────────────────────────


class ECDFNormalizer:
    """Empirical CDF normalizer: maps any distribution to uniform [0, 1].

    CI = ECDF(x) = rank(x) / n, clamped to [0.01, 0.99].
    No distributional assumptions. Robust to outliers by construction.
    """

    def __init__(self, window: int):
        self.window = window
        self._buf: deque[float] = deque(maxlen=window)

    def update(self, value: float) -> float:
        """Ingest a new value and return its ECDF-based CI ∈ [0, 1]."""
        self._buf.append(value)
        n = len(self._buf)
        if n < 3:
            return 0.5
        arr = np.array(self._buf)
        rank = float(np.sum(arr <= value))
        ci = rank / n
        return float(np.clip(ci, 0.01, 0.99))

    @property
    def n(self) -> int:
        return len(self._buf)

    def cdf_value(self, value: float) -> float:
        """Query ECDF for an arbitrary value without updating buffer."""
        if len(self._buf) < 3:
            return 0.5
        rank = float(np.sum(np.array(self._buf) <= value))
        return float(np.clip(rank / len(self._buf), 0.01, 0.99))


# ── Block 2: Dual-EWMA Filter ─────────────────────────────────────────


class DualEWMA:
    """Dual Exponential Weighted Moving Average for irregular time series.

    Maintains fast and slow averages. The smoothed value and the
    momentum (fast - slow) provide analytical continuity for the
    Reflexivity Jacobian.

    When no update arrives (gap days), both averages converge toward
    each other exponentially, preserving inertia.
    """

    def __init__(self, alpha_fast: float, alpha_slow: float):
        assert 0 < alpha_slow < alpha_fast < 1, "fast must be > slow"
        self._alpha_fast = alpha_fast
        self._alpha_slow = alpha_slow
        self._fast: float | None = None
        self._slow: float | None = None
        self._last_update: datetime | None = None

    def update(self, value: float, timestamp: datetime) -> float:
        """Ingest a new observation and return the smoothed value."""
        if self._fast is None or self._slow is None:
            self._fast = value
            self._slow = value
        else:
            self._fast = self._alpha_fast * value + (1 - self._alpha_fast) * self._fast
            self._slow = self._alpha_slow * value + (1 - self._alpha_slow) * self._slow
        self._last_update = timestamp
        return float(self._fast)

    def decay(self, now: datetime) -> float:
        """Decay both averages toward each other during gaps.

        Returns the smoothed value at *now* with convergence applied.
        """
        if self._fast is None or self._slow is None or self._last_update is None:
            return 0.5
        # Strip tzinfo for naive-safe arithmetic
        n = now.replace(tzinfo=None) if now.tzinfo else now
        lu = self._last_update.replace(tzinfo=None) if self._last_update.tzinfo else self._last_update
        days = max(0.0, (n - lu).total_seconds() / 86400.0)
        if days < 1e-6:
            return float(self._fast)

        # Over the gap, fast and slow exponentially converge toward
        # each other: convergence_rate ≈ alpha_diff per period.
        gap_steps = days
        decay_factor = (1 - (self._alpha_fast - self._alpha_slow)) ** gap_steps
        diff = (self._fast - self._slow) * decay_factor
        converged = self._slow + diff
        return float(converged)

    @property
    def momentum(self) -> float:
        """Fast - slow = direction + velocity of positioning change."""
        if self._fast is None or self._slow is None:
            return 0.0
        return float(self._fast - self._slow)

    @property
    def initialized(self) -> bool:
        return self._fast is not None

    def reset(self) -> None:
        self._fast = None
        self._slow = None
        self._last_update = None


# ── Block 3: CrowdingIndexSensor ──────────────────────────────────────


class CrowdingIndexSensor:
    """Layer 3 Positioning Sensor v2 — 4 sources → P vector + UTC metadata.

    Usage:
        sensor = CrowdingIndexSensor()
        sensor.ingest_cot(net_position, snapshot_date)
        sensor.ingest_oi(open_interest_value)
        sensor.ingest_funding_rate(rate)
        sensor.ingest_etf_flow(net_flow)
        result = sensor.compute_ci()   # full output dict
        ci = result["ci"]              # scalar [0, 1]
        P = result["P"]                # [p_long, p_short, p_flat]
    """

    def __init__(self):
        # ECDF normalizers per source
        self._cot_norm = ECDFNormalizer(COT_WINDOW)
        self._oi_norm = ECDFNormalizer(OI_WINDOW)
        self._fr_norm = ECDFNormalizer(FR_WINDOW)
        self._etf_norm = ECDFNormalizer(ETF_WINDOW)

        # Dual-EWMA for COT smoothing
        self._cot_ewma = DualEWMA(COT_EWMA_FAST, COT_EWMA_SLOW)

        # Raw stores
        self._cot_raw: float | None = None
        self._cot_snap: datetime | None = None  # Tuesday snapshot
        self._cot_valid_from: datetime | None = None  # Friday 15:30 ET
        self._oi_raw: float | None = None
        self._fr_raw: float | None = None
        self._etf_raw: float | None = None

        # Normalized CIs per source (value ∈ [0, 1])
        self._cot_ci: float = 0.5
        self._oi_ci: float = 0.5
        self._fr_ci: float = 0.5
        self._etf_ci: float = 0.5

        # Divergence tracker (last computed)
        self._divergence_last: float = 0.0

    # ── Ingest Methods ─────────────────────────────────────────────────

    def ingest_cot(self, value: float, snapshot_date: datetime, valid_from: datetime | None = None) -> None:
        """Ingest a COT data point with Dual-Timestamp Protocol.

        Parameters
        ----------
        value : float
            Net position of Large Speculators.
            Positive = net long; negative = net short.
        snapshot_date : datetime
            Date the data reflects (Tuesday close).
        valid_from : datetime, optional
            Availability anchor — Friday 15:30 ET when CFTC releases.
            If None, defaults to snapshot_date (legacy fallback).
            Decay is calculated from valid_from, NOT snapshot_date.
        """
        self._cot_raw = value
        self._cot_snap = snapshot_date
        # Availability anchor: decay starts from valid_from (Friday 15:30 ET)
        self._cot_valid_from = valid_from or snapshot_date
        self._cot_ci = self._cot_norm.update(value)
        self._cot_ewma.update(value, snapshot_date)

    def ingest_oi(self, value: float) -> None:
        """Ingest daily Open Interest value."""
        self._oi_raw = value
        self._oi_ci = self._oi_norm.update(value)

    def ingest_funding_rate(self, value: float) -> None:
        """Ingest a funding rate print (8-hourly or hourly).

        Sign: positive = longs pay shorts (crowded long).
        """
        self._fr_raw = value
        self._fr_ci = self._fr_norm.update(value)

    def ingest_etf_flow(self, value: float) -> None:
        """Ingest daily ETF net flow.

        Sign: positive = net inflow (crowded long).
        """
        self._etf_raw = value
        self._etf_ci = self._etf_norm.update(value)

    # ── CI Computation ─────────────────────────────────────────────────

    def compute_ci(self, current_date: datetime | None = None) -> dict:
        """Full CI computation with lag adjustment + output schema.

        Returns
        -------
        dict with keys:
            ci        : float ∈ [0, 1]  — blended crowding index
            P         : [p_long, p_short, p_flat] — position state vector
            timestamp : str (ISO UTC)
            components: per-source breakdown
            divergence: fast/slow divergence for audit
        """
        now = current_date or datetime.now()
        # Strip tzinfo if present to match local DualEWMA timestamps
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)

        # Component CIs via ECDF
        oi_ci = self._oi_ci
        fr_ci = self._fr_ci
        etf_ci = self._etf_ci

        # Fast CI (real-time sources: FR + ETF + OI)
        w_fast = OI_WEIGHT_BASE + FR_WEIGHT_BASE + ETF_WEIGHT_BASE
        ci_fast = (OI_WEIGHT_BASE * oi_ci + FR_WEIGHT_BASE * fr_ci + ETF_WEIGHT_BASE * etf_ci) / w_fast if w_fast > 0 else 0.5

        # Slow CI (COT only, with Dual-EWMA smoothing + decay)
        cot_smoothed = self._cot_ewma.decay(now)
        if self._cot_norm.n >= 3 and self._cot_raw is not None:
            ci_slow = self._cot_norm.cdf_value(cot_smoothed)
        else:
            ci_slow = 0.5

        # Divergence-triggered lag adjustment
        self._divergence_last = abs(ci_fast - ci_slow)
        if self._divergence_last > DIVERGENCE_THRESHOLD:
            lag_adjusted = True
            # During divergence, COT plateau shrinks from 3→1 day
            # and linear decay accelerates
            effective_plateau = max(1, COT_PLATEAU_DAYS - 2)
        else:
            lag_adjusted = False
            effective_plateau = COT_PLATEAU_DAYS

        # COT weight with plateau decay
        w_cot = self._cot_weight(now, plateau_days=effective_plateau)
        w_oi = OI_WEIGHT_BASE if self._oi_norm.n >= 1 else 0.0
        w_fr = FR_WEIGHT_BASE if self._fr_norm.n >= 1 else 0.0
        w_etf = ETF_WEIGHT_BASE if self._etf_norm.n >= 1 else 0.0

        # ── Denominator Stabilization (Dynamic Rebalancing) ──
        # Rebalance so total weight always = 1.0, maintaining
        # sigmoid center at exactly 0.5 regardless of data gaps.
        raw_w = np.array([w_cot, w_oi, w_fr, w_etf])
        w_sum = float(raw_w.sum())
        if w_sum < 1e-10:
            rebalanced = np.array([0.25, 0.25, 0.25, 0.25])
        else:
            rebalanced = raw_w / w_sum

        cis = np.array([ci_slow, oi_ci, fr_ci, etf_ci])
        ci = float(np.clip(rebalanced @ cis, 0.0, 1.0))

        # Position state vector
        p_long = max(0.0, (ci - 0.5) * 2.0)
        p_short = max(0.0, (0.5 - ci) * 2.0)
        p_flat = 1.0 - abs(ci - 0.5) * 2.0

        return {
            "ci": round(ci, 4),
            "P": [
                round(p_long, 4),
                round(p_short, 4),
                round(p_flat, 4),
            ],
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "components": {
                "cot": {
                    "ci": round(ci_slow, 4),
                    "weight": round(w_cot, 4),
                    "smoothed": round(cot_smoothed, 2),
                    "momentum": round(self._cot_ewma.momentum, 4),
                    "n": self._cot_norm.n,
                },
                "oi": {
                    "ci": round(oi_ci, 4),
                    "weight": round(w_oi, 4),
                    "n": self._oi_norm.n,
                },
                "fr": {
                    "ci": round(fr_ci, 4),
                    "weight": round(w_fr, 4),
                    "n": self._fr_norm.n,
                },
                "etf_flow": {
                    "ci": round(etf_ci, 4),
                    "weight": round(w_etf, 4),
                    "n": self._etf_norm.n,
                },
            },
            "divergence": {
                "ci_fast": round(ci_fast, 4),
                "ci_slow": round(ci_slow, 4),
                "delta": round(self._divergence_last, 4),
                "lag_adjusted": lag_adjusted,
            },
            "weights_rebalanced": {
                "cot": round(float(rebalanced[0]), 4),
                "oi": round(float(rebalanced[1]), 4),
                "fr": round(float(rebalanced[2]), 4),
                "etf_flow": round(float(rebalanced[3]), 4),
                "sum": round(float(rebalanced.sum()), 4),
            },
        }

    def _cot_weight(self, now: datetime, plateau_days: int = COT_PLATEAU_DAYS) -> float:
        """Plateau decay function anchored on valid_from (Friday 15:30 ET).

        Phase 1 — Plateau (days 0-3): weight = COT_WEIGHT_BASE.
          Market needs time to digest Friday release. No decay.

        Phase 2 — Linear decay (days 4-7): weight drops from
          COT_WEIGHT_BASE linearly to COT_WEIGHT_BASE * COT_DECAY_FLOOR.

        Phase 3 — Floor (days 8+): weight holds at floor until next release.
        """
        if self._cot_valid_from is None or self._cot_norm.n < 1:
            return 0.0
        vf = self._cot_valid_from.replace(tzinfo=None) if self._cot_valid_from.tzinfo else self._cot_valid_from
        n = now.replace(tzinfo=None) if now.tzinfo and hasattr(now, "tzinfo") else now
        days = max(0.0, (n - vf).total_seconds() / 86400.0)

        if days <= plateau_days:
            return COT_WEIGHT_BASE

        decay_days = days - plateau_days
        floor = COT_WEIGHT_BASE * COT_DECAY_FLOOR
        if decay_days >= COT_LINEAR_DECAY_DAYS:
            return floor

        decay_per_day = (COT_WEIGHT_BASE - floor) / COT_LINEAR_DECAY_DAYS
        return COT_WEIGHT_BASE - decay_per_day * decay_days

    # ── Status & Inspection ────────────────────────────────────────────

    def status(self) -> dict:
        """Full status for audit — includes all 4 components + divergence."""
        result = self.compute_ci()
        result["cot_ewma"] = {
            "fast": round(self._cot_ewma._fast or 0.0, 2) if self._cot_ewma.initialized else None,
            "slow": round(self._cot_ewma._slow or 0.0, 2) if self._cot_ewma.initialized else None,
            "momentum": round(self._cot_ewma.momentum, 4),
            "initialized": self._cot_ewma.initialized,
        }
        return result

    def reset(self) -> None:
        """Clear all state."""
        self._cot_norm = ECDFNormalizer(COT_WINDOW)
        self._oi_norm = ECDFNormalizer(OI_WINDOW)
        self._fr_norm = ECDFNormalizer(FR_WINDOW)
        self._etf_norm = ECDFNormalizer(ETF_WINDOW)
        self._cot_ewma = DualEWMA(COT_EWMA_FAST, COT_EWMA_SLOW)
        self._cot_raw = None
        self._cot_snap = None
        self._cot_valid_from = None
        self._oi_raw = None
        self._fr_raw = None
        self._etf_raw = None
        self._cot_ci = 0.5
        self._oi_ci = 0.5
        self._fr_ci = 0.5
        self._etf_ci = 0.5
        self._divergence_last = 0.0
