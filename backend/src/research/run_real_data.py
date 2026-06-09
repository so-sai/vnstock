"""
run_real_data.py — Run HSRBatchRunner with real market data from screener_cache.db.

Builds the Feature Lattice from SQLite, runs the ROM engine with
narrative + ETS + drift assessment for real Vietnamese market data.
"""
import sys, json, logging
from pathlib import Path
import pandas as pd

# ── Hydrate path ────────────────────────────────────────────────
def _hydrate_path():
    current = Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root_path = current; break
        current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection
from backend.src.backtest.feature_lattice_builder import FeatureLatticeBuilder
from backend.src.backtest.hsr_batch_runner import HSRBatchRunner

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("run_real_data")

def get_trading_dates(start, end):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv "
            "WHERE date >= ? AND date <= ? AND symbol = 'VNINDEX' ORDER BY date",
            (start, end)
        ).fetchall()
    return [r[0] for r in rows]

def build_lattice(start, end):
    with get_connection() as conn:
        all_ohlcv = pd.read_sql(
            "SELECT date, symbol, open, high, low, close, volume FROM daily_ohlcv "
            "WHERE date >= ? AND date <= ? ORDER BY symbol, date",
            conn, params=(start, end)
        )
    builder = FeatureLatticeBuilder()
    lattice = builder.build(all_ohlcv)
    return lattice, all_ohlcv["symbol"].nunique()

def run_analysis(start, end):
    dates = get_trading_dates(start, end)
    logger.info(f"Trading days: {len(dates)} ({dates[0]} → {dates[-1]})")
    
    lattice, n_syms = build_lattice(start, end)
    logger.info(f"Lattice: {len(lattice)} rows across {n_syms} symbols")
    
    runner = HSRBatchRunner(lattice)
    snaps = runner.run(dates)
    logger.info(f"Snapshots: {len(snaps)}")
    return snaps

def print_snapshot(s, label=""):
    ds = s["driver_state"]
    ev = s["explain_validation"]
    dr = s["drift_assessment"]
    print(f"\n  [{label}] {s['date']}")
    print(f"    Regime: {s['regime_status']:10s} Trade: {s['trade_state_level']:10s}")
    print(f"    Driver: {ds['dominant']:10s} ent={ds['entropy']:.3f} conf={ds['confidence']:.3f}")
    print(f"    Dist:   {', '.join(f'{k}={v:.2f}' for k,v in sorted(ds['distribution'].items(), key=lambda x:-x[1])[:3])}")
    print(f"    ETS:    {ev['ets_score']:.3f} ({ev['status']})")
    print(f"    Drift:  {dr['drift_score']:.3f} ({dr['drift_status']})")
    if dr["drift_sources"]:
        print(f"    Issues: {', '.join(dr['drift_sources'])}")
    if dr["flow_rotation"]:
        print(f"    Flow:   {dr['flow_rotation']}")
    if dr["narrative_truth_gap"]:
        print(f"    Gap:    {dr['narrative_truth_gap']}")
    print(f"    Narrative (lý_do): {s['narrative_vi']['lý_do'][:100]}")

if __name__ == "__main__":
    # ── January 2026 (20 days, ~300 symbols) ─────────────────────
    print("=" * 60)
    print("  2026-01-05 → 2026-01-30 (Jan 2026, ~300 symbols/day)")
    print("=" * 60)
    snaps = run_analysis("2026-01-05", "2026-01-30")
    
    print(f"\n--- All {len(snaps)} snapshots ---")
    for i, s in enumerate(snaps):
        print_snapshot(s, f"{i+1}/{len(snaps)}")
    
    # Summary stats
    from collections import Counter
    ets_avg = sum(s["explain_validation"]["ets_score"] for s in snaps) / len(snaps)
    drift_avg = sum(s["drift_assessment"]["drift_score"] for s in snaps) / len(snaps)
    aligned = sum(1 for s in snaps if s["explain_validation"]["status"] == "aligned")
    drift_high = sum(1 for s in snaps if s["drift_assessment"]["drift_status"] in ("HIGH", "CRITICAL"))
    regimes = Counter(s["regime_status"] for s in snaps)
    drivers = Counter(s["driver_state"]["dominant"] for s in snaps)
    
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Days:     {len(snaps)}")
    print(f"  Avg ETS:  {ets_avg:.3f}")
    print(f"  Aligned:  {aligned}/{len(snaps)}")
    print(f"  Avg drift:{drift_avg:.3f}")
    print(f"  High drft:{drift_high}")
    print(f"  Regimes:  {dict(regimes)}")
    print(f"  Drivers:  {dict(drivers)}")
    
    # ── Latest data (2026-06-04, 1500+ symbols) ──────────────────
    print(f"\n{'='*60}")
    print(f"  2026-04-17 → 2026-06-04 (Latest, 1500+ symbols/day)")
    print(f"{'='*60}")
    snaps2 = run_analysis("2026-04-17", "2026-06-04")
    
    print(f"\n--- All {len(snaps2)} snapshots ---")
    for i, s in enumerate(snaps2):
        print_snapshot(s, f"{i+1}/{len(snaps2)}")
