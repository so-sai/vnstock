# -*- coding: utf-8 -*-
"""
regime_return_matrix.py — Alpha Evaluation Layer (AEL), Module 2

Builds the regime × forward-return performance matrix from tagged rows.

Output: list[RegimeReturnStats] — one row per regime_bin.

Usage:
    from backend.src.alpha.regime_return_matrix import build_matrix, REGIME_BIN_ORDER
"""

import math
import sys
from dataclasses import dataclass, field
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

from src.alpha.forward_return_tagger import ForwardReturnRow  # noqa: E402

# Canonical display order (worst → best)
REGIME_BIN_ORDER = ["CRISIS", "RANGING", "TRENDING_LOW", "TRENDING_HIGH"]


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class InvariantCheck:
    name: str
    passed: bool
    observed: float
    threshold: float
    direction: str   # "lt" or "gt"
    description: str


@dataclass
class RegimeReturnStats:
    """Performance statistics for one regime bucket across all tagged days."""
    regime_bin: str
    n_days: int

    # t+5 stats
    mean_fwd5: Optional[float]
    std_fwd5: Optional[float]
    hit_rate_fwd5: Optional[float]   # % days with fwd_ret_t5 > 0
    sharpe_fwd5: Optional[float]     # mean / std (not annualized)
    worst_fwd5: Optional[float]
    best_fwd5: Optional[float]

    # t+20 stats
    mean_fwd20: Optional[float]
    std_fwd20: Optional[float]
    hit_rate_fwd20: Optional[float]
    sharpe_fwd20: Optional[float]
    worst_fwd20: Optional[float]
    best_fwd20: Optional[float]

    # t+1 stats (overnight edge check)
    mean_fwd1: Optional[float]
    hit_rate_fwd1: Optional[float]

    # Regime signal properties
    mean_score: float
    mean_breadth: float
    mean_atr_ratio: float

    invariants: list[InvariantCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "regime_bin": self.regime_bin,
            "n_days": self.n_days,
            "t1": {
                "mean_fwd": _r(self.mean_fwd1),
                "hit_rate": _r(self.hit_rate_fwd1),
            },
            "t5": {
                "mean_fwd": _r(self.mean_fwd5),
                "std_fwd": _r(self.std_fwd5),
                "hit_rate": _r(self.hit_rate_fwd5),
                "sharpe": _r(self.sharpe_fwd5),
                "worst": _r(self.worst_fwd5),
                "best": _r(self.best_fwd5),
            },
            "t20": {
                "mean_fwd": _r(self.mean_fwd20),
                "std_fwd": _r(self.std_fwd20),
                "hit_rate": _r(self.hit_rate_fwd20),
                "sharpe": _r(self.sharpe_fwd20),
                "worst": _r(self.worst_fwd20),
                "best": _r(self.best_fwd20),
            },
            "regime_signal": {
                "mean_score": _r(self.mean_score),
                "mean_breadth": _r(self.mean_breadth),
                "mean_atr_ratio": _r(self.mean_atr_ratio),
            },
            "invariants": [
                {
                    "name": iv.name,
                    "passed": bool(iv.passed),
                    "observed": _r(iv.observed),
                    "threshold": _r(iv.threshold),
                    "direction": iv.direction,
                    "description": iv.description,
                }
                for iv in self.invariants
            ],
        }
        return d


def _r(v) -> Optional[float]:
    """Round to 4 decimal places, propagate None / NaN."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return round(float(v), 4)


def _stats(series: pd.Series) -> dict:
    """Compute mean/std/hit_rate/sharpe/worst/best from a float series."""
    s = series.dropna()
    if s.empty:
        return dict(mean=None, std=None, hit_rate=None, sharpe=None, worst=None, best=None)
    mean = s.mean()
    std  = s.std()
    sharpe = (mean / std) if (std and std > 0) else None
    return dict(
        mean=mean,
        std=std,
        hit_rate=(s > 0).mean(),
        sharpe=sharpe,
        worst=s.min(),
        best=s.max(),
    )


# ── Core function ─────────────────────────────────────────────────────────────

def build_matrix(rows: list[ForwardReturnRow]) -> list[RegimeReturnStats]:
    """
    Computes per-regime-bin forward-return statistics.

    Args:
        rows: Output of forward_return_tagger.build_tagged_rows()

    Returns:
        List of RegimeReturnStats ordered by REGIME_BIN_ORDER.
        Includes invariant checks per bin.
    """
    if not rows:
        return []

    df = pd.DataFrame([
        {
            "regime_bin": r.regime_bin,
            "regime_score": r.regime_score,
            "breadth_pct": r.breadth_pct,
            "atr_ratio": r.atr_ratio,
            "fwd1": r.fwd_ret_t1,
            "fwd5": r.fwd_ret_t5,
            "fwd20": r.fwd_ret_t20,
        }
        for r in rows
    ])

    results: list[RegimeReturnStats] = []

    for bin_name in REGIME_BIN_ORDER:
        sub = df[df["regime_bin"] == bin_name]
        n = len(sub)
        if n == 0:
            continue

        s1  = _stats(sub["fwd1"])
        s5  = _stats(sub["fwd5"])
        s20 = _stats(sub["fwd20"])

        # ── Invariant checks ─────────────────────────────────────────────────
        invs: list[InvariantCheck] = []

        # Minimum sample size
        invs.append(InvariantCheck(
            name="min_sample_size",
            passed=(n >= 20),
            observed=float(n),
            threshold=20.0,
            direction="gt",
            description=f"{bin_name}: need ≥20 days to be statistically meaningful",
        ))

        # CRISIS should have low hit rate (regime correctly identifies downside)
        if bin_name == "CRISIS" and s5["hit_rate"] is not None:
            invs.append(InvariantCheck(
                name="crisis_hit_rate_lt45pct",
                passed=(s5["hit_rate"] < 0.45),
                observed=s5["hit_rate"],
                threshold=0.45,
                direction="lt",
                description="CRISIS fwd5 hit_rate should be <45% — engine detects downside correctly",
            ))

        # TRENDING_HIGH should have high hit rate (regime correctly identifies upside)
        if bin_name == "TRENDING_HIGH" and s5["hit_rate"] is not None:
            invs.append(InvariantCheck(
                name="trending_high_hit_rate_gt55pct",
                passed=(s5["hit_rate"] > 0.55),
                observed=s5["hit_rate"],
                threshold=0.55,
                direction="gt",
                description="TRENDING_HIGH fwd5 hit_rate should be >55% — engine detects upside correctly",
            ))

        # TRENDING_HIGH mean fwd5 should be positive
        if bin_name == "TRENDING_HIGH" and s5["mean"] is not None:
            invs.append(InvariantCheck(
                name="trending_high_positive_edge",
                passed=(s5["mean"] > 0),
                observed=s5["mean"],
                threshold=0.0,
                direction="gt",
                description="TRENDING_HIGH mean fwd5 return should be positive",
            ))

        # CRISIS mean fwd5 should be negative (or near zero)
        if bin_name == "CRISIS" and s5["mean"] is not None:
            invs.append(InvariantCheck(
                name="crisis_negative_edge",
                passed=(s5["mean"] < 0.005),  # allow near-zero
                observed=s5["mean"],
                threshold=0.005,
                direction="lt",
                description="CRISIS mean fwd5 return should be negative/flat (≤+0.5%)",
            ))

        results.append(RegimeReturnStats(
            regime_bin=bin_name,
            n_days=n,
            mean_fwd5=s5["mean"],
            std_fwd5=s5["std"],
            hit_rate_fwd5=s5["hit_rate"],
            sharpe_fwd5=s5["sharpe"],
            worst_fwd5=s5["worst"],
            best_fwd5=s5["best"],
            mean_fwd20=s20["mean"],
            std_fwd20=s20["std"],
            hit_rate_fwd20=s20["hit_rate"],
            sharpe_fwd20=s20["sharpe"],
            worst_fwd20=s20["worst"],
            best_fwd20=s20["best"],
            mean_fwd1=s1["mean"],
            hit_rate_fwd1=s1["hit_rate"],
            mean_score=sub["regime_score"].mean(),
            mean_breadth=sub["breadth_pct"].mean(),
            mean_atr_ratio=sub["atr_ratio"].mean(),
            invariants=invs,
        ))

    return results


def compute_regime_bias_score(matrix: list[RegimeReturnStats]) -> float:
    """
    Aggregate signal edge score ∈ (-1.0, 1.0).

    Logic:
      For each bin, compute (mean_fwd5 * n_days) weighted contribution.
      Normalize by total tagged days.
      Then scale to [-1, 1] assuming ±3% is the max expected monthly swing.
    """
    total_weight = sum(s.n_days for s in matrix if s.mean_fwd5 is not None)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(
        (s.mean_fwd5 or 0.0) * s.n_days
        for s in matrix
        if s.mean_fwd5 is not None
    )
    raw = weighted_sum / total_weight
    # Scale: 3% swing → score of 1.0
    return max(-1.0, min(1.0, raw / 0.03))


if __name__ == "__main__":
    from src.alpha.forward_return_tagger import build_tagged_rows
    rows = build_tagged_rows(start_date="2023-01-01")
    matrix = build_matrix(rows)

    print(f"\n{'='*70}")
    print(f"  REGIME RETURN MATRIX ({len(rows)} tagged days)")
    print(f"{'='*70}")
    print(f"{'Regime Bin':20s} {'N':>5s} {'hit5':>7s} {'mean5':>8s} {'sharpe5':>9s} {'hit20':>7s} {'mean20':>8s}")
    print("-" * 70)
    for s in matrix:
        hit5  = f"{s.hit_rate_fwd5:.1%}"  if s.hit_rate_fwd5  is not None else "  N/A  "
        mean5 = f"{s.mean_fwd5:+.2%}"    if s.mean_fwd5      is not None else "   N/A  "
        sh5   = f"{s.sharpe_fwd5:+.3f}"  if s.sharpe_fwd5    is not None else "    N/A  "
        hit20 = f"{s.hit_rate_fwd20:.1%}" if s.hit_rate_fwd20 is not None else "  N/A  "
        mean20= f"{s.mean_fwd20:+.2%}"   if s.mean_fwd20     is not None else "   N/A  "
        print(f"{s.regime_bin:20s} {s.n_days:>5d} {hit5:>7s} {mean5:>8s} {sh5:>9s} {hit20:>7s} {mean20:>8s}")

    bias = compute_regime_bias_score(matrix)
    print(f"\n  Regime Bias Score: {bias:+.4f}  (−1=bearish_engine, 0=neutral, +1=bullish_engine)")

    print(f"\n{'='*70}")
    print("  INVARIANT CHECKS")
    print("-" * 70)
    for s in matrix:
        for iv in s.invariants:
            icon = "PASS" if iv.passed else "FAIL"
            print(f"  [{icon}] {iv.name:40s} observed={iv.observed:.4f}  threshold={iv.threshold:.4f}")
