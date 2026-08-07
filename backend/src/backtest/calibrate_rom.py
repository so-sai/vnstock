"""
calibrate_rom.py — Calibrate RegimeROM to match real engine state-space dynamics.

Usage: python backend/src/backtest/calibrate_rom.py
"""

import io
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("calibrate_rom")


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

from core.validation.state_space_validator import StateSpaceValidator
from src.database.db_core import get_connection

from backend.src.backtest.feature_lattice_builder import FeatureLatticeBuilder
from backend.src.backtest.hsr_batch_runner import HSRBatchRunner
from backend.src.backtest.hsr_kernel import InMemoryDB


def run_rom(
    dates: list[str],
    lattice: pd.DataFrame,
    smoothing: float = 0.75,
    trending_bias: float = 0.03,
    crisis_bias: float = 0.03,
    trending_thresh: float = 0.53,
    crisis_thresh: float = 0.30,
) -> list[str]:
    """Run ROM with custom params. Returns regime sequence."""

    class _ROM:
        def __init__(self):
            self.prev_regime = None
            self.momentum = 0.45

        def evaluate(self, bs, fs, rs):
            raw = max(0.0, min(1.0, 0.5 * bs + 0.35 * fs + 0.15 * rs))
            if bs > 0.0:
                self.momentum = smoothing * self.momentum + (1 - smoothing) * raw
            inertia = self.momentum
            if self.prev_regime == "TRENDING":
                inertia = min(1.0, self.momentum + trending_bias)
            elif self.prev_regime == "CRISIS":
                inertia = max(0.0, self.momentum - crisis_bias)
            if inertia > trending_thresh:
                reg = "TRENDING"
            elif inertia < crisis_thresh:
                reg = "CRISIS"
            else:
                reg = "RANGING"
            self.prev_regime = reg
            return {"market_status": reg}

    rom = _ROM()
    runner = HSRBatchRunner(lattice)
    cache = runner._index()
    seq = []
    for d in dates:
        df = cache.get(d)
        if df is None or len(df) == 0:
            continue
        b = runner.breadth(df)
        f = runner.flow(df)
        r = runner.recovery(df)
        reg = rom.evaluate(b["health_score_ma20"], f["flow_bias_score"], 1.0 if r.get("status") == "RECOVERY" else 0.0)
        seq.append(reg["market_status"])
    return seq


def score_distribution(seq: list) -> dict:
    from collections import Counter

    c = Counter(seq)
    n = len(seq)
    return {k: {"count": v, "pct": round(v / n * 100, 1)} for k, v in sorted(c.items())}


def try_params(dates, lattice, target_seq, smoothing, tb, cb, tt, ct):
    pred = run_rom(dates, lattice, smoothing, tb, cb, tt, ct)
    min_len = min(len(target_seq), len(pred))
    v = StateSpaceValidator("comparison")
    a = v.analyze(target_seq[:min_len])
    p = v.analyze(pred[:min_len])
    d = v.compare(a, p)
    ed = d.get("entropy", {}).get("delta_pct", 100)
    pd_ = d.get("persist_ratio", {}).get("delta_pct", 100)
    td_ = d.get("num_transitions", {}).get("delta_pct", 100)
    return {
        "params": {"smoothing": smoothing, "tb": tb, "cb": cb, "tt": tt, "ct": ct},
        "entropy_delta": ed,
        "persist_delta": pd_,
        "trans_delta": td_,
        "avg_delta": (ed + pd_ + td_) / 3,
        "target_entropy": a["entropy"],
        "target_persist": a["persist_ratio"],
        "pred_entropy": p["entropy"],
        "pred_persist": p["persist_ratio"],
        "target_trans": a["num_transitions"],
        "pred_trans": p["num_transitions"],
        "target_dist": score_distribution(target_seq[:min_len]),
        "pred_dist": score_distribution(pred[:min_len]),
    }


def main():
    # Use known Q1 2023 data: CRISIS=23, RANGING=27, TRENDING=9 (59 days)
    # Build from 2023-01-03 to 2023-03-31
    start_date, end_date = "2023-01-01", "2023-03-31"

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date>=? AND date<=? AND symbol='VNINDEX' ORDER BY date",
            (start_date, end_date),
        ).fetchall()
    dates = [r[0] for r in rows]
    logger.info(f"Period: {start_date} to {end_date} ({len(dates)} trading days)")

    # Build lattice
    kernel = InMemoryDB(start_date, end_date)
    with kernel.patch_get_connection():
        with get_connection() as conn:
            all_ohlcv = pd.read_sql(
                "SELECT date,symbol,open,high,low,close,volume FROM daily_ohlcv WHERE date>=? AND date<=? ORDER BY symbol,date",
                conn,
                params=(start_date, end_date),
            )
        lattice = FeatureLatticeBuilder().build(all_ohlcv)
        logger.info(f"Lattice: {len(lattice):,} rows, {all_ohlcv['symbol'].nunique()} symbols")
    kernel.close()

    # Target sequence from Q1 2023 real engine
    # CRISIS=23, RANGING=27, TRENDING=9 (from earlier run)
    # Reconstruct a plausible sequence with these counts (we don't have per-day ordering)
    # For calibration, we'll build one from the known distribution
    # Actually, let's just run real engine on these ~59 days
    logger.info("Running real engine (Q1 2023, ~59 days)...")
    kernel2 = InMemoryDB(start_date, end_date)

    # Patch merge_decisions to be quiet
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

    # Also suppress the coordinator's prints by patching them
    from backend.src.backtest import hsr_coordinator as hc

    _orig_logger = hc.logger
    import logging as _logging

    hc.logger = _logging.getLogger("silent")
    hc.logger.setLevel(_logging.ERROR)

    real_seq = []
    with kernel2.patch_get_connection():
        for d in dates:
            hc.reset_memory()
            snap = hc.build_historical_snapshot(target_date=d, lattice=lattice)
            if snap.get("status") == "ENGINE_FAILURE":
                continue
            real_seq.append(snap.get("regime_status", "UNKNOWN"))

    # Restore
    de.merge_decisions = _orig_merge
    hc.logger = _orig_logger
    kernel2.close()

    logger.info(f"Real engine: {len(real_seq)} regimes collected")
    logger.info(f"Distribution: {dict(sorted(score_distribution(real_seq).items()))}")

    # Analyze target
    v = StateSpaceValidator("Real Engine Q1 2023")
    target = v.analyze(real_seq)
    v.print_report(target)

    # Test ROM parameter grid
    # We need: entropy ~1.46, persist ~50%, trans/100d ~49.2
    # Key insight: smoothing must be very low, thresholds very tight
    param_grid = [
        #   smoothing   tb    cb    tt    ct
        (0.75, 0.03, 0.03, 0.53, 0.30),  # current default
        (0.50, 0.03, 0.03, 0.53, 0.30),
        (0.30, 0.03, 0.03, 0.53, 0.30),
        (0.20, 0.03, 0.03, 0.53, 0.30),
        (0.10, 0.03, 0.03, 0.53, 0.30),
        (0.05, 0.03, 0.03, 0.53, 0.30),
        (0.00, 0.03, 0.03, 0.53, 0.30),
        (0.20, 0.01, 0.01, 0.50, 0.33),
        (0.10, 0.01, 0.01, 0.50, 0.33),
        (0.05, 0.01, 0.01, 0.50, 0.33),
        (0.00, 0.01, 0.01, 0.50, 0.33),
        (0.05, 0.00, 0.00, 0.48, 0.35),
        (0.00, 0.00, 0.00, 0.48, 0.35),
        (0.05, 0.00, 0.00, 0.45, 0.38),  # very tight bands
        (0.00, 0.00, 0.00, 0.45, 0.38),
    ]

    results = []
    for params in param_grid:
        r = try_params(dates, lattice, real_seq, *params)
        results.append(r)
        logger.info(
            f"  s={r['params']['smoothing']:.2f} tb={r['params']['tb']:.2f} "
            f"cb={r['params']['cb']:.2f} tt={r['params']['tt']:.2f} ct={r['params']['ct']:.2f}  "
            f"Δ={r['avg_delta']:5.1f}%  "
            f"E={r['pred_entropy']:.4f}/{r['target_entropy']:.4f}  "
            f"P={r['pred_persist']:.2%}/{r['target_persist']:.2%}  "
            f"T={r['pred_trans']}/{r['target_trans']}"
        )

    results.sort(key=lambda x: x["avg_delta"])

    logger.info(f"\n{'=' * 60}")
    logger.info("  TOP 5 PARAMETER SETS")
    logger.info(f"{'=' * 60}")
    for i, r in enumerate(results[:5]):
        p = r["params"]
        logger.info(f"\n  #{i + 1}: Avg Δ = {r['avg_delta']:.1f}%")
        logger.info(f"    smoothing={p['smoothing']}, tb={p['tb']}, cb={p['cb']}, tt={p['tt']}, ct={p['ct']}")
        logger.info(
            f"    TARGET: entropy={r['target_entropy']:.4f} persist={r['target_persist']:.2%} trans={r['target_trans']}"
        )
        logger.info(f"    PRED:   entropy={r['pred_entropy']:.4f} persist={r['pred_persist']:.2%} trans={r['pred_trans']}")
        logger.info(f"    Target dist: {r['target_dist']}")
        logger.info(f"    Pred dist:   {r['pred_dist']}")

    logger.info("\n  Best params to use in RegimeROM:")
    if results:
        best = results[0]["params"]
        logger.info(f"    RegimeROM(smoothing={best['smoothing']})")
        logger.info(f"    + trending_bias={best['tb']}, crisis_bias={best['cb']}")
        logger.info(f"    + trending_threshold={best['tt']}, crisis_threshold={best['ct']}")


if __name__ == "__main__":
    main()
