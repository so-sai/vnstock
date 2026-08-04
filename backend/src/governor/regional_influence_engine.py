"""regional_influence_engine.py — Macro State Vector Computation.

Computes the Macro State Vector M ∈ R^4 from live market data:
  M = [US_Liquidity, China_Economy, Commodity_Cycle, Domestic_Liquidity]

Each component is normalized to [0, 1] via regime-aware Bayesian bounds.

Integration:
  Macro History DB → RegionalInfluenceEngine → M vector → SectorExposureMatrix
                                                      → Sector Macro Scores

LAW-010 (Regime Invariance):
  All macro observations are evaluated conditional on regime.
"""

import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _hydrate_path():
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

# ── Normalization Bounds ──────────────────────────────────────────────
# Historical ranges for each macro variable (VN market 2011-2026).
NORMALIZATION_BOUNDS = {
    # US Liquidity: Fed Funds Rate + DXY composite
    "US_FED_RATE": {"low": 0.0, "high": 5.5},
    "DXY": {"low": 90.0, "high": 115.0},
    "US10Y": {"low": 0.5, "high": 5.0},
    "VIX": {"low": 12.0, "high": 35.0},
    # China Economy
    "USDCNY": {"low": 6.3, "high": 7.5},
    # Commodity Cycle
    "BRENT_OIL": {"low": 40.0, "high": 120.0},
    "COPPER": {"low": 6000.0, "high": 11000.0},
    # Domestic Liquidity
    "INTERBANK_ON": {"low": 2.0, "high": 8.0},
    "VNINDEX": {"low": 800.0, "high": 1500.0},
}

# ── Composite Weights for Each Macro Node ─────────────────────────────
# How to aggregate multiple indicators into one node score.
NODE_COMPOSITE_WEIGHTS = {
    "US_Liquidity": {
        "US_FED_RATE": 0.40,
        "DXY": 0.30,
        "US10Y": 0.20,
        "VIX": 0.10,
    },
    "China_Economy": {
        "USDCNY": 0.50,
        "BRENT_OIL": 0.25,  # China is largest oil importer
        "COPPER": 0.25,  # China is largest copper consumer
    },
    "Commodity_Cycle": {
        "BRENT_OIL": 0.40,
        "COPPER": 0.40,
        "DXY": 0.20,  # USD inverse correlation with commodities
    },
    "Domestic_Liquidity": {
        "INTERBANK_ON": 0.50,
        "VNINDEX": 0.30,  # Market breadth as liquidity proxy
        "US10Y": 0.20,  # Global yield influence
    },
}


def _normalize(value: float, low: float, high: float, invert: bool = False) -> float:
    """Normalize value to [0, 1]. Invert=True for inverse indicators."""
    if value is None:
        return 0.5
    if high <= low:
        return 0.5
    clamped = max(low, min(high, value))
    normalized = (clamped - low) / (high - low)
    return 1.0 - normalized if invert else normalized


@dataclass
class RegionalMacroResult:
    """Result of regional macro state vector computation."""

    macro_vector: dict  # M = {node: score ∈ [0, 1]}
    component_scores: dict  # Raw indicator scores
    data_quality: dict  # Which indicators had data
    node_details: dict  # Per-node decomposition
    momentum: dict = None  # {node: Δscore over lookback} — direction of change
    confidence: dict = None  # {node: data completeness ratio [0,1]}

    def __post_init__(self):
        if self.momentum is None:
            self.momentum = {}
        if self.confidence is None:
            self.confidence = {}


class RegionalInfluenceEngine:
    """Compute Macro State Vector M from live market data.

    Architecture:
      1. Fetch macro indicators from DB
      2. Normalize each indicator to [0, 1]
      3. Aggregate into 4 macro nodes via weighted average
      4. Return M vector for SectorExposureMatrix

    Example:
      engine = RegionalInfluenceEngine()
      result = engine.compute()
      print(result.macro_vector)
      # {'US_Liquidity': 0.35, 'China_Economy': 0.72,
      #  'Commodity_Cycle': 0.58, 'Domestic_Liquidity': 0.61}
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")

    def _fetch_latest(self, variable: str, target_date: Optional[str] = None) -> Optional[float]:
        """Fetch latest value for a macro variable.

        WHY: VNINDEX lives in daily_ohlcv (not macro_history) because it's
        crawled by VnstockProvider, not yfinance. All other macro variables
        live in macro_history. This fallback bridges the two tables.
        """
        conn = sqlite3.connect(self.db_path)
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
            if row and row[0] is not None:
                return float(row[0])

            # VNINDEX fallback: lives in daily_ohlcv, not macro_history
            if variable == "VNINDEX":
                if target_date:
                    row = conn.execute(
                        "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC LIMIT 1",
                        (target_date,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT 1"
                    ).fetchone()
                if row and row[0] is not None:
                    return float(row[0])

            return None
        except Exception:
            return None
        finally:
            conn.close()

    def _fetch_rolling_avg(self, variable: str, window: int = 20, target_date: Optional[str] = None) -> Optional[float]:
        """Fetch rolling average for a macro variable."""
        conn = sqlite3.connect(self.db_path)
        try:
            if target_date:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? AND date <= ? ORDER BY date DESC LIMIT ?",
                    (variable, target_date, window),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT ?",
                    (variable, window),
                ).fetchall()
            values = [r[0] for r in rows if r[0] is not None]
            return float(np.mean(values)) if values else None
        except Exception:
            return None
        finally:
            conn.close()

    def _normalize_indicator(self, variable: str, value: Optional[float], invert: bool = False) -> Optional[float]:
        """Normalize a macro indicator to [0, 1]."""
        if value is None:
            return None
        bounds = NORMALIZATION_BOUNDS.get(variable)
        if not bounds:
            return None
        return _normalize(value, bounds["low"], bounds["high"], invert)

    def _compute_node_score(
        self,
        node_name: str,
        indicator_scores: dict,
        data_quality: dict,
    ) -> float:
        """Compute weighted average score for a macro node."""
        weights = NODE_COMPOSITE_WEIGHTS.get(node_name, {})
        total_weight = 0.0
        weighted_sum = 0.0

        for indicator, weight in weights.items():
            score = indicator_scores.get(indicator)
            has_data = data_quality.get(indicator, False)
            if has_data and score is not None:
                weighted_sum += score * weight
                total_weight += weight

        if total_weight > 0:
            return weighted_sum / total_weight
        return 0.5  # neutral when no data

    def compute(self, target_date: Optional[str] = None) -> RegionalMacroResult:
        """Compute Macro State Vector M.

        Returns:
            RegionalMacroResult with M vector and diagnostics
        """
        # ── Fetch all indicators ──────────────────────────────────────
        indicators = {}

        # US Liquidity
        indicators["US_FED_RATE"] = self._fetch_latest("FED_TARGET_RATE", target_date)
        indicators["DXY"] = self._fetch_rolling_avg("DXY", 5, target_date)
        indicators["US10Y"] = self._fetch_rolling_avg("US10Y", 5, target_date)
        indicators["VIX"] = self._fetch_rolling_avg("VIX", 5, target_date)

        # China Economy
        indicators["USDCNY"] = self._fetch_latest("USD_CNY", target_date)

        # Commodity Cycle
        indicators["BRENT_OIL"] = self._fetch_rolling_avg("BRENT_OIL", 5, target_date)
        copper_lb = self._fetch_rolling_avg("COPPER_HG", 5, target_date)
        # WHY: DB stores COPPER in USD/lb (HG=F), but normalization bounds
        # are in USD/ton (6000-11000). Convert: 1 metric ton = 2204.62 lbs.
        indicators["COPPER"] = copper_lb * 2204.62 if copper_lb is not None else None

        # Domestic Liquidity
        indicators["INTERBANK_ON"] = self._fetch_latest("INTERBANK_ON", target_date)
        indicators["VNINDEX"] = self._fetch_latest("VNINDEX", target_date)

        # ── Normalize indicators ──────────────────────────────────────
        indicator_scores = {}
        data_quality = {}

        # Invert indicators where HIGHER = WORSE for liquidity
        invert_map = {"US_FED_RATE", "VIX", "DXY", "US10Y", "USDCNY", "INTERBANK_ON"}

        for variable, value in indicators.items():
            invert = variable in invert_map
            normalized = self._normalize_indicator(variable, value, invert)
            indicator_scores[variable] = normalized
            data_quality[variable] = value is not None and normalized is not None

        # ── Compute node scores ───────────────────────────────────────
        node_details = {}
        macro_vector = {}

        for node_name in ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]:
            score = self._compute_node_score(node_name, indicator_scores, data_quality)
            macro_vector[node_name] = round(score, 4)

            # Decompose node into constituent indicators
            weights = NODE_COMPOSITE_WEIGHTS.get(node_name, {})
            details = {}
            for indicator, weight in weights.items():
                s = indicator_scores.get(indicator)
                details[indicator] = {
                    "raw": indicators.get(indicator),
                    "normalized": round(s, 4) if s is not None else None,
                    "weight": weight,
                }
            node_details[node_name] = details

        # ── Compute momentum (direction of change) ──────────────────
        momentum = {}
        for node_name in ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]:
            weights = NODE_COMPOSITE_WEIGHTS.get(node_name, {})
            current_score = macro_vector[node_name]

            # Fetch historical score (30 days ago) for comparison
            past_score = self._compute_historical_node_score(
                node_name, target_date, lookback_days=30
            )
            if past_score is not None:
                delta = current_score - past_score
                momentum[node_name] = round(delta, 4)
            else:
                momentum[node_name] = None

        # ── Compute confidence (data completeness per node) ─────────
        confidence = {}
        for node_name in ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]:
            weights = NODE_COMPOSITE_WEIGHTS.get(node_name, {})
            total = len(weights)
            has_data = sum(
                1 for ind in weights
                if data_quality.get(ind, False)
            )
            confidence[node_name] = round(has_data / total, 2) if total > 0 else 0.0

        return RegionalMacroResult(
            macro_vector=macro_vector,
            component_scores=indicator_scores,
            data_quality=data_quality,
            node_details=node_details,
            momentum=momentum,
            confidence=confidence,
        )

    def _compute_historical_node_score(
        self, node_name: str, target_date: Optional[str], lookback_days: int = 30
    ) -> Optional[float]:
        """Compute node score at a historical date for momentum comparison."""
        if not target_date:
            return None
        try:
            from datetime import datetime, timedelta
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            past_dt = dt - timedelta(days=lookback_days)
            past_date = past_dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None

        # Fetch historical indicators
        indicators = {}
        indicators["US_FED_RATE"] = self._fetch_latest("FED_TARGET_RATE", past_date)
        indicators["DXY"] = self._fetch_rolling_avg("DXY", 5, past_date)
        indicators["US10Y"] = self._fetch_rolling_avg("US10Y", 5, past_date)
        indicators["VIX"] = self._fetch_rolling_avg("VIX", 5, past_date)
        indicators["USDCNY"] = self._fetch_latest("USD_CNY", past_date)
        indicators["BRENT_OIL"] = self._fetch_rolling_avg("BRENT_OIL", 5, past_date)
        copper_lb = self._fetch_rolling_avg("COPPER_HG", 5, past_date)
        indicators["COPPER"] = copper_lb * 2204.62 if copper_lb is not None else None
        indicators["INTERBANK_ON"] = self._fetch_latest("INTERBANK_ON", past_date)
        indicators["VNINDEX"] = self._fetch_latest("VNINDEX", past_date)

        invert_map = {"US_FED_RATE", "VIX", "DXY", "US10Y", "USDCNY", "INTERBANK_ON"}
        indicator_scores = {}
        data_quality = {}
        for variable, value in indicators.items():
            invert = variable in invert_map
            normalized = self._normalize_indicator(variable, value, invert)
            indicator_scores[variable] = normalized
            data_quality[variable] = value is not None and normalized is not None

        return self._compute_node_score(node_name, indicator_scores, data_quality)


def compute_macro_vector(target_date: Optional[str] = None, db_path: Optional[str] = None) -> dict:
    """Convenience function: returns just the M vector dict."""
    engine = RegionalInfluenceEngine(db_path)
    result = engine.compute(target_date)
    return result.macro_vector
