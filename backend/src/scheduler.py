"""
Scheduler — Fixed Market-Aligned CronTrigger (3 slots)

Replaces infinite catch-up crawling with 3 fixed slots per trading day:
  1. Pre-market  08:30 — macro/world indicators from overnight
  2. Mid-day     11:45 — VNIndex session update
  3. Post-market 15:30 — BCTC/closing price ingestion

Uses APScheduler CronTrigger with:
  - misfire_grace_time=300s (skip if >5 min late, no catch-up flood)
  - coalesce=True (merge delayed runs into single execution)
  - max_instances=1 (prevent concurrent runs)

LAW-009 alignment: all macro data fetched at fixed times ensures
temporal consistency — no drift between M vector and sector scores.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("[SCHEDULER] apscheduler not installed. Install: pip install apscheduler")


def _get_data_pipeline():
    """Lazy-import the daily data pipeline."""
    from engine.eod_runner import run_daily_pipeline

    return run_daily_pipeline


def _get_macro_refresh():
    """Lazy-import macro indicator refresh."""
    from governor.regional_influence_engine import compute_macro_vector

    return compute_macro_vector


def create_market_scheduler() -> BackgroundScheduler | None:
    """Create and configure the market-aligned scheduler.

    Returns:
        BackgroundScheduler if APScheduler is available, None otherwise.
    """
    if not APSCHEDULER_AVAILABLE:
        return None

    scheduler = BackgroundScheduler()

    # ── Slot 1: Pre-market (08:30) ──────────────────────────
    # WHY: Fetch overnight macro indicators before VN-Index opens.
    #      Macro state must be stable before trading begins.
    scheduler.add_job(
        func=_get_macro_refresh,
        trigger=CronTrigger(hour=8, minute=30, day_of_week="mon-fri"),
        id="macro_pre_market",
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # ── Slot 2: Mid-day (11:45) ─────────────────────────────
    # WHY: Update VNIndex intraday state for afternoon session.
    #      Captures any overnight gap or morning momentum shift.
    scheduler.add_job(
        func=_get_macro_refresh,
        trigger=CronTrigger(hour=11, minute=45, day_of_week="mon-fri"),
        id="macro_mid_day",
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # ── Slot 3: Post-market (15:30) ─────────────────────────
    # WHY: Ingest closing prices + BCTC after VN-Index closes.
    #      All end-of-day data captured before next session.
    scheduler.add_job(
        func=_get_data_pipeline,
        trigger=CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
        id="eod_data_pipeline",
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    logger.info("[SCHEDULER] Market-aligned CronTrigger configured: 08:30 (macro), 11:45 (mid-day), 15:30 (EOD)")
    return scheduler


def start_scheduler():
    """Start the market scheduler in background."""
    scheduler = create_market_scheduler()
    if scheduler is None:
        return
    scheduler.start()
    logger.info("[SCHEDULER] Started. 3 CronTrigger slots active.")


def stop_scheduler():
    """Stop the market scheduler gracefully."""
    if not APSCHEDULER_AVAILABLE:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        for s in BackgroundScheduler.__subclasses__():
            if s._instance:
                s._instance.shutdown(wait=False)
                logger.info("[SCHEDULER] Stopped.")
    except ImportError, AttributeError, TypeError, KeyError:
        logger.debug("Dừng scheduler thất bại (bỏ qua)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PTCK_VNSTOCK Market-Aligned Scheduler")
    parser.add_argument("--start", action="store_true", help="Start the scheduler in background")
    parser.add_argument("--stop", action="store_true", help="Stop the scheduler")
    parser.add_argument("--status", action="store_true", help="Show scheduled jobs")
    args = parser.parse_args()

    if args.start:
        start_scheduler()
        print("Scheduler started. 3 slots: 08:30 | 11:45 | 15:30 (Mon-Fri)")
        try:
            import time

            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            stop_scheduler()
            print("Scheduler stopped.")

    elif args.stop:
        stop_scheduler()
        print("Scheduler stopped.")

    elif args.status:
        if APSCHEDULER_AVAILABLE:
            s = create_market_scheduler()
            if s:
                for job in s.get_jobs():
                    print(f"  {job.id}: {job.trigger}")
                s.shutdown(wait=False)
        else:
            print("apscheduler not installed.")
