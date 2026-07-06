# -*- coding: utf-8 -*-
"""
forward_return_tagger.py — Alpha Evaluation Layer (AEL), Module 1

Joins regime_history × daily_ohlcv (VNINDEX) to produce forward-return-tagged rows.

Output: list[ForwardReturnRow]  — stateless, never writes to SQLite.

Usage (import):
    from backend.src.alpha.forward_return_tagger import build_tagged_rows

Horizons:
    t+1  : overnight / next-session signal check
    t+5  : 1-week market momentum
    t+20 : 1-month regime consequence
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────────────
def _hydrate_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root_path = current
            break
        current = current.parent
    for p in [str(root_path), str(root_path / "backend")]:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection  # noqa: E402

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class ForwardReturnRow:
    """One regime-tagged day with forward VNINDEX returns."""
    date: str
    regime_score: float
    regime_score_raw: float        # available if EMA hardening wrote raw score
    status: str                    # TRENDING / RANGING / CRISIS
    regime_bin: str                # 4-class fine-grained bucket
    breadth_pct: float
    atr_ratio: float
    trend_score: float
    vol_score: float
    fwd_ret_t1: Optional[float]    # (close[t+1] - close[t]) / close[t]
    fwd_ret_t5: Optional[float]    # (close[t+5] - close[t]) / close[t]
    fwd_ret_t20: Optional[float]   # (close[t+20] - close[t]) / close[t]


def _classify_bin(score: float) -> str:
    """4-class regime bucket — finer than the 3-class status field."""
    if score >= 0.75:
        return "TRENDING_HIGH"
    elif score >= 0.65:
        return "TRENDING_LOW"
    elif score >= 0.35:
        return "RANGING"
    else:
        return "CRISIS"


# ── Core function ─────────────────────────────────────────────────────────────

def build_tagged_rows(
    start_date: str = "2023-01-01",
    end_date: str = "2026-12-31",
) -> list[ForwardReturnRow]:
    """
    Joins regime_history with VNINDEX daily close prices to produce
    forward-return-tagged rows for each regime observation.

    Args:
        start_date: Inclusive start (YYYY-MM-DD).
        end_date:   Inclusive end  (YYYY-MM-DD).

    Returns:
        List of ForwardReturnRow — only rows where all 3 horizons are available.
        Days near the tail (last 20 sessions) will have NaN fwd_ret_t20 and
        are included with that field set to None.
    """
    with get_connection() as conn:
        # 1. Regime history for the requested period
        regime_df = pd.read_sql(
            "SELECT date, regime_score, status, breadth_pct, "
            "       trend_score, vol_score, atr_ratio "
            "FROM regime_history "
            "WHERE date >= ? AND date <= ? "
            "ORDER BY date",
            conn, params=(start_date, end_date)
        )

        # 2. VNINDEX closes — fetch a buffer of +25 sessions beyond end_date
        #    so we can compute t+20 for dates near the tail
        vnindex_df = pd.read_sql(
            "SELECT date, close FROM daily_ohlcv "
            "WHERE symbol = 'VNINDEX' "
            "ORDER BY date",
            conn
        )

    if regime_df.empty:
        return []

    # ── Prepare VNINDEX close series ─────────────────────────────────────────
    vnindex_df["date"] = pd.to_datetime(vnindex_df["date"])
    vnindex_df = vnindex_df.sort_values("date").reset_index(drop=True)
    vnindex_df["close"] = pd.to_numeric(vnindex_df["close"], errors="coerce")

    vnindex_df = vnindex_df.assign(
        fwd_t1=vnindex_df["close"].shift(-1),
        fwd_t5=vnindex_df["close"].shift(-5),
        fwd_t20=vnindex_df["close"].shift(-20),
    )

    # Build date → returns lookup
    vni_lookup = vnindex_df.set_index("date")[["close", "fwd_t1", "fwd_t5", "fwd_t20"]]

    # ── Join & tag ────────────────────────────────────────────────────────────
    regime_df["date"] = pd.to_datetime(regime_df["date"])
    rows: list[ForwardReturnRow] = []

    for _, row in regime_df.iterrows():
        dt = row["date"]
        score = float(row["regime_score"])

        if dt not in vni_lookup.index:
            continue  # no VNINDEX price for this date — skip

        price_row = vni_lookup.loc[dt]
        close_t = price_row["close"]
        if pd.isna(close_t) or close_t <= 0:
            continue

        def _ret(future_close) -> Optional[float]:
            if pd.isna(future_close) or future_close <= 0:
                return None
            return (future_close - close_t) / close_t

        rows.append(ForwardReturnRow(
            date=dt.strftime("%Y-%m-%d"),
            regime_score=score,
            regime_score_raw=score,  # regime_history stores smoothed; raw not persisted
            status=str(row.get("status", "UNKNOWN")),
            regime_bin=_classify_bin(score),
            breadth_pct=float(row.get("breadth_pct", 0.0)),
            atr_ratio=float(row.get("atr_ratio", 1.0)),
            trend_score=float(row.get("trend_score", 0.0)),
            vol_score=float(row.get("vol_score", 0.0)),
            fwd_ret_t1=_ret(price_row["fwd_t1"]),
            fwd_ret_t5=_ret(price_row["fwd_t5"]),
            fwd_ret_t20=_ret(price_row["fwd_t20"]),
        ))

    return rows


if __name__ == "__main__":
    rows = build_tagged_rows(start_date="2023-01-01")
    t5_available  = sum(1 for r in rows if r.fwd_ret_t5  is not None)
    t20_available = sum(1 for r in rows if r.fwd_ret_t20 is not None)
    print(f"Total regime rows tagged : {len(rows)}")
    print(f"  fwd_ret_t5  available  : {t5_available}")
    print(f"  fwd_ret_t20 available  : {t20_available}")
    from collections import Counter
    dist = Counter(r.regime_bin for r in rows)
    print("\nRegime bin distribution:")
    for k, v in sorted(dist.items()):
        print(f"  {k:20s}: {v:4d} days  ({v/len(rows)*100:.1f}%)")
