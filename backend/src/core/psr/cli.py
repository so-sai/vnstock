"""PSR CLI — command-line interface for production stress release operations.

Usage::

    python -m src.core.psr.cli snapshot         # capture & persist
    python -m src.core.psr.cli replay <id>      # replay one snapshot
    python -m src.core.psr.cli replay-all        # replay all
    python -m src.core.psr.cli freeze <version>  # freeze version manifest
    python -m src.core.psr.cli status           # show version + audit stats
    python -m src.core.psr.cli audit            # recent audit entries
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent.parent
        root = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root = current
                break
            current = current.parent
    for p in (root, root / "backend", root / "backend" / "libs", root / "backend" / "libs" / "vnstock"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("psr_cli")


def cmd_snapshot():
    from src.core.psr.snapshot import SystemStateSnapshotter
    s = SystemStateSnapshotter().capture()
    SystemStateSnapshotter().persist(s)
    print(f"SNAPSHOT {s.snapshot_id} | hash={s.snapshot_hash} | {s.timestamp}")
    print(f"  regime={s.regime.get('status')} | dis={s.data_quality.get('dis')} | divi={s.data_quality.get('divi')}")


def cmd_replay(snapshot_id: str):
    from src.core.psr.replay import DeterministicReplayEngine
    r = DeterministicReplayEngine().replay(snapshot_id)
    if r is None:
        print(f"SNAPSHOT NOT FOUND: {snapshot_id}")
        return
    status = "MATCH" if r.match else "MISMATCH"
    print(f"REPLAY {r.snapshot_id} | {status} | {r.replay_duration_ms:.1f}ms")
    for d in r.diffs:
        icon = "✔" if d.match else "✘"
        print(f"  {icon} {d.field}")


def cmd_replay_all():
    from src.core.psr.replay import DeterministicReplayEngine
    results = DeterministicReplayEngine().replay_all()
    match_count = sum(1 for r in results if r.match)
    print(f"REPLAY-ALL: {match_count}/{len(results)} matched")
    for r in results:
        icon = "✔" if r.match else "✘"
        print(f"  {icon} {r.snapshot_id} | {r.replay_duration_ms:.1f}ms")


def cmd_freeze(version: str):
    from src.core.psr.version import VersionFreeze
    v = VersionFreeze().freeze(version)
    print(f"FROZEN {v.version} | commit={v.git_commit} | hash={v.semantic_contract_hash}")


def cmd_status():
    from src.core.psr.audit import DecisionAuditTrail
    from src.core.psr.snapshot import SystemStateSnapshotter
    from src.core.psr.version import VersionFreeze
    v = VersionFreeze().current()
    snapshots = SystemStateSnapshotter().list_snapshots()
    audit_count = DecisionAuditTrail().count()
    print(f"Version:     {v.version}")
    print(f"Commit:      {v.git_commit}")
    print(f"Contract:    {v.semantic_contract_hash}")
    print(f"Snapshots:   {len(snapshots)}")
    print(f"Audit count: {audit_count}")


def cmd_audit(limit: int = 10):
    from src.core.psr.audit import DecisionAuditTrail
    entries = DecisionAuditTrail().replay(limit=limit)
    if not entries:
        print("No audit entries.")
        return
    print(f"Recent {len(entries)} audit entries (newest first):")
    for e in entries:
        print(f"  [{e.timestamp[:19]}] {e.source} | {e.regime} | {e.label_vi} | sev={e.severity}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "replay" and len(sys.argv) >= 3:
        cmd_replay(sys.argv[2])
    elif cmd == "replay-all":
        cmd_replay_all()
    elif cmd == "freeze" and len(sys.argv) >= 3:
        cmd_freeze(sys.argv[2])
    elif cmd == "status":
        cmd_status()
    elif cmd == "audit":
        limit = int(sys.argv[2]) if len(sys.argv) >= 3 else 10
        cmd_audit(limit)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
