"""cross_validate.py — Multi-source financial statement cross-validation.

Decorator `@cross_validate` wraps ProviderManager domain methods so that
each statement fetch also pulls a secondary source (KBS) and compares
overlapping core metrics. Results are recorded on the manager's audit
registry (`cross_validation_report`) without changing the DataFrame
returned by the wrapped method — consumers keep working unchanged.

Flow (mirrors the audit design):
  1. fetch primary source (VCI) via the normal fallback chain
  2. fetch secondary source (KBS) for the same symbol/statement
  3. normalize both to canonical keys (item_mapping) and pivot to
     (metric, period) long form
  4. compare relative error on overlapping periods for shared metrics
  5. if any core metric exceeds `threshold` → FLAG_DISCREPANCY and the
     record is quarantined to `financial_facts_staging`-style reporting

Secondary sources that return empty (e.g. KBS balance_sheet) are
reported as `NO_SECONDARY` rather than flagged — they are simply not
cross-validatable, and the primary data still stands.
"""

import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional

import pandas as pd

from src.providers.item_mapping import canonical_key

logger = logging.getLogger(__name__)

FLAG_VERIFIED = "VERIFIED"
FLAG_DISCREPANCY = "FLAG_DISCREPANCY"
FLAG_NO_SECONDARY = "NO_SECONDARY"

# Core metrics that matter most for audit (aligned with financial_facts).
CORE_METRICS: Dict[str, tuple] = {
    "income_statement": ("NET_REVENUE", "COGS", "NET_PROFIT", "OPERATING_PROFIT"),
    "balance_sheet": ("TOTAL_ASSETS", "TOTAL_LIABILITIES", "EQUITY"),
    "cashflow": ("CFO", "CFI", "CFF", "NET_CHANGE_IN_CASH"),
}

# Default discrepancy tolerance (5% as per audit plan).
DEFAULT_THRESHOLD = 0.05


def _period_columns(df: pd.DataFrame) -> list:
    """Columns that look like period labels (e.g. '2018-Q1', '2025-Q4')."""
    return [c for c in df.columns if c not in ("item", "item_en", "item_id", "unit", "levels", "row_number")]


def _normalize(df: pd.DataFrame, source: str, method: str) -> pd.DataFrame:
    """Pivot a wide statement to long (canonical_metric, period, value).

    Rows missing an item_id are dropped; periods are kept as-is and
    compared only where the two sources overlap.
    """
    if df is None or df.empty or "item_id" not in df.columns:
        return pd.DataFrame(columns=["metric", "period", "value"])

    periods = _period_columns(df)
    long_rows = []
    for _, row in df.iterrows():
        metric = canonical_key(source, str(row["item_id"]))
        for period in periods:
            val = row.get(period)
            if val is None or pd.isna(val):
                continue
            try:
                long_rows.append((metric, period, float(val)))
            except (TypeError, ValueError):
                continue
    out = pd.DataFrame(long_rows, columns=["metric", "period", "value"])
    return out if not out.empty else pd.DataFrame(columns=["metric", "period", "value"])


def _relative_error(a: float, b: float) -> float:
    """Relative error normalized against the larger magnitude (avoid div-by-0)."""
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 0.0 if a == b else 1.0
    return abs(a - b) / denom


def compare_statements(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    source: str,
    secondary_source: str,
    method: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    """Compare two statements; return {status, max_error, metrics, ...}."""
    p = _normalize(primary, source, method)
    s = _normalize(secondary, secondary_source, method)
    if s.empty:
        return {"status": FLAG_NO_SECONDARY, "max_error": 0.0, "metrics": {}}

    pv = p.pivot_table(index="period", columns="metric", values="value", aggfunc="first")
    sv = s.pivot_table(index="period", columns="metric", values="value", aggfunc="first")

    core = CORE_METRICS.get(method, ())
    metric_errors: Dict[str, float] = {}
    compared = 0
    for metric in core:
        if metric not in pv.columns or metric not in sv.columns:
            continue
        common_periods = sorted(set(pv.index) & set(sv.index))
        if not common_periods:
            continue
        errors = []
        for period in common_periods:
            a, b = pv.loc[period, metric], sv.loc[period, metric]
            if pd.isna(a) or pd.isna(b):
                continue
            errors.append(_relative_error(float(a), float(b)))
        if errors:
            metric_errors[metric] = max(errors)
            compared += 1

    if not metric_errors:
        # No overlapping core metric → nothing we can audit, don't flag.
        return {"status": FLAG_NO_SECONDARY, "max_error": 0.0, "metrics": {}}

    max_error = max(metric_errors.values())
    status = FLAG_DISCREPANCY if max_error > threshold else FLAG_VERIFIED
    return {
        "status": status,
        "max_error": round(max_error, 6),
        "metrics": {k: round(v, 6) for k, v in metric_errors.items()},
        "compared_metrics": compared,
    }


def cross_validate(
    method_name: Optional[str] = None,
    secondary_source: str = "KBS",
    threshold: float = DEFAULT_THRESHOLD,
) -> Callable:
    """Decorator: after a provider statement fetch, cross-check vs secondary source.

    Records into the manager's `_cross_validation` registry under
    (symbol, method_name). Returns the primary DataFrame unchanged.

    Usage:
        @cross_validate(secondary_source="KBS")
        def income_statement(self, symbol, **kwargs): ...
    """
    def _decorate(fn: Callable) -> Callable:
        name = method_name or fn.__name__

        @wraps(fn)
        def wrapper(self, symbol: str, **kwargs: Any):
            result = fn(self, symbol, **kwargs)
            report = None
            try:
                secondary = self._secondary_provider(source=secondary_source)
                sec_fn = getattr(secondary, name)
                sec_result = sec_fn(symbol, **kwargs)
                primary_source = self._last_primary_source or "VCI"
                report = compare_statements(
                    result,
                    sec_result,
                    source=primary_source,
                    secondary_source=secondary_source,
                    method=name,
                    threshold=threshold,
                )
            except Exception as e:  # noqa: BLE001 - secondary failures never break primary
                logger.debug("Cross-validation failed for %s/%s: %s", symbol, name, e)
                report = {"status": FLAG_NO_SECONDARY, "max_error": 0.0, "metrics": {}}

            with self._lock:
                self._cross_validation[(symbol, name)] = report
                if report["status"] == FLAG_DISCREPANCY:
                    self._discrepancies[(symbol, name)] = report
            return result

        return wrapper

    # Support both @cross_validate and @cross_validate(...)
    if callable(method_name) and secondary_source == "KBS" and threshold == DEFAULT_THRESHOLD:
        fn = method_name
        method_name = None
        return _decorate(fn)
    return _decorate
