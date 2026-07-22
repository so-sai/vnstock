# -*- coding: utf-8 -*-
"""
run_hsr.py — CLI entry point for Historical State Reconstruction + SRV validation.

Usage:
    python -m backend.src.backtest.run_hsr \\
        --from 2023-01-01 --to 2026-06-01 \\
        --report hsr_report.json

Output:
    - Console summary of Suite A/B/C metrics
    - JSON report with full SRVReport
    - Per-snapshot quality markers for data completeness audit
"""

import argparse
import io
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

if sys.platform == "win32" and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


class _NullWriter(io.TextIOBase):
    """Encoding-safe null writer — never fails on Unicode/emoji."""
    def write(self, s): return len(s or '')
    def flush(self): pass
    @property
    def encoding(self): return 'utf-8'

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("run_hsr")


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
from backend.src.backtest.hsr_coordinator import (
    build_historical_snapshot,
    reset_memory,
)
from backend.src.backtest.hsr_kernel import InMemoryDB
from core.validation.backtest_contract import default_contract
from core.validation.state_reconstruction_validator import SRVReport, run_srv

from src.database.db_core import get_connection


def get_trading_dates(start_date: str, end_date: str) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv "
            "WHERE date >= ? AND date <= ? AND symbol = 'VNINDEX' "
            "ORDER BY date",
            (start_date, end_date)
        ).fetchall()
    return [row[0] for row in rows]


def run_hsr_pipeline(
    start_date: str = "2023-01-01",
    end_date: str = "2026-06-01",
    max_days: Optional[int] = None,
    verbose: bool = True,
) -> list:
    dates = get_trading_dates(start_date, end_date)
    if not dates:
        logger.error(f"No trading dates found between {start_date} and {end_date}")
        return []

    if max_days:
        dates = dates[:max_days]

    reset_memory()
    snapshots = []
    total = len(dates)
    errors = 0
    skipped = 0

    kernel = InMemoryDB(start_date, end_date)

    logger.info(f"\n{'='*60}")
    logger.info("  HISTORICAL STATE RECONSTRUCTION")
    logger.info(f"  Period: {start_date} → {end_date} ({total} trading days)")
    logger.info(f"{'='*60}")

    with kernel.patch_get_connection():
        # Build Feature Lattice (Layer 0) — once for the entire date range
        from src.database.db_core import get_connection as _get_conn
        with _get_conn() as conn:
            all_ohlcv = pd.read_sql(
                "SELECT date, symbol, open, high, low, close, volume FROM daily_ohlcv "
                "WHERE date >= ? AND date <= ? ORDER BY symbol, date",
                conn, params=(start_date, end_date)
            )
        lattice_builder = FeatureLatticeBuilder()
        lattice = lattice_builder.build(all_ohlcv)
        n_syms = all_ohlcv["symbol"].nunique()
        logger.info(f"  [Lattice] Built {len(lattice):,} feature rows across {n_syms} symbols")

        for idx, d in enumerate(dates, 1):
            snapshot = build_historical_snapshot(target_date=d, lattice=lattice)
            status = snapshot.get("status", "OK")
            if status == "ENGINE_FAILURE":
                errors += 1
                if verbose:
                    logger.warning(f"  [{idx:4d}/{total}] {d} → ENGINE_FAILURE: {snapshot.get('error','')}")
                continue
            if status == "SKIP":
                skipped += 1
                continue
            snapshots.append(snapshot)
            if verbose and idx % 20 == 0:
                logger.info(f"  [{idx:4d}/{total}] ... {d} ({len(snapshots)} snapshots)")
    kernel.close()

    logger.info(f"\n{'='*60}")
    logger.info("  Reconstruction complete:")
    logger.info(f"    Total days: {total}")
    logger.info(f"    Snapshots:  {len(snapshots)}")
    logger.info(f"    Errors:     {errors}")
    logger.info(f"    Skipped:    {skipped}")
    logger.info(f"{'='*60}")

    return snapshots


def print_summary(report: SRVReport):
    logger.info(f"\n{'='*60}")
    logger.info(f"  SRV REPORT: {report.contract_name}")
    logger.info(f"  Run: {report.run_date}")
    logger.info(f"  Period: {report.date_range}  ({report.total_days_processed} days)")
    logger.info(f"{'='*60}")

    sa = report.suite_a
    logger.info("\n  Suite A — DBE Stability")
    logger.info(f"  {'Flip Rate':30s}: {sa.flip_rate:.2%}")
    logger.info(f"  {'Flip Count':30s}: {sa.flip_count}")
    logger.info(f"  {'Mean Confidence':30s}: {sa.mean_confidence:.4f}")
    logger.info(f"  {'Mean Strength':30s}: {sa.mean_strength:.4f}")

    sb = report.suite_b
    logger.info("\n  Suite B — DPL Persistence")
    logger.info(f"  {'Persistent Days':30s}: {sb.persistent_days}")
    logger.info(f"  {'Flickering Days':30s}: {sb.flickering_days}")
    logger.info(f"  {'Flicker %':30s}: {sb.flicker_pct:.2%}")
    logger.info(f"  {'Mean Stability':30s}: {sb.mean_stability:.4f}")
    logger.info(f"  {'Stability (Persistent)':30s}: {sb.mean_stability_when_persistent:.4f}")
    logger.info(f"  {'Stability (Flickering)':30s}: {sb.mean_stability_when_flickering:.4f}")

    sc = report.suite_c
    logger.info("\n  Suite C — TTL Transition")
    logger.info(f"  {'Hit Rate':30s}: {sc.hit_rate:.2%}")
    logger.info(f"  {'Hits / Events':30s}: {sc.hits} / {sc.total_events}")
    logger.info(f"  {'Misses':30s}: {sc.misses}")
    logger.info(f"  {'False Positive Rate':30s}: {sc.false_positive_rate:.2%}")
    logger.info(f"  {'Mean Delay (days)':30s}: {sc.mean_delay_days:.1f}")
    logger.info(f"  {'Total TTL Triggers':30s}: {sc.total_ttl_triggers}")

    logger.info("\n  Regime Distribution:")
    for rt, count in sorted(report.regime_type_summary.items(),
                            key=lambda x: -x[1]):
        logger.info(f"    {rt:20s}: {count}")

    logger.info("\n  Transition Matches:")
    for m in sc.matches:
        icon = "✓" if m.is_hit else "✗"
        logger.info(f"    {icon} {m.event_name:25s} "
                     f"type={m.event_type:15s} "
                     f"delay={m.delay_days:2d}d "
                     f"ttl={m.ttl_date} ({m.ttl_type})")

    logger.info(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Historical State Reconstruction + SRV Validation"
    )
    parser.add_argument("--from", dest="start_date", default="2023-01-01")
    parser.add_argument("--to", dest="end_date", default="2026-06-01")
    parser.add_argument("--report", default=None,
                        help="Output path for JSON report")
    parser.add_argument("--max-days", type=int, default=None,
                        help="Limit number of days (for testing)")
    parser.add_argument("--quiet", action="store_true",
                        help="Minimal console output")
    args = parser.parse_args()

    if args.quiet:
        sys.stdout = _NullWriter()

    snapshots = run_hsr_pipeline(
        start_date=args.start_date,
        end_date=args.end_date,
        max_days=args.max_days,
        verbose=not args.quiet,
    )

    if args.quiet:
        sys.stdout = sys.__stdout__

    if not snapshots:
        logger.error("No snapshots to validate. Aborting.")
        sys.exit(1)

    contract = default_contract()
    report = run_srv(snapshots=snapshots, contract=contract)

    print_summary(report)

    if args.report:
        path = Path(args.report)
        from core.validation.state_reconstruction_validator import export_srv_report
        export_srv_report(report, str(path))

    # ── Quality audit ────────────────────────────────────────────────
    missing_sources = sum(
        1 for s in snapshots
        if s.get("hsr_quality", {}).get("capital_displacement") == "MISSING_HISTORICAL_SOURCE"
    )
    logger.info(f"\n  Data Quality: {missing_sources}/{len(snapshots)} snapshots "
                 f"with MISSING_HISTORICAL_SOURCE markers")
    logger.info("  HSR complete. SRV diagnostic mode — not a trading backtest.\n")


if __name__ == "__main__":
    main()
