"""Shadow CAO — Daily batch scheduler.

Idempotent: safe to run multiple times. Only processes new data.
Runs after telemetry evaluation completes.
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.shadow_cao.belief import update_engine_profiles
from src.shadow_cao.hooks import daily_shadow_tick
from src.shadow_cao.storage import get_shadow_stats, initialize_shadow_database


def run_daily_batch(verbose: bool = True) -> dict:
    """Run the daily Shadow CAO batch pipeline.

    Safe to call multiple times — only processes unprocessed data.
    No weight updates — statistics accumulation only.
    """
    if verbose:
        print("=" * 56)
        print("  SHADOW CAO — Daily Batch")
        print("=" * 56)
    try:
        initialize_shadow_database()
    except Exception as e:
        logger.error("[SHADOW_CAO] DB init failed: %s", e)
        return {"error": str(e)}
    stats_before = get_shadow_stats()
    if verbose:
        print(f"  Before: {stats_before['decision_logs']} logs | "
              f"{stats_before['ablations']} ablations | "
              f"{stats_before['outcomes']} outcomes")
    summary = daily_shadow_tick()
    stats_after = get_shadow_stats()
    summary["stats_before"] = stats_before
    summary["stats_after"] = stats_after
    if verbose:
        print(f"  Processed: {summary['processed']} decisions")
        print(f"  After: {stats_after['decision_logs']} logs | "
              f"{stats_after['ablations']} ablations | "
              f"{stats_after['outcomes']} outcomes")
        print(f"  Stability: {summary['stability']:.4f} "
              f"{'| Entropy: ' + str(summary['entropy']) if summary.get('entropy') is not None else ''}")
        if summary.get("readiness_pass") is not None:
            status = "PASS" if summary["readiness_pass"] else "BLOCKED"
            print(f"  Readiness gate: {status}")
        print("=" * 56)
    return summary


def run_gate_recheck(verbose: bool = True) -> dict:
    """Re-check CAO readiness gates using current shadow data.

    Does NOT process new data — only re-evaluates readiness based on
    accumulated shadow state.
    """
    if verbose:
        print("=" * 56)
        print("  SHADOW CAO — Gate Re-check")
        print("=" * 56)
    from src.cao_readiness import run_readiness_check
    profiles = update_engine_profiles()
    if verbose:
        print(f"  Engine profiles: {len(profiles)}")
        for eng, p in sorted(profiles.items()):
            if p.total_decisions > 0:
                print(f"    {eng}: {p.total_decisions} decisions | "
                      f"flip_ratio={p.flip_count/max(p.total_decisions,1):.3f} | "
                      f"stability={p.stability_score:.3f}")
    verdict = run_readiness_check(verbose=verbose)
    if verbose:
        print()
        status = "PASS" if verdict.overall_pass else "BLOCKED"
        print(f"  CAO Readiness: {status} ({verdict.pass_count}/{len(verdict.gates)} gates pass)")
    return {
        "overall_pass": verdict.overall_pass,
        "gates": [
            {"name": g.gate_name, "status": g.status, "score": g.score, "message": g.message}
            for g in verdict.gates
        ],
        "engine_profiles": {
            eng: {
                "total_decisions": p.total_decisions,
                "flip_count": p.flip_count,
                "avg_confidence_delta": p.avg_confidence_delta,
                "action_change_ratio": p.action_change_ratio,
                "stability_score": p.stability_score,
            }
            for eng, p in profiles.items()
        },
    }
