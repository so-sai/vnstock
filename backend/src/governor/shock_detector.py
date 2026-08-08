"""shock_detector.py — Shock Detector (Shock ≠ Macro Score).

Detects market stress via 5 orthogonal PIT Z-scores, each computed against a
trailing window STRICTLY BEFORE target_date (no future leakage):

    Shock_t = clip(w1·Z_vol + w2·Z_spread + w3·Z_fx + w4·Z_liq + w5·Z_breadth)

  Z_vol        daily close-to-close |return| of VNINDEX (volatility)
  Z_spread     intraday high-low range of VNINDEX (fragility)
  Z_fx         daily % change of USD_VND (FX stress)
  Z_liquidity  volume of VNINDEX (liquidity drain)
  Z_breadth    regime_history.breadth_velocity (market breadth deterioration)

Severity bands:
  <0.30 NORMAL / <0.50 WATCH / <0.70 STRESS / <0.85 SHOCK / >=0.85 SYSTEMIC_SHOCK

GOVERNANCE: ShockScore carries ONLY veto/sizing rights. It exposes severity
and a band — it never encodes a BUY/SELL action. Downstream consumers decide
how to use it (freeze entries, scale allocation, force-sell).

Source of truth:
  - daily_ohlcv (symbol = VNINDEX) for vol/spread/liquidity
  - macro_history (USD_VND) for FX
  - regime_history (breadth_velocity) for breadth
All queries filter date <= target_date and use a trailing lookback window
that EXCLUDES target_date itself for the mean/std baseline.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Component weights (sum = 1.0) ──────────────────────────────────────────
COMPONENT_WEIGHTS = {
    "vol": 0.25,
    "spread": 0.20,
    "fx": 0.15,
    "liquidity": 0.20,
    "breadth": 0.20,
}

# Trailing window (calendar days) for the Z-score baseline, excluding target.
TRAILING_DAYS = 60
# Minimum baseline samples; below → neutral Z=0 (not enough history).
MIN_SAMPLES = 5

# Severity bands (see module docstring).
BAND_EDGES = [
    ("NORMAL", 0.30),
    ("WATCH", 0.50),
    ("STRESS", 0.70),
    ("SHOCK", 0.85),
    ("SYSTEMIC_SHOCK", 1.01),
]


def severity_bands() -> list[str]:
    return [name for name, _ in BAND_EDGES]


def stress_fraction(z: float) -> float:
    """Map a Z-score to a stress fraction ∈ [0, 1].

    Z is clamped to ±3 sigma then rescaled: (clip(z)+3)/6.
    A single extreme Z can no longer saturate the composite shock score
    (orthogonality: each dimension contributes its own bounded stress).
    """
    clipped = max(-3.0, min(3.0, z))
    return max(0.0, min(1.0, (clipped + 3.0) / 6.0))


def classify_shock(severity: float) -> str:
    """Map severity ∈ [0,1] to one of the 5 severity bands."""
    for name, edge in BAND_EDGES:
        if severity < edge:
            return name
    return "SYSTEMIC_SHOCK"


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
DEFAULT_DB = str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")


@dataclass(frozen=True)
class ShockScore:
    """Read-only market stress signal. Veto/sizing rights ONLY, never orders."""

    target_date: str
    severity: float
    z_scores: dict = field(default_factory=dict)
    source_trace: dict = field(default_factory=dict)
    db_path: str = ""

    @property
    def band(self) -> str:
        return classify_shock(self.severity)


class ShockDetector:
    """Compute Shock_t from PIT market data."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DEFAULT_DB

    # ── Low-level PIT helpers ─────────────────────────────────────────────
    def _query(self, conn, sql: str, params: tuple) -> list:
        return conn.execute(sql, params).fetchall()

    def _baseline_stats(self, values: list[float]) -> tuple[float, float] | None:
        """(mean, std) of baseline; None if too few samples or flat."""
        if len(values) < MIN_SAMPLES:
            return None
        mean = sum(values) / len(values)
        var = sum((x - mean) ** 2 for x in values) / len(values)
        std = math.sqrt(var)
        if std < 1e-9:
            return None
        return mean, std

    def _zscore(self, current: float, stats: tuple[float, float] | None) -> float:
        if stats is None or current is None:
            return 0.0
        mean, std = stats
        return (current - mean) / std

    # ── Component: volatility (daily close-to-close |return| of VNINDEX) ──
    def _vol(self, conn, target_date: str) -> tuple[float, float]:
        """(Z_vol, source_trace) — current |return| vs trailing |returns|.

        Orthogonality: vol = close-to-close move magnitude; spread = intraday
        range. Both are computed on VNINDEX but measure DIFFERENT dimensions.
        """
        rows = self._query(
            conn,
            "SELECT date, close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date ASC",
            (target_date,),
        )
        if len(rows) < MIN_SAMPLES + 1:
            return 0.0, "daily_ohlcv VNINDEX close (insufficient history)"
        closes = [(d, float(c)) for d, c in rows if c is not None]
        if len(closes) < MIN_SAMPLES + 1:
            return 0.0, "daily_ohlcv VNINDEX close (insufficient valid rows)"
        returns = []
        for (d0, c0), (d1, c1) in zip(closes, closes[1:]):
            if c0 > 0:
                returns.append((d1, abs((c1 - c0) / c0)))
        if len(returns) < MIN_SAMPLES:
            return 0.0, "daily_ohlcv VNINDEX returns (insufficient)"
        current = returns[-1][1]
        baseline = [v for d, v in returns[:-1]]
        stats = self._baseline_stats(baseline)
        return self._zscore(current, stats), "daily_ohlcv VNINDEX |close return| trailing 60d"

    # ── Component: spread (intraday high-low range) ───────────────────────
    def _spread(self, conn, target_date: str) -> tuple[float, float]:
        """(Z_spread, trace) — current intraday range vs trailing range."""
        rows = self._query(
            conn,
            "SELECT date, high, low, close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC",
            (target_date,),
        )
        if not rows:
            return 0.0, "daily_ohlcv VNINDEX high/low (no data)"
        series = []
        for d, high, low, close in rows:
            if high is None or low is None or not close:
                continue
            spread = (high - low) / close
            series.append((d, spread))
        series.sort(key=lambda x: x[0])
        if not series:
            return 0.0, "daily_ohlcv VNINDEX (no valid rows)"
        current = series[-1][1]
        baseline = [v for d, v in series[:-1]]
        stats = self._baseline_stats(baseline)
        return self._zscore(current, stats), "daily_ohlcv VNINDEX intraday range trailing 60d"

    # ── Component: FX (USD_VND daily % change) ────────────────────────────
    def _fx(self, conn, target_date: str) -> tuple[float, float]:
        """(Z_fx, trace) — daily % change of USD_VND vs trailing % changes."""
        rows = self._query(
            conn,
            "SELECT date, value FROM macro_history WHERE variable = 'USD_VND' AND date <= ? ORDER BY date DESC",
            (target_date,),
        )
        if not rows:
            return 0.0, "macro_history USD_VND (no data)"
        series = sorted(((d, float(v)) for d, v in rows if v is not None), key=lambda x: x[0])
        if len(series) < MIN_SAMPLES + 1:
            return 0.0, "macro_history USD_VND (insufficient history)"
        changes = []
        for (d0, v0), (d1, v1) in zip(series, series[1:]):
            if v0 > 0:
                changes.append((v1 - v0) / v0)
        if len(changes) < MIN_SAMPLES:
            return 0.0, "macro_history USD_VND (insufficient changes)"
        current = changes[-1]
        baseline = changes[:-1]
        stats = self._baseline_stats(baseline)
        return self._zscore(current, stats), "macro_history USD_VND pct-change trailing 60d"

    # ── Component: liquidity (VNINDEX volume) ─────────────────────────────
    def _liquidity(self, conn, target_date: str) -> tuple[float, float]:
        """(Z_liq, trace) — current volume vs trailing. Negative Z = drain."""
        rows = self._query(
            conn,
            "SELECT date, volume FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC",
            (target_date,),
        )
        if not rows:
            return 0.0, "daily_ohlcv VNINDEX volume (no data)"
        series = sorted(((d, float(v)) for d, v in rows if v is not None), key=lambda x: x[0])
        if not series:
            return 0.0, "daily_ohlcv VNINDEX volume (no valid rows)"
        current = series[-1][1]
        baseline = [v for d, v in series[:-1]]
        stats = self._baseline_stats(baseline)
        return self._zscore(current, stats), "daily_ohlcv VNINDEX volume trailing 60d"

    # ── Component: breadth (regime_history.breadth_velocity) ──────────────
    def _breadth(self, conn, target_date: str) -> tuple[float, float]:
        """(Z_breadth, trace) — current breadth_velocity vs trailing."""
        rows = self._query(
            conn,
            "SELECT date, breadth_velocity FROM regime_history WHERE date <= ? ORDER BY date DESC",
            (target_date,),
        )
        if not rows:
            return 0.0, "regime_history breadth_velocity (no data)"
        series = sorted(((d, float(v)) for d, v in rows if v is not None), key=lambda x: x[0])
        if not series:
            return 0.0, "regime_history breadth_velocity (no valid rows)"
        current = series[-1][1]
        baseline = [v for d, v in series[:-1]]
        stats = self._baseline_stats(baseline)
        return self._zscore(current, stats), "regime_history breadth_velocity trailing 60d"

    # ── Orchestration ─────────────────────────────────────────────────────
    def _stress_fraction(self, z: float) -> float:
        """Backward-compat wrapper for module-level :func:`stress_fraction`."""
        return stress_fraction(z)

    def detect(self, target_date: str) -> ShockScore:
        """Compute Shock_t for a single PIT date."""
        conn = sqlite3.connect(self.db_path)
        try:
            z_vol, t_vol = self._vol(conn, target_date)
            z_spread, t_spread = self._spread(conn, target_date)
            z_fx, t_fx = self._fx(conn, target_date)
            z_liq, t_liq = self._liquidity(conn, target_date)
            z_breadth, t_breadth = self._breadth(conn, target_date)

            z_scores = {
                "vol": round(z_vol, 4),
                "spread": round(z_spread, 4),
                "fx": round(z_fx, 4),
                "liquidity": round(z_liq, 4),
                "breadth": round(z_breadth, 4),
            }

            # Stress directions: vol/spread/fx stress on POSITIVE Z; liquidity
            # and breadth stress on NEGATIVE Z (volume / breadth collapse).
            shock_raw = (
                COMPONENT_WEIGHTS["vol"] * self._stress_fraction(z_vol)
                + COMPONENT_WEIGHTS["spread"] * self._stress_fraction(z_spread)
                + COMPONENT_WEIGHTS["fx"] * self._stress_fraction(z_fx)
                + COMPONENT_WEIGHTS["liquidity"] * self._stress_fraction(-z_liq)
                + COMPONENT_WEIGHTS["breadth"] * self._stress_fraction(-z_breadth)
            )
            severity = max(0.0, min(1.0, round(shock_raw, 4)))

            source_trace = {
                "vol": t_vol,
                "spread": t_spread,
                "fx": t_fx,
                "liquidity": t_liq,
                "breadth": t_breadth,
            }

            return ShockScore(
                target_date=target_date,
                severity=severity,
                z_scores=z_scores,
                source_trace=source_trace,
                db_path=self.db_path,
            )
        finally:
            conn.close()


def detect_shock(target_date: str, db_path: str | None = None) -> ShockScore:
    """Convenience: one-shot shock detection."""
    return ShockDetector(db_path).detect(target_date)
