"""macro_lag_engine.py — Macro Transmission Delay & Exponential Decay.

LAW-009 (Causal Delay & Interaction Principle):
  Macro signals do not transmit instantaneously. Each macro→sector link
  has a characteristic lag_range and half_life. The effective macro signal
  for a sector is NOT the latest M vector, but a time-decay-weighted
  integral of historical M vectors.

Architecture:
  Macro_Lag_Engine:
    1. Fetch historical M vectors (lookback = max_lag across all sectors)
    2. For each sector, compute effective signal:
       S_eff(sector, t) = Σ M(t-d) × 0.5^(d / HL_sector) / Σ 0.5^(d / HL_sector)
    3. Return lag-adjusted macro scores per sector

Mathematical Formulation:
  Let M(t) = [m_US(t), m_CN(t), m_CM(t), m_DN(t)] be the macro vector at time t.
  Let HL_s = half_life of sector s (days until 50% signal decay).
  Let L_s = lag_range of sector s (min, max days of transmission).

  Effective signal:
    S_eff(s, t) = (1/Z) × Σ_{d=0}^{L_max} M(t-d) × 0.5^(d / HL_s)
    where Z = Σ_{d=0}^{L_max} 0.5^(d / HL_s) is the normalization constant.

  Properties:
    - Recent signals (d ≈ 0) have weight ≈ 1.0
    - Signals at d = HL_s have weight = 0.5
    - Signals beyond L_max are excluded (not relevant)
    - S_eff ∈ [0, 1] because M ∈ [0, 1] and weights are positive

Why This Matters for HPG:
  STEEL sector has short half_life (15d) and short lag (3-21d).
  When China_Economy improves TODAY, the M vector rises.
  But HPG's effective signal should reflect the 3-21 day window,
  not just today's snapshot. If China was weak for 30 days and
  just turned positive yesterday, the effective signal is still
  depressed by the recent weakness. This prevents false positives.

Usage:
  engine = MacroLagEngine(db_path)
  result = engine.compute(sector="STEEL", target_date="2026-08-04")
  print(result.effective_score)  # 0.55 (lag-adjusted)
  print(result.raw_score)        # 0.70 (latest M vector)
  print(result.signal_deficit)   # -0.15 (raw - effective)
"""

import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.errors import AnalysisError
from governor.sector_exposure_matrix import SectorExposureMatrix

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

# ── Macro Node Labels ─────────────────────────────────────────────────
MACRO_NODES = ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]

# ── Transmission Parameters (from CausalEdge + expert calibration) ────
# WHY these values:
#   lag_min/lag_max: from CausalEdge registry (causal_edge.py)
#   half_life: from CausalEdge.half_life (signal decay rate)
#   STEEL: China→HRC price is fast (3-21d, HL=15d). HRC spot reprices weekly.
#   BANK:  Interest rate→NIM is slow (15-60d, HL=45d). Loan book reprices quarterly.
#   RE:    Interest rate→presales is very slow (30-120d, HL=90d). Mortgage cycle.
#   OIL:   Brent→refining margin is moderate (10-45d, HL=30d).
#   TECH:  AI capex→backlog is slow (30-120d, HL=60d). Enterprise procurement.
#   TRANS: China trade volume→port throughput is moderate (7-30d, HL=20d).


@dataclass
class TransmissionParameters:
    """Lag and decay parameters for a sector's macro transmission."""

    sector: str
    lag_min: int  # Minimum transmission delay (trading days)
    lag_max: int  # Maximum transmission delay (trading days)
    half_life: float  # Signal half-life (days until 50% decay)
    attenuation: float  # Information loss per hop [0, 1]
    description: str = ""

    @property
    def lookback(self) -> int:
        """Effective lookback window = lag_max + safety margin."""
        return self.lag_max + 5


# ── Sector Transmission Parameters ────────────────────────────────────
# Calibrated from CausalEdge + domain expertise.
# Each sector has UNIQUE lag characteristics because the transmission
# mechanism (price discovery, contract repricing, inventory cycle) differs.

SECTOR_TRANSMISSION: dict[str, TransmissionParameters] = {
    "STEEL": TransmissionParameters(
        sector="STEEL",
        lag_min=3,
        lag_max=21,
        half_life=15.0,
        attenuation=0.10,
        description="HRC spot reprices weekly. China SHFE→VN HRC 3-21 days.",
    ),
    "BANK": TransmissionParameters(
        sector="BANK",
        lag_min=15,
        lag_max=60,
        half_life=45.0,
        attenuation=0.15,
        description="Interest rate→NIM repricing. Floating loans reprice quarterly.",
    ),
    "SEC": TransmissionParameters(
        sector="SEC",
        lag_min=5,
        lag_max=30,
        half_life=20.0,
        attenuation=0.10,
        description="Risk appetite→margin lending. Fast reaction to VIX/sentiment.",
    ),
    "RE": TransmissionParameters(
        sector="RE",
        lag_min=30,
        lag_max=120,
        half_life=90.0,
        attenuation=0.20,
        description="Rate→mortgage capacity→presales. Long decision cycle.",
    ),
    "OIL": TransmissionParameters(
        sector="OIL",
        lag_min=10,
        lag_max=45,
        half_life=30.0,
        attenuation=0.10,
        description="Brent→refining margin. Contract lag + spot repricing.",
    ),
    "UTILITY": TransmissionParameters(
        sector="UTILITY",
        lag_min=30,
        lag_max=180,
        half_life=90.0,
        attenuation=0.25,
        description="Tariff policy→revenue. Regulatory decision lag.",
    ),
    "CONST": TransmissionParameters(
        sector="CONST",
        lag_min=15,
        lag_max=60,
        half_life=45.0,
        attenuation=0.20,
        description="Construction activity→steel order. Project cycle lag.",
    ),
    "CONSUMER": TransmissionParameters(
        sector="CONSUMER",
        lag_min=5,
        lag_max=30,
        half_life=20.0,
        attenuation=0.15,
        description="Consumer spending→retail sales. Fast but noisy.",
    ),
    "FOOD": TransmissionParameters(
        sector="FOOD",
        lag_min=10,
        lag_max=45,
        half_life=30.0,
        attenuation=0.15,
        description="Agricultural commodity→input cost. Contract repricing.",
    ),
    "TECH": TransmissionParameters(
        sector="TECH",
        lag_min=30,
        lag_max=120,
        half_life=60.0,
        attenuation=0.20,
        description="AI capex→IT backlog. Enterprise procurement cycle.",
    ),
    "TRANS": TransmissionParameters(
        sector="TRANS",
        lag_min=7,
        lag_max=30,
        half_life=20.0,
        attenuation=0.15,
        description="China trade→port throughput. Shipping cycle lag.",
    ),
}

DEFAULT_TRANSMISSION = TransmissionParameters(
    sector="DEFAULT",
    lag_min=10,
    lag_max=45,
    half_life=30.0,
    attenuation=0.15,
    description="Default: moderate lag for unknown sectors.",
)


# ── Lag Result ────────────────────────────────────────────────────────


@dataclass
class LagResult:
    """Result of lag-adjusted macro signal computation."""

    sector: str
    effective_score: float  # Lag-adjusted macro score ∈ [0, 1]
    raw_score: float  # Latest M vector dot product (no lag)
    signal_deficit: float  # raw - effective (negative = still catching up)
    effective_vector: dict[str, float]  # Per-node effective scores
    raw_vector: dict[str, float]  # Per-node raw scores
    lookback_used: int  # Actual lookback window
    half_life: float  # Sector half_life used
    data_points: int  # Number of historical M vectors used
    weights: dict[int, float]  # Day→weight mapping (for diagnostics)


# ── Macro Lag Engine ──────────────────────────────────────────────────


class MacroLagEngine:
    """Compute lag-adjusted macro signals with exponential time decay.

    Architecture:
      1. Fetch historical macro indicator values from DB
      2. Reconstruct historical M vectors (one per day)
      3. For target sector, apply exponential decay weighting
      4. Return effective macro score (lag-adjusted)

    Usage:
      engine = MacroLagEngine(db_path)
      result = engine.compute("STEEL", target_date="2026-08-04")
      # result.effective_score: lag-adjusted score
      # result.signal_deficit: how much the signal is "behind"
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")

    def get_transmission(self, sector: str) -> TransmissionParameters:
        """Get transmission parameters for a sector."""
        return SECTOR_TRANSMISSION.get(sector, DEFAULT_TRANSMISSION)

    def _fetch_macro_history(self, variable: str, lookback: int, target_date: str | None = None) -> list[tuple[str, float]]:
        """Fetch recent history for a macro variable.

        Returns list of (date, value) tuples, most recent first.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            if target_date:
                rows = conn.execute(
                    """SELECT date, value FROM macro_history
                       WHERE variable = ? AND date <= ?
                       ORDER BY date DESC LIMIT ?""",
                    (variable, target_date, lookback),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT date, value FROM macro_history
                       WHERE variable = ?
                       ORDER BY date DESC LIMIT ?""",
                    (variable, lookback),
                ).fetchall()
            return [(r[0], r[1]) for r in rows if r[1] is not None]
        except sqlite3.Error as e:
            logger.warning("[LAG] Failed to fetch %s: %s", variable, e)
            return []
        finally:
            conn.close()

    def _fetch_vnindex_history(self, lookback: int, target_date: str | None = None) -> list[tuple[str, float]]:
        """Fetch VNINDEX from daily_ohlcv (fallback for macro_history)."""
        conn = sqlite3.connect(self.db_path)
        try:
            if target_date:
                rows = conn.execute(
                    """SELECT date, close FROM daily_ohlcv
                       WHERE symbol = 'VNINDEX' AND date <= ?
                       ORDER BY date DESC LIMIT ?""",
                    (target_date, lookback),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT date, close FROM daily_ohlcv
                       WHERE symbol = 'VNINDEX'
                       ORDER BY date DESC LIMIT ?""",
                    (lookback,),
                ).fetchall()
            return [(r[0], r[1]) for r in rows if r[1] is not None]
        except sqlite3.Error as e:
            logger.warning("[LAG] Failed to fetch VNINDEX: %s", e)
            return []
        finally:
            conn.close()

    def _reconstruct_historical_vectors(self, lookback: int, target_date: str | None = None) -> list[dict[str, float]]:
        """Reconstruct historical M vectors for the lookback window.

        Returns list of M vectors, most recent first.
        Each M vector is a dict mapping macro nodes to normalized [0,1] scores.

        WHY: Instead of fetching pre-computed M vectors (which don't exist
        in DB), we fetch raw indicator histories and normalize them.
        This is more robust because we can handle missing data per-day.
        """
        from governor.regional_influence_engine import (
            NODE_COMPOSITE_WEIGHTS,
            NORMALIZATION_BOUNDS,
            _normalize,
        )

        # ── Fetch indicator histories ────────────────────────────────
        # Map: indicator_variable → macro_node
        indicator_map = {
            "FED_TARGET_RATE": ("US_Liquidity", "US_FED_RATE", True),  # invert
            "DXY": ("US_Liquidity", "DXY", True),  # invert
            "US10Y": ("US_Liquidity", "US10Y", True),  # invert
            "VIX": ("US_Liquidity", "VIX", True),  # invert
            "USD_CNY": ("China_Economy", "USDCNY", True),  # invert
            "BRENT_OIL": ("Commodity_Cycle", "BRENT_OIL", False),  # direct
            "COPPER_HG": ("Commodity_Cycle", "COPPER", False),  # direct (lb→ton)
            "INTERBANK_ON": ("Domestic_Liquidity", "INTERBANK_ON", True),  # invert
        }

        histories: dict[str, list[tuple[str, float]]] = {}
        for var in indicator_map:
            if var == "COPPER_HG":
                raw = self._fetch_rolling_avg_history(var, lookback, target_date)
                histories[var] = [(d, v * 2204.62 if v is not None else None) for d, v in raw]
            elif var == "VNINDEX":
                histories[var] = self._fetch_vnindex_history(lookback, target_date)
            else:
                histories[var] = self._fetch_rolling_avg_history(var, lookback, target_date)

        # Also fetch VNINDEX for Domestic_Liquidity
        histories["VNINDEX"] = self._fetch_vnindex_history(lookback, target_date)

        # ── Align by date ────────────────────────────────────────────
        # Collect all dates across all indicators
        all_dates: set = set()
        for var_history in histories.values():
            for date_str, _ in var_history:
                all_dates.add(date_str)

        sorted_dates = sorted(all_dates, reverse=True)[:lookback]

        # ── Build M vectors per date ─────────────────────────────────
        m_vectors: list[dict[str, float]] = []

        for date_str in sorted_dates:
            indicator_scores: dict[str, float | None] = {}
            data_quality: dict[str, bool] = {}

            for var, (node, norm_key, invert) in indicator_map.items():
                # Find value for this date
                value = None
                for d, v in histories.get(var, []):
                    if d == date_str:
                        value = v
                        break

                bounds = NORMALIZATION_BOUNDS.get(norm_key, {})
                low, high = bounds.get("low", 0), bounds.get("high", 1)
                normalized = _normalize(value, low, high, invert) if value is not None else None
                indicator_scores[norm_key] = normalized
                data_quality[norm_key] = value is not None and normalized is not None

            # Also get VNINDEX for Domestic_Liquidity
            vnindex_value = None
            for d, v in histories.get("VNINDEX", []):
                if d == date_str:
                    vnindex_value = v
                    break
            if vnindex_value is not None:
                vnindex_norm = _normalize(vnindex_value, 800.0, 1500.0, False)
                indicator_scores["VNINDEX"] = vnindex_norm
                data_quality["VNINDEX"] = True

            # Compute node scores
            m_vector = {}
            for node_name in MACRO_NODES:
                weights = NODE_COMPOSITE_WEIGHTS.get(node_name, {})
                total_weight = 0.0
                weighted_sum = 0.0
                for indicator, weight in weights.items():
                    score = indicator_scores.get(indicator)
                    has_data = data_quality.get(indicator, False)
                    if has_data and score is not None:
                        weighted_sum += score * weight
                        total_weight += weight
                m_vector[node_name] = weighted_sum / total_weight if total_weight > 0 else 0.5

            m_vectors.append(m_vector)

        return m_vectors

    def _fetch_rolling_avg_history(
        self, variable: str, window: int, target_date: str | None = None
    ) -> list[tuple[str, float]]:
        """Fetch raw values for rolling average computation."""
        conn = sqlite3.connect(self.db_path)
        try:
            if target_date:
                rows = conn.execute(
                    """SELECT date, value FROM macro_history
                       WHERE variable = ? AND date <= ?
                       ORDER BY date DESC LIMIT ?""",
                    (variable, target_date, window),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT date, value FROM macro_history
                       WHERE variable = ?
                       ORDER BY date DESC LIMIT ?""",
                    (variable, window),
                ).fetchall()
            return [(r[0], r[1]) for r in rows if r[1] is not None]
        except sqlite3.Error as e:
            logger.warning("[LAG] Rolling avg fetch failed for %s: %s", variable, e)
            return []
        finally:
            conn.close()

    def _compute_decay_weights(self, lookback: int, half_life: float) -> dict[int, float]:
        """Compute exponential decay weights for each day offset.

        Returns dict: day_offset → weight.
        Weight = 0.5^(day_offset / half_life).
        """
        weights = {}
        for d in range(lookback):
            weights[d] = 0.5 ** (d / half_life) if half_life > 0 else (1.0 if d == 0 else 0.0)
        return weights

    def compute(
        self,
        sector: str,
        target_date: str | None = None,
        custom_transmission: TransmissionParameters | None = None,
    ) -> LagResult:
        """Compute lag-adjusted macro signal for a sector.

        Args:
            sector: Sector code (e.g., "STEEL", "BANK")
            target_date: Evaluation date (YYYY-MM-DD)
            custom_transmission: Override transmission parameters

        Returns:
            LagResult with effective vs raw scores
        """
        tx = custom_transmission or self.get_transmission(sector)
        lookback = tx.lookback

        # ── Reconstruct historical M vectors ────────────────────────
        m_vectors = self._reconstruct_historical_vectors(lookback, target_date)

        if not m_vectors:
            # No data: return neutral
            return LagResult(
                sector=sector,
                effective_score=0.5,
                raw_score=0.5,
                signal_deficit=0.0,
                effective_vector={n: 0.5 for n in MACRO_NODES},
                raw_vector={n: 0.5 for n in MACRO_NODES},
                lookback_used=0,
                half_life=tx.half_life,
                data_points=0,
                weights={},
            )

        # ── Compute raw score (latest M vector) ─────────────────────
        raw_vector = m_vectors[0]  # most recent
        raw_score = sum(raw_vector.values()) / len(raw_vector) if raw_vector else 0.5

        # ── Compute decay weights ────────────────────────────────────
        decay_weights = self._compute_decay_weights(lookback, tx.half_life)

        # ── Compute effective score per node ─────────────────────────
        effective_vector: dict[str, float] = {}
        weights_used: dict[int, float] = {}

        for node in MACRO_NODES:
            weighted_sum = 0.0
            weight_sum = 0.0

            for d, m_vec in enumerate(m_vectors):
                if d >= lookback:
                    break
                w = decay_weights.get(d, 0.0)
                m_val = m_vec.get(node, 0.5)
                weighted_sum += m_val * w
                weight_sum += w
                weights_used[d] = w

            if weight_sum > 0:
                effective_vector[node] = weighted_sum / weight_sum
            else:
                effective_vector[node] = 0.5

        # ── Compute effective aggregate score ────────────────────────
        effective_score = sum(effective_vector.values()) / len(effective_vector)

        # ── Apply attenuation (information loss) ─────────────────────
        # WHY: Even with perfect lag correction, some information is
        # lost in transmission. Attenuation reduces effective score
        # toward 0.5 (neutral) by the attenuation factor.
        if tx.attenuation > 0:
            effective_score = effective_score * (1.0 - tx.attenuation) + 0.5 * tx.attenuation
            for node in effective_vector:
                effective_vector[node] = effective_vector[node] * (1.0 - tx.attenuation) + 0.5 * tx.attenuation

        # ── Clamp to [0, 1] ─────────────────────────────────────────
        effective_score = max(0.0, min(1.0, effective_score))

        # ── Signal deficit ───────────────────────────────────────────
        # Negative = raw is higher than effective (signal still propagating)
        # Positive = effective is higher (signal already baked in)
        signal_deficit = raw_score - effective_score

        return LagResult(
            sector=sector,
            effective_score=round(effective_score, 6),
            raw_score=round(raw_score, 6),
            signal_deficit=round(signal_deficit, 6),
            effective_vector={k: round(v, 6) for k, v in effective_vector.items()},
            raw_vector={k: round(v, 6) for k, v in raw_vector.items()},
            lookback_used=min(lookback, len(m_vectors)),
            half_life=tx.half_life,
            data_points=len(m_vectors),
            weights={d: round(w, 6) for d, w in sorted(weights_used.items())[:10]},
        )

    def compute_all_sectors(self, target_date: str | None = None) -> dict[str, LagResult]:
        """Compute lag-adjusted signals for all sectors.

        Returns:
            Dict mapping sector codes to LagResult
        """
        results = {}
        for sector in SECTOR_TRANSMISSION:
            results[sector] = self.compute(sector, target_date)
        return results

    def compute_persistence(self, sector: str, target_date: str | None = None) -> float:
        """Compute signal persistence for a sector.

        Persistence measures how stable the macro signal direction has been
        over recent periods. High persistence = consistent trend.
        Low persistence = volatile / noisy signal.

        Formula:
          persistence = (consecutive same-direction periods) / (total periods checked)

        Returns:
            float ∈ [0, 1] — 1.0 = perfectly stable, 0.0 = completely volatile
        """
        # Fetch raw M vectors over last 90 days
        lookback_days = 90
        if target_date:
            try:
                dt = datetime.strptime(target_date, "%Y-%m-%d")
                end_dt = dt
            except ValueError, TypeError:
                end_dt = datetime.now()
        else:
            end_dt = datetime.now()

        m_vectors = []
        for d in range(0, lookback_days, 5):  # sample every 5 days
            check_date = (end_dt - timedelta(days=d)).strftime("%Y-%m-%d")
            try:
                vectors = self._reconstruct_historical_vectors(1, check_date)
                if vectors:
                    m_vectors.append(vectors[0])
            except (AnalysisError, ValueError, TypeError) as e:
                logger.debug("[LAG] Skipping date %s in persistence calc: %s", check_date, e)
                continue

        if len(m_vectors) < 3:
            return 0.5  # insufficient data, neutral persistence

        # Compute sector score at each point in time
        matrix = SectorExposureMatrix()
        scores = []
        for m in m_vectors:
            result = matrix.compute_sector_macro_score(sector, m)
            scores.append(result.macro_score)

        # Count direction changes
        direction_changes = 0
        for i in range(1, len(scores)):
            prev_delta = scores[i - 1] - scores[i - 2] if i >= 2 else 0
            curr_delta = scores[i] - scores[i - 1]
            # Direction change if signs differ (and both non-zero)
            if prev_delta * curr_delta < 0 and abs(prev_delta) > 0.01 and abs(curr_delta) > 0.01:
                direction_changes += 1

        total_transitions = len(scores) - 1
        if total_transitions <= 0:
            return 0.5

        persistence = 1.0 - (direction_changes / total_transitions)
        return round(max(0.0, min(1.0, persistence)), 4)
