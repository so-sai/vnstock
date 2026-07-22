"""
calibrate_hazard.py — Calibrate HazardTransitionEngine weights to match
real engine state-space dynamics.

Usage:
    python backend/src/backtest/calibrate_hazard.py

Strategy:
    Search over hazard weight combinations using random perturbations
    of DEFAULT_HAZARD_WEIGHTS. Evaluate each by comparing state-space
    metrics (entropy, persist ratio, transition rate) against the real
    engine regime sequence.

    Because the hazard engine is stochastic, each candidate is evaluated
    across MULTIPLE seeds and the metrics are AVERAGED.
"""

import io
import json
import logging
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("calibrate_hazard")


class _NullWriter(io.TextIOBase):
    def write(self, s):
        return len(s or "")

    def flush(self):
        pass

    @property
    def encoding(self):
        return "utf-8"


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from backend.src.backtest.feature_lattice_builder import FeatureLatticeBuilder
from backend.src.backtest.hsr_batch_runner import HSRBatchRunner
from backend.src.backtest.hsr_kernel import InMemoryDB
from backend.src.engine.hazard_engine import (
    DEFAULT_HAZARD_WEIGHTS,
    HazardTransitionEngine,
)
from core.validation.state_space_validator import StateSpaceValidator

from src.database.db_core import get_connection

# ── Number of random seeds to average per candidate ────────────────
N_SEEDS_PER_CANDIDATE = 5
N_CANDIDATES = 100


def get_trading_dates(start_date: str, end_date: str) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date>=? AND date<=? AND symbol='VNINDEX' ORDER BY date",
            (start_date, end_date),
        ).fetchall()
    return [r[0] for r in rows]


def run_real_engine(dates: list, lattice: pd.DataFrame) -> list[str]:
    """Run the full real engine and return regime sequence."""
    import src.engine.decision_engine as de

    _orig_merge = de.merge_decisions

    def _quiet_merge(model_a_verdict=None, target_date=None):
        old = sys.stdout
        sys.stdout = _NullWriter()
        try:
            result = _orig_merge(model_a_verdict, target_date)
        finally:
            sys.stdout = old
        return result

    de.merge_decisions = _quiet_merge

    from backend.src.backtest import hsr_coordinator as hc

    _orig_logger = hc.logger
    import logging as _logging

    hc.logger = _logging.getLogger("silent")
    hc.logger.setLevel(_logging.ERROR)

    real_seq = []
    with InMemoryDB(dates[0], dates[-1]).patch_get_connection():
        for d in dates:
            hc.reset_memory()
            snap = hc.build_historical_snapshot(target_date=d, lattice=lattice)
            if snap.get("status") == "ENGINE_FAILURE":
                continue
            real_seq.append(snap.get("regime_status", "UNKNOWN"))

    de.merge_decisions = _orig_merge
    hc.logger = _orig_logger
    return real_seq


def score_distribution(seq: list) -> dict:
    from collections import Counter

    c = Counter(seq)
    n = len(seq)
    return {k: {"count": v, "pct": round(v / n * 100, 1)} for k, v in sorted(c.items())}


def run_hazard_with_weights(
    dates: list[str],
    cache: dict,
    weights: dict,
    seed: int,
) -> list[str]:
    """Run hazard engine with given weights and return regime sequence."""
    engine = HazardTransitionEngine(weights=weights, seed=seed)
    seq = []
    for d in dates:
        df = cache.get(d)
        if df is None or len(df) == 0:
            continue
        result = engine.evaluate(df)
        seq.append(result["market_status"])
    return seq


def evaluate_weights(
    dates: list[str],
    cache: dict,
    weights: dict,
    target_analysis: dict,
) -> dict:
    """
    Evaluate a weight set across multiple seeds.
    Returns average delta metrics and stability (std across seeds).
    """
    validator = StateSpaceValidator("hazard_eval")
    seed_metrics = []

    for seed in range(N_SEEDS_PER_CANDIDATE):
        seq = run_hazard_with_weights(dates, cache, weights, seed)
        analysis = validator.analyze(seq)
        comp = validator.compare(target_analysis, analysis)
        seed_metrics.append(
            {
                "entropy": analysis["entropy"],
                "persist_ratio": analysis["persist_ratio"],
                "num_transitions": analysis["num_transitions"],
                "entropy_delta": comp["entropy"]["delta_pct"],
                "persist_delta": comp["persist_ratio"]["delta_pct"],
                "trans_delta": comp["num_transitions"]["delta_pct"],
            }
        )

    avg = {
        "entropy": np.mean([m["entropy"] for m in seed_metrics]),
        "persist_ratio": np.mean([m["persist_ratio"] for m in seed_metrics]),
        "num_transitions": np.mean([m["num_transitions"] for m in seed_metrics]),
        "entropy_delta": np.mean([m["entropy_delta"] for m in seed_metrics]),
        "persist_delta": np.mean([m["persist_delta"] for m in seed_metrics]),
        "trans_delta": np.mean([m["trans_delta"] for m in seed_metrics]),
    }
    avg["avg_delta"] = (avg["entropy_delta"] + avg["persist_delta"] + avg["trans_delta"]) / 3

    std = {
        "entropy": np.std([m["entropy"] for m in seed_metrics]),
        "persist_ratio": np.std([m["persist_ratio"] for m in seed_metrics]),
        "num_transitions": np.std([m["num_transitions"] for m in seed_metrics]),
    }

    return {"avg": avg, "std": std, "seed_metrics": seed_metrics}


def generate_candidate_weights(base: dict, n: int) -> list[dict]:
    """
    Generate candidate weight sets by perturbing DEFAULT weights.
    Uses Latin Hypercube-like stratified sampling over a perturbation range.
    """
    # Define which weights to vary and their perturbation ranges
    # (multiplier range: [1 - range, 1 + range])
    perturb_spec = {
        "intercept": 0.4,  # ±40%
        "volatility_norm": 0.5,
        "volume_shock_norm": 0.5,
        "trend_strength_norm": 0.5,
        "compression_norm": 0.5,
        "return_5d_norm": 0.5,
        "breadth": 0.5,
        "entropy_norm": 0.5,
        "age_norm": 0.5,
    }

    candidates = []
    for _ in range(n):
        weights = dict(base)
        for k, prange in perturb_spec.items():
            factor = 1.0 + random.uniform(-prange, prange)
            weights[k] = base[k] * factor
        candidates.append(weights)
    return candidates


def main():
    start_date, end_date = "2023-01-01", "2023-03-31"

    dates = get_trading_dates(start_date, end_date)
    logger.info(f"Period: {start_date} to {end_date} ({len(dates)} trading days)")

    # Build lattice
    kernel = InMemoryDB(start_date, end_date)
    with kernel.patch_get_connection():
        with get_connection() as conn:
            all_ohlcv = pd.read_sql(
                "SELECT date,symbol,open,high,low,close,volume FROM daily_ohlcv "
                "WHERE date>=? AND date<=? ORDER BY symbol,date",
                conn,
                params=(start_date, end_date),
            )
        lattice = FeatureLatticeBuilder().build(all_ohlcv)
        logger.info(f"Lattice: {len(lattice):,} rows, {all_ohlcv['symbol'].nunique()} symbols")
    kernel.close()

    # Build cache for fast iteration
    runner = HSRBatchRunner(lattice)
    cache = runner._index()

    # Run real engine
    logger.info("Running real engine (Q1 2023)...")
    real_seq = run_real_engine(dates, lattice)
    logger.info(f"Real engine: {len(real_seq)} regimes collected")
    logger.info(f"Distribution: {score_distribution(real_seq)}")

    validator = StateSpaceValidator("Real Engine Q1 2023")
    target = validator.analyze(real_seq)
    validator.print_report(target)

    # Generate and test candidates
    logger.info(f"\n{'=' * 55}")
    logger.info(f"  SEARCH: {N_CANDIDATES} candidates x {N_SEEDS_PER_CANDIDATE} seeds")
    logger.info(f"{'=' * 55}")

    candidates = generate_candidate_weights(DEFAULT_HAZARD_WEIGHTS, N_CANDIDATES)

    # Always include the default weights as baseline
    candidates.insert(0, dict(DEFAULT_HAZARD_WEIGHTS))

    results = []
    for i, w in enumerate(candidates):
        eval_result = evaluate_weights(dates, cache, w, target)
        avg = eval_result["avg"]
        results.append(
            {
                "weights": w,
                "avg_delta": avg["avg_delta"],
                "entropy": avg["entropy"],
                "persist_ratio": avg["persist_ratio"],
                "num_transitions": avg["num_transitions"],
                "entropy_delta": avg["entropy_delta"],
                "persist_delta": avg["persist_delta"],
                "trans_delta": avg["trans_delta"],
                "stability": eval_result["std"],
            }
        )

        if (i + 1) % 10 == 0:
            logger.info(f"  [{i + 1:4d}/{len(candidates)}] best so far: Δ={min(r['avg_delta'] for r in results):.1f}%")

    results.sort(key=lambda x: x["avg_delta"])

    # Report
    logger.info(f"\n{'=' * 60}")
    logger.info("  TOP 5 HAZARD WEIGHT SETS")
    logger.info(f"{'=' * 60}")

    for rank, r in enumerate(results[:5], 1):
        w = r["weights"]
        logger.info(
            f"\n  #{rank}: Avg Δ = {r['avg_delta']:.1f}%  "
            f"(E={r['entropy_delta']:.1f}%  P={r['persist_delta']:.1f}%  "
            f"T={r['trans_delta']:.1f}%)"
        )
        logger.info("    Weights:")
        for k, v in sorted(w.items()):
            logger.info(f"      {k:25s} = {v:.4f}")
        logger.info(
            f"    Metrics:  entropy={r['entropy']:.4f}  persist={r['persist_ratio']:.2%}  trans={r['num_transitions']:.1f}"
        )
        logger.info(
            f"    Target:   entropy={target['entropy']:.4f}  "
            f"persist={target['persist_ratio']:.2%}  "
            f"trans={target['num_transitions']}"
        )

    # Save best weights
    if results:
        best = results[0]
        out_path = PROJECT_ROOT / "calibrated_hazard_weights.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "weights": {k: round(v, 4) for k, v in best["weights"].items()},
                    "metrics": {
                        "entropy": round(best["entropy"], 4),
                        "persist_ratio": round(best["persist_ratio"], 4),
                        "num_transitions": round(best["num_transitions"], 1),
                        "avg_delta_pct": round(best["avg_delta"], 1),
                    },
                    "target_metrics": {
                        "entropy": target["entropy"],
                        "persist_ratio": target["persist_ratio"],
                        "num_transitions": target["num_transitions"],
                    },
                    "calibration_period": f"{start_date} → {end_date}",
                    "n_seeds_per_candidate": N_SEEDS_PER_CANDIDATE,
                },
                f,
                indent=2,
            )
        logger.info(f"\n  Saved best weights → {out_path}")

        logger.info("\n  To use calibrated weights in HazardBatchRunner:")
        logger.info("    runner = HazardBatchRunner(lattice_df, seed=42)")
        logger.info(f"    runner.set_weights({best['weights']})")
        logger.info("    snapshots = runner.run(dates)")
        logger.info("\n  Or pass weights to HazardTransitionEngine:")
        logger.info(f"    engine = HazardTransitionEngine(weights={best['weights']})")

    logger.info("\n  Done.")


if __name__ == "__main__":
    main()
