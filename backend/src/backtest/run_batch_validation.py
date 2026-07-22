"""
run_batch_validation.py — Run full batch runner with calibrated ROM and validate.
"""

import io
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("batch_val")


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
from core.validation.state_space_validator import StateSpaceValidator

from src.database.db_core import get_connection


def get_trading_dates(start_date: str, end_date: str) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date >= ? AND date <= ? AND symbol = 'VNINDEX' ORDER BY date",
            (start_date, end_date),
        ).fetchall()
    return [row[0] for row in rows]


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--hazard", action="store_true", help="Use HazardTransitionEngine instead of ROM")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for hazard engine")
    parser.add_argument("--weights", type=str, default=None, help="Path to calibrated hazard weights JSON")
    args = parser.parse_args()

    start_date, end_date = "2023-01-01", "2026-06-01"

    dates = get_trading_dates(start_date, end_date)
    logger.info(f"Trading days: {len(dates)} ({start_date} → {end_date})")

    # Build lattice
    with get_connection() as conn:
        all_ohlcv = pd.read_sql(
            "SELECT date,symbol,open,high,low,close,volume FROM daily_ohlcv WHERE date>=? AND date<=? ORDER BY symbol,date",
            conn,
            params=(start_date, end_date),
        )
    logger.info(f"OHLCV rows: {len(all_ohlcv):,}, symbols: {all_ohlcv['symbol'].nunique()}")

    lattice = FeatureLatticeBuilder().build(all_ohlcv)
    logger.info(f"Lattice: {len(lattice):,} rows")

    # Run batch runner
    if args.hazard:
        from backend.src.engine.hazard_engine import DEFAULT_HAZARD_WEIGHTS

        weights = dict(DEFAULT_HAZARD_WEIGHTS)
        if args.weights:
            import json

            with open(args.weights, encoding="utf-8") as f:
                loaded = json.load(f)
                weights.update(loaded.get("weights", loaded))
        logger.info(f"\nRunning batch runner with HazardTransitionEngine (seed={args.seed})...")
        runner = HSRBatchRunner(lattice)
        snapshots = runner.run_hazard(dates, weights=weights, seed=args.seed)
        logger.info(f"Snapshots: {len(snapshots)}")
        regime_label = "Hazard Engine"
    else:
        logger.info("\nRunning batch runner with calibrated ROM...")
        runner = HSRBatchRunner(lattice)
        snapshots = runner.run(dates)
        regime_label = "ROM (calibrated)"
    logger.info(f"Snapshots: {len(snapshots)}")

    # Regime sequence
    rom_seq = [s["regime_status"] for s in snapshots]
    from collections import Counter

    dist = Counter(rom_seq)
    logger.info(
        f"ROM regime distribution: CRISIS={dist.get('CRISIS', 0)} "
        f"RANGING={dist.get('RANGING', 0)} TRENDING={dist.get('TRENDING', 0)}"
    )

    # State-space analysis
    v = StateSpaceValidator(f"{regime_label} — Full 820d")
    analysis = v.analyze(rom_seq)
    v.print_report(analysis)

    # Save regime sequence for later comparison
    tag = "hazard" if args.hazard else "rom"
    output = {
        "regime_sequence": rom_seq,
        "dates": [s["date"] for s in snapshots],
        "model": regime_label,
        "state_space": {
            "entropy": analysis["entropy"],
            "persist_ratio": analysis["persist_ratio"],
            "num_transitions": analysis["num_transitions"],
            "regime_distribution": analysis["regime_distribution"],
            "dwell_stats": analysis["dwell_stats"],
            "transition_matrix": {
                k: {k2: round(v2, 4) for k2, v2 in vv.items()} for k, vv in analysis["transition_matrix"].items()
            },
        },
    }
    out_path = PROJECT_ROOT / f"{tag}_calibrated_820d.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved regime sequence → {out_path}")

    # Compare with real engine Q1 2023
    logger.info(f"\n{'=' * 55}")
    logger.info(f"  COMPARISON: Real Engine Q1 2023 vs {regime_label} Q1 2023")
    logger.info(f"{'=' * 55}")

    q1_dates = [d for d in dates if d < "2023-04-01"]
    q1_seq = rom_seq[: len(q1_dates)]

    v_real = StateSpaceValidator("Real Engine Q1 2023")
    real_q1 = {
        "total_days": len(q1_dates),
        "entropy": 1.4597,
        "persist_ratio": 0.50,
        "num_transitions": 29,
        "regime_distribution": {
            "CRISIS": {"days": 23, "pct": 39.0},
            "RANGING": {"days": 27, "pct": 45.8},
            "TRENDING": {"days": 9, "pct": 15.3},
        },
    }

    v_pred = StateSpaceValidator(f"{regime_label} Q1 2023")
    pred_q1 = v_pred.analyze(q1_seq)

    diffs = v_real.compare(real_q1, pred_q1)
    v_real.print_comparison(diffs)

    logger.info(f"\n  {regime_label} distribution: {dict(sorted(Counter(q1_seq).items()))}")

    # Full 820d metrics
    logger.info(f"\n{'=' * 55}")
    logger.info(f"  {regime_label} — Full 820d Metrics")
    logger.info(f"{'=' * 55}")
    logger.info(f"  Entropy:        {analysis['entropy']:.4f}")
    logger.info(f"  Persist ratio:  {analysis['persist_ratio']:.2%}")
    logger.info(
        f"  Transitions:    {analysis['num_transitions']} "
        f"({analysis['num_transitions'] / max(1, analysis['total_days']) * 100:.1f}/100d)"
    )
    logger.info(f"  Distribution:   {dist.get('CRISIS', 0)}C / {dist.get('RANGING', 0)}R / {dist.get('TRENDING', 0)}T")

    logger.info("\n  Done.")


if __name__ == "__main__":
    main()
