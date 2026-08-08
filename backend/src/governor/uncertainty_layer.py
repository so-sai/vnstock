"""uncertainty_layer.py — Uncertainty Layer (Uncertainty ≠ Signal).

Uncertainty U ∈ [0, 1] is a RISK MODIFIER. It is NEVER added to any composite
score (orthogonality law). It only scales capital allocation:

    Alloc_final = Alloc_Kelly × Confidence × (1 - U_shock)

U is a weighted blend of five independent components, each computed from
source-of-truth tables in screener_cache.db, strictly point-in-time:

    U = w_C·(1-C) + w_F·(1-F) + w_R·(1-R) + w_A·(1-A) + w_P·(1-P)

  C Completeness — fraction of macro indicators with data per node
                    (macro_history + daily_ohlcv fallback)
  F Freshness    — exponential decay by data lag (days) vs target_date
                    (macro_history.date; fresh_score is useless: all 1.0)
  R Reliability  — source quality from macro_history_v2 (yahoo=1.0,
                    unknown=0.4, missing=0.5 neutral)
  A Agreement    — cross-variable consistency: 1 - std of normalized
                    indicator scores within a node (single-source reality:
                    agreement is cross-variable, NOT cross-source)
  P Persistence  — regime stability from regime_history (atr_ratio low +
                    vol_score low → stable → U down)

Each component is a "trust" value (higher = more trustworthy), so U = 1 - blend.
No provenance = no trust: every result exposes source_trace citing the exact
table/field each component came from.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Component weights (sum = 1.0) ──────────────────────────────────────────
COMPONENT_WEIGHTS = {
    "C": 0.30,  # Completeness — highest: missing data is the worst failure
    "F": 0.25,  # Freshness
    "R": 0.15,  # Reliability (single source reality: bounded value)
    "A": 0.15,  # Agreement (cross-variable)
    "P": 0.15,  # Persistence
}

NODES = ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]

# Variables required per macro node (mirror regional_influence_engine).
NODE_VARIABLES = {
    "US_Liquidity": ["FED_TARGET_RATE", "DXY", "US10Y", "VIX"],
    "China_Economy": ["USD_CNY", "BRENT_OIL", "COPPER_HG"],
    "Commodity_Cycle": ["BRENT_OIL", "COPPER_HG", "DXY"],
    "Domestic_Liquidity": ["INTERBANK_ON", "VNINDEX", "US10Y"],
}

# Variables whose higher value means WORSE liquidity (mirror engine invert_map).
INVERT_MAP = {"FED_TARGET_RATE", "VIX", "DXY", "US10Y", "USD_CNY", "INTERBANK_ON"}

# Normalization bounds (mirror regional_influence_engine).
NORMALIZATION_BOUNDS = {
    "FED_TARGET_RATE": {"low": 0.0, "high": 5.5},
    "DXY": {"low": 90.0, "high": 115.0},
    "US10Y": {"low": 0.5, "high": 5.0},
    "VIX": {"low": 12.0, "high": 35.0},
    "USD_CNY": {"low": 6.3, "high": 7.5},
    "BRENT_OIL": {"low": 40.0, "high": 120.0},
    "COPPER_HG": {"low": 6000.0, "high": 11000.0},
    "INTERBANK_ON": {"low": 2.0, "high": 8.0},
    "VNINDEX": {"low": 800.0, "high": 1500.0},
}

# Source → reliability score.
SOURCE_RELIABILITY = {
    "yahoo": 1.0,
    "unknown": 0.4,
}

# Freshness half-life (days): 14 days stale halves the trust.
FRESHNESS_HALF_LIFE_DAYS = 14.0
# Data older than this (days) is fully untrustworthy for macro signals.
FRESHNESS_FLOOR_DAYS = 90.0

# Uncertainty bands.
LOW_THRESHOLD = 0.33
MEDIUM_THRESHOLD = 0.66

# Copper stored in USD/lb; bounds in USD/ton (1 metric ton = 2204.62 lbs).
LB_PER_TON = 2204.62


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


def _normalize(value: float, low: float, high: float, invert: bool = False) -> float:
    """Normalize to [0, 1]; invert=True for inverse indicators."""
    if high <= low:
        return 0.5
    clamped = max(low, min(high, value))
    norm = (clamped - low) / (high - low)
    return 1.0 - norm if invert else norm


def _as_date(target_date: str) -> date:
    return datetime.strptime(target_date, "%Y-%m-%d").date()


def classify_uncertainty(u: float) -> str:
    """Map U ∈ [0,1] to LOW / MEDIUM / HIGH band."""
    if u < LOW_THRESHOLD:
        return "LOW"
    if u < MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "HIGH"


@dataclass(frozen=True)
class UncertaintyResult:
    """Result of the Uncertainty Layer computation (immutable, PIT)."""

    target_date: str
    db_path: str
    u: float
    band: str
    components: dict = field(default_factory=dict)
    per_node: dict = field(default_factory=dict)
    per_node_components: dict = field(default_factory=dict)
    source_trace: dict = field(default_factory=dict)


class UncertaintyLayer:
    """Compute U = f(C, F, R, A, P) for a given point-in-time date.

    Pure read-only: opens its own connection to screener_cache.db, uses only
    data with date <= target_date (PIT), and never writes.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DEFAULT_DB
        self._snapshot: dict | None = None

    # ── PIT snapshot (loaded once, then bisect lookups) ───────────────────
    def _load_snapshot(self) -> dict:
        """Load the minimal PIT dataset into memory once.

        macro_history has NO index (full scan per query). Loading the required
        variables once (lazy cache) then bisect-searching per date turns
        ~370ms/date into ~1ms/date while keeping the same PIT semantics.
        """
        if self._snapshot is not None:
            return self._snapshot

        wanted = sorted({v for vars_ in NODE_VARIABLES.values() for v in vars_})
        placeholders = ",".join("?" * len(wanted))

        conn = sqlite3.connect(self.db_path)
        try:
            macro_series: dict[str, list[tuple[str, float]]] = {v: [] for v in wanted}
            if placeholders:
                for variable, date, value in conn.execute(
                    f"SELECT variable, date, value FROM macro_history WHERE variable IN ({placeholders})",
                    tuple(wanted),
                ):
                    if value is not None:
                        macro_series[variable].append((date, float(value)))
            # bisect requires ascending order per variable (YYYY-MM-DD lexical).
            for v in macro_series:
                macro_series[v].sort(key=lambda x: x[0])

            # macro_history_v2 → source per variable, PIT (date-tagged).
            source_by_var: dict[str, list[tuple[str, str]]] = {v: [] for v in wanted}
            if placeholders:
                for variable, date, source in conn.execute(
                    f"SELECT variable, date, source FROM macro_history_v2 WHERE variable IN ({placeholders})",
                    tuple(wanted),
                ):
                    if source:
                        source_by_var[variable].append((date, source))
            for v in source_by_var:
                source_by_var[v].sort(key=lambda x: x[0])

            vnindex_rows = conn.execute(
                "SELECT date, close FROM daily_ohlcv WHERE symbol = 'VNINDEX' ORDER BY date ASC"
            ).fetchall()
            vnindex_series = [(d, float(c)) for d, c in vnindex_rows if c is not None]

            regime_rows = conn.execute("SELECT date, atr_ratio, vol_score FROM regime_history ORDER BY date ASC").fetchall()
            regime_series = [(d, float(atr), float(vol or 0.0)) for d, atr, vol in regime_rows if atr is not None]

            self._snapshot = {
                "macro_series": macro_series,
                "source_by_var": source_by_var,
                "vnindex_series": vnindex_series,
                "regime_series": regime_series,
            }
            return self._snapshot
        finally:
            conn.close()

    @staticmethod
    def _latest_le(series: list[tuple[str, float]] | list[tuple[str, float, float]], target_date: str) -> tuple | None:
        """Latest entry with date <= target_date via binary search.

        series must be sorted ascending by date (YYYY-MM-DD is lexicographic).
        Returns the whole row or None. Value is the SECOND element.
        """
        import bisect

        dates = [entry[0] for entry in series] if series else []
        idx = bisect.bisect_right(dates, target_date) - 1
        if idx < 0:
            return None
        return series[idx]

    def _latest_value(self, variable: str, target_date: str) -> tuple[float, str] | None:
        """(value, date) latest <= target_date. VNINDEX falls back to daily_ohlcv."""
        snap = self._load_snapshot()
        macro_series = snap["macro_series"].get(variable, [])
        row = self._latest_le(macro_series, target_date)
        if row:
            return float(row[1]), row[0]
        if variable == "VNINDEX":
            row = self._latest_le(snap["vnindex_series"], target_date)
            if row:
                return float(row[1]), row[0]
        return None

    def _latest_date_for(self, variable: str, target_date: str) -> str | None:
        """Latest date <= target_date for a variable (macro_history)."""
        snap = self._load_snapshot()
        macro_series = snap["macro_series"].get(variable, [])
        row = self._latest_le(macro_series, target_date)
        if row:
            return row[0]
        if variable == "VNINDEX":
            row = self._latest_le(snap["vnindex_series"], target_date)
            return row[0] if row else None
        return None

    # ── Component C: Completeness ─────────────────────────────────────────
    def _completeness(self, target_date: str) -> dict:
        """C per node = fraction of required variables with data."""
        c_node = {}
        for node, variables in NODE_VARIABLES.items():
            if not variables:
                continue
            have = sum(1 for v in variables if self._latest_value(v, target_date) is not None)
            c_node[node] = have / len(variables)
        return c_node

    # ── Component F: Freshness ────────────────────────────────────────────
    def _freshness(self, target_date: str) -> dict:
        """F per node = mean freshness across required variables.

        Freshness per variable = exponential decay by lag days vs target_date.
        """
        target_dt = _as_date(target_date)
        f_node = {}
        for node, variables in NODE_VARIABLES.items():
            lags = []
            for v in variables:
                d = self._latest_date_for(v, target_date)
                if not d:
                    lags.append(FRESHNESS_FLOOR_DAYS)
                    continue
                try:
                    lag = (target_dt - _as_date(d)).days
                except ValueError:
                    lag = FRESHNESS_FLOOR_DAYS
                lags.append(max(0, lag))
            if not lags:
                f_node[node] = 0.0
                continue
            mean_lag = sum(lags) / len(lags)
            if mean_lag >= FRESHNESS_FLOOR_DAYS:
                f_node[node] = 0.0
            else:
                f_node[node] = math.exp(-mean_lag * math.log(2) / FRESHNESS_HALF_LIFE_DAYS)
        return f_node

    # ── Component R: Reliability ──────────────────────────────────────────
    def _reliability(self, target_date: str) -> dict:
        """R per node = mean source reliability of required variables.

        Source read from macro_history_v2 (the only table with source).
        Missing mapping → neutral 0.5 (no provenance = reduced trust).
        """
        snap = self._load_snapshot()
        r_node = {}
        for node, variables in NODE_VARIABLES.items():
            scores = []
            for v in variables:
                series = snap["source_by_var"].get(v, [])
                row = self._latest_le(series, target_date)
                src = row[1] if row else None
                scores.append(SOURCE_RELIABILITY.get(src, 0.5))
            r_node[node] = sum(scores) / len(scores) if scores else 0.5
        return r_node

    # ── Component A: Agreement ────────────────────────────────────────────
    def _normalized_scores(self, target_date: str) -> dict:
        """Per-variable normalized score [0,1] (mirror engine normalization)."""
        scores = {}
        for v in NORMALIZATION_BOUNDS:
            pair = self._latest_value(v, target_date)
            if pair is None:
                scores[v] = None
                continue
            value = pair[0]
            if v == "COPPER_HG":
                value = value * LB_PER_TON
                bounds_var = "COPPER_HG"
            else:
                bounds_var = v
            bounds = NORMALIZATION_BOUNDS.get(bounds_var)
            if not bounds:
                scores[v] = None
                continue
            scores[v] = _normalize(value, bounds["low"], bounds["high"], v in INVERT_MAP)
        return scores

    def _agreement(self, target_date: str) -> dict:
        """A per node = 1 - std(normalized scores of present indicators).

        High std → indicators disagree → high uncertainty (A low).
        Nodes with < 2 present indicators → neutral 0.5.
        """
        scores = self._normalized_scores(target_date)
        a_node = {}
        for node, variables in NODE_VARIABLES.items():
            present = [scores[v] for v in variables if scores.get(v) is not None]
            if len(present) < 2:
                a_node[node] = 0.5
                continue
            mean = sum(present) / len(present)
            var = sum((x - mean) ** 2 for x in present) / len(present)
            std = math.sqrt(var)
            # std ∈ [0, 0.5] for [0,1] values → normalize to trust.
            a_node[node] = max(0.0, min(1.0, 1.0 - (std / 0.5)))
        return a_node

    # ── Component P: Persistence ──────────────────────────────────────────
    def _persistence(self, target_date: str) -> dict:
        """P per node from regime_history: low atr_ratio + low vol_score → stable.

        Uses the latest regime row <= target_date. Neutral 0.5 if absent.
        """
        snap = self._load_snapshot()
        row = self._latest_le(snap["regime_series"], target_date)
        if not row:
            return {node: 0.5 for node in NODES}
        atr, vol = row[1], row[2]
        # atr_ratio ~[0,3], vol_score ~[0,1]. High ratio → unstable.
        stability_atr = max(0.0, min(1.0, 1.0 - (atr / 3.0)))
        stability_vol = max(0.0, min(1.0, 1.0 - vol))
        p = (stability_atr + stability_vol) / 2.0
        return {node: p for node in NODES}

    # ── Orchestration ─────────────────────────────────────────────────────
    def compute(self, target_date: str) -> UncertaintyResult:
        """Compute U for a single PIT date."""
        c_node = self._completeness(target_date)
        f_node = self._freshness(target_date)
        r_node = self._reliability(target_date)
        a_node = self._agreement(target_date)
        p_node = self._persistence(target_date)

        per_node = {}
        per_node_components = {}
        for node in NODES:
            comp_per_node = {
                "C": c_node[node],
                "F": f_node[node],
                "R": r_node[node],
                "A": a_node[node],
                "P": p_node[node],
            }
            per_node_components[node] = comp_per_node
            per_node[node] = round(
                1.0
                - (
                    COMPONENT_WEIGHTS["C"] * comp_per_node["C"]
                    + COMPONENT_WEIGHTS["F"] * comp_per_node["F"]
                    + COMPONENT_WEIGHTS["R"] * comp_per_node["R"]
                    + COMPONENT_WEIGHTS["A"] * comp_per_node["A"]
                    + COMPONENT_WEIGHTS["P"] * comp_per_node["P"]
                ),
                4,
            )

        # Composite: average component trust across nodes, then invert.
        mean_c = sum(c_node.values()) / len(NODES)
        mean_f = sum(f_node.values()) / len(NODES)
        mean_r = sum(r_node.values()) / len(NODES)
        mean_a = sum(a_node.values()) / len(NODES)
        mean_p = sum(p_node.values()) / len(NODES)
        u = round(
            1.0
            - (
                COMPONENT_WEIGHTS["C"] * mean_c
                + COMPONENT_WEIGHTS["F"] * mean_f
                + COMPONENT_WEIGHTS["R"] * mean_r
                + COMPONENT_WEIGHTS["A"] * mean_a
                + COMPONENT_WEIGHTS["P"] * mean_p
            ),
            4,
        )
        u = max(0.0, min(1.0, u))

        source_trace = {
            "C": "macro_history + daily_ohlcv (data availability per node)",
            "F": "macro_history.date lag vs target_date (fresh_score=1.0 all rows: ignored)",
            "R": "macro_history_v2.source (yahoo=1.0, unknown=0.4, missing=0.5)",
            "A": "macro_history normalized scores cross-variable std (single source: not cross-source)",
            "P": "regime_history.atr_ratio + vol_score (latest row <= target_date)",
        }

        return UncertaintyResult(
            target_date=target_date,
            db_path=self.db_path,
            u=u,
            band=classify_uncertainty(u),
            components={"C": mean_c, "F": mean_f, "R": mean_r, "A": mean_a, "P": mean_p},
            per_node=per_node,
            per_node_components=per_node_components,
            source_trace=source_trace,
        )


def compute_uncertainty(target_date: str, db_path: str | None = None) -> UncertaintyResult:
    """Convenience: one-shot uncertainty computation."""
    return UncertaintyLayer(db_path).compute(target_date)
