"""Shadow CAO — CLI Entry Point

Usage:
    python -m src.shadow_cao                    # Run daily batch
    python -m src.shadow_cao --recheck          # Re-check readiness gates
    python -m src.shadow_cao --replay           # Replay historical snapshots
    python -m src.shadow_cao --replay --limit N # Replay last N snapshots
    python -m src.shadow_cao --stats            # Show shadow storage stats
    python -m src.shadow_cao --bus-stats        # Show async event bus stats
    python -m src.shadow_cao --json             # Machine-readable output
"""
import sys
import json
from pathlib import Path


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

from src.shadow_cao.scheduler import run_daily_batch, run_gate_recheck
from src.shadow_cao.storage import get_shadow_stats
from src.shadow_cao.belief import get_belief_state
from src.shadow_cao.hooks import run_replay
from src.shadow_cao.hardening import get_event_bus


def main():
    do_recheck = "--recheck" in sys.argv
    do_replay = "--replay" in sys.argv
    do_stats = "--stats" in sys.argv
    do_bus_stats = "--bus-stats" in sys.argv
    json_output = "--json" in sys.argv
    quiet = "--quiet" in sys.argv
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[i + 1])
            except ValueError:
                pass
    if do_bus_stats:
        bus = get_event_bus()
        stats = bus.stats
        if json_output:
            print(json.dumps(stats, indent=2))
        else:
            print("Shadow Event Bus Stats:")
            for k, v in stats.items():
                print(f"  {k}: {v}")
        return
    if do_stats:
        stats = get_shadow_stats()
        if json_output:
            print(json.dumps(stats, indent=2))
        else:
            print("Shadow CAO Stats:")
            for k, v in stats.items():
                print(f"  {k}: {v}")
        return
    if do_replay:
        replay_limit = limit or 200
        if not quiet:
            print(f"Replaying last {replay_limit} snapshots...")
        results = run_replay(limit=replay_limit)
        if json_output:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            ok = sum(1 for r in results if r.get("status") == "ok")
            failed = sum(1 for r in results if r.get("status") == "failed")
            violations = sum(r.get("violations", 0) for r in results)
            print(f"Replay complete: {ok} ok, {failed} failed, {violations} timestamp violations")
        return
    if do_recheck:
        result = run_gate_recheck(verbose=not quiet)
        if json_output:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    summary = run_daily_batch(verbose=not quiet)
    if json_output:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
