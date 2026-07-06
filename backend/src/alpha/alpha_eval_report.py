# -*- coding: utf-8 -*-
"""
alpha_eval_report.py — Alpha Evaluation Layer (AEL), CLI Entry Point

Produces the regime → forward-return measurement report.
This is a MEASUREMENT tool — not an optimizer, not a trader.

Usage:
    python -X utf8 backend/src/alpha/alpha_eval_report.py \\
        --from 2023-01-01 \\
        --report backend/logs/ael_report.json

Output:
    Console: regime return matrix + invariant check summary
    JSON:    backend/logs/ael_report.json
"""

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ael_report")


class _NumpyEncoder(json.JSONEncoder):
    """Handles numpy scalar types that the default JSON encoder cannot serialize."""
    def default(self, obj):
        import numpy as np
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return None if math.isnan(obj) else float(obj)
        return super().default(obj)


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

from src.alpha.forward_return_tagger import build_tagged_rows  # noqa: E402
from src.alpha.regime_return_matrix import (  # noqa: E402
    REGIME_BIN_ORDER,
    build_matrix,
    compute_regime_bias_score,
)

# ── Console printer ──────────────────────────────────────────────────────────

def _print_report(rows, matrix, bias_score, start_date: str):
    t5_valid  = sum(1 for r in rows if r.fwd_ret_t5  is not None)
    t20_valid = sum(1 for r in rows if r.fwd_ret_t20 is not None)

    logger.info(f"\n{'='*72}")
    logger.info("  ALPHA EVALUATION LAYER (AEL) — Regime Return Matrix")
    logger.info(f"  Period   : {start_date} -> {datetime.now().strftime('%Y-%m-%d')}")
    logger.info(f"  Tagged   : {len(rows)} regime days  "
                f"| t+5 valid: {t5_valid}  | t+20 valid: {t20_valid}")
    logger.info(f"{'='*72}")

    # Regime distribution
    from collections import Counter
    dist = Counter(r.regime_bin for r in rows)
    logger.info(f"\n  Regime Distribution (total {len(rows)} days):")
    for bin_name in REGIME_BIN_ORDER:
        n = dist.get(bin_name, 0)
        bar = "#" * (n // 5)
        logger.info(f"    {bin_name:20s}: {n:4d}  {bar}")

    # Main matrix table
    logger.info(f"\n{'─'*72}")
    logger.info(f"  {'Regime Bin':20s} {'N':>5s} "
                f"{'hit_t5':>8s} {'mean_t5':>8s} {'sharpe_t5':>10s} "
                f"{'hit_t20':>8s} {'mean_t20':>9s}")
    logger.info(f"{'─'*72}")
    for s in matrix:
        hit5  = f"{s.hit_rate_fwd5:.1%}"   if s.hit_rate_fwd5  is not None else "   N/A"
        mean5 = f"{s.mean_fwd5:+.2%}"      if s.mean_fwd5      is not None else "    N/A"
        sh5   = f"{s.sharpe_fwd5:+.4f}"    if s.sharpe_fwd5    is not None else "     N/A"
        hit20 = f"{s.hit_rate_fwd20:.1%}"  if s.hit_rate_fwd20 is not None else "   N/A"
        mean20= f"{s.mean_fwd20:+.2%}"     if s.mean_fwd20     is not None else "    N/A"
        logger.info(f"  {s.regime_bin:20s} {s.n_days:>5d} "
                    f"{hit5:>8s} {mean5:>8s} {sh5:>10s} "
                    f"{hit20:>8s} {mean20:>9s}")

    logger.info(f"\n  Regime Bias Score: {bias_score:+.4f}"
                f"  (-1.0=bearish engine | 0=neutral | +1.0=bullish engine)")

    # Invariant check summary
    all_invs = [iv for s in matrix for iv in s.invariants]
    passed = sum(1 for iv in all_invs if iv.passed)
    logger.info(f"\n{'─'*72}")
    logger.info(f"  INVARIANT CHECKS  ({passed}/{len(all_invs)} passed)")
    logger.info(f"{'─'*72}")
    for s in matrix:
        for iv in s.invariants:
            icon = "[PASS]" if iv.passed else "[FAIL]"
            logger.info(f"  {icon} {iv.name:42s} "
                        f"observed={iv.observed:.4f}  threshold={iv.threshold:.4f}")

    # Interpretation
    logger.info(f"\n{'─'*72}")
    logger.info("  INTERPRETATION")
    logger.info(f"{'─'*72}")
    th = next((s for s in matrix if s.regime_bin == "TRENDING_HIGH"), None)
    cr = next((s for s in matrix if s.regime_bin == "CRISIS"), None)
    if th and cr:
        if (th.mean_fwd5 or 0) > 0 and (cr.mean_fwd5 or 0) < 0:
            logger.info("  >> REGIME ENGINE HAS REAL PREDICTIVE EDGE")
            logger.info("     TRENDING_HIGH mean_fwd5 > 0  AND  CRISIS mean_fwd5 < 0")
        elif (th.mean_fwd5 or 0) > 0:
            logger.info("  >> PARTIAL EDGE: engine identifies upside but not downside")
        elif (cr.mean_fwd5 or 0) < 0:
            logger.info("  >> PARTIAL EDGE: engine identifies downside but not upside")
        else:
            logger.info("  >> NO EDGE DETECTED: scoring weights need recalibration")
    logger.info(f"{'='*72}\n")


# ── JSON export ───────────────────────────────────────────────────────────────

def _build_json(rows, matrix, bias_score, start_date: str) -> dict:
    all_invs = [iv for s in matrix for iv in s.invariants]
    return {
        "run_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_start": start_date,
        "period_end": datetime.now().strftime("%Y-%m-%d"),
        "total_tagged_days": len(rows),
        "fwd_t5_available": sum(1 for r in rows if r.fwd_ret_t5  is not None),
        "fwd_t20_available": sum(1 for r in rows if r.fwd_ret_t20 is not None),
        "regime_bias_score": round(bias_score, 6),
        "matrix": [s.to_dict() for s in matrix],
        "invariant_summary": {
            "total": len(all_invs),
            "passed": sum(1 for iv in all_invs if iv.passed),
            "failed": sum(1 for iv in all_invs if not iv.passed),
            "crisis_hit_rate_ok": bool(
                next((iv.passed for s in matrix for iv in s.invariants
                      if iv.name == "crisis_hit_rate_lt45pct"), False)
            ),
            "trending_hit_rate_ok": bool(
                next((iv.passed for s in matrix for iv in s.invariants
                      if iv.name == "trending_high_hit_rate_gt55pct"), False)
            ),
            "min_sample_ok": bool(
                all(iv.passed for s in matrix for iv in s.invariants
                    if iv.name == "min_sample_size")
            ),
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Alpha Evaluation Layer — Regime Return Matrix"
    )
    parser.add_argument("--from", dest="start_date", default="2023-01-01",
                        help="Start date for regime history (YYYY-MM-DD)")
    parser.add_argument("--report", default=None,
                        help="Path to write JSON report (optional)")
    args = parser.parse_args()

    logger.info("  [AEL] Building forward-return tagged rows...")
    rows = build_tagged_rows(start_date=args.start_date)

    if not rows:
        logger.error("  [AEL] No tagged rows found. Check regime_history and daily_ohlcv.")
        sys.exit(1)

    logger.info(f"  [AEL] Computing return matrix for {len(rows)} rows...")
    matrix = build_matrix(rows)
    bias   = compute_regime_bias_score(matrix)

    _print_report(rows, matrix, bias, args.start_date)

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _build_json(rows, matrix, bias, args.start_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, cls=_NumpyEncoder)
        logger.info(f"  [AEL] Report written -> {path}")


if __name__ == "__main__":
    main()
