"""CAGL CLI — command-line route verification.

Usage::

    python -m src.core.cagl verify [--mode SHADOW|WARN|STRICT]

Verifies all registered FastAPI routes against the CAGL contract.
"""
from __future__ import annotations

import io
import sys

if isinstance(sys.stdout, io.TextIOWrapper):
    if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import argparse
import logging
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
    for p in (root, root / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("cagl_cli")


def cmd_verify(mode: str = "WARN", lang: str = "vi"):
    """Scan the FastAPI app and verify all routes."""
    from src.api.main import app
    from src.core.cagl.bootstrap import verify_cagl

    result = verify_cagl(app, mode=mode)

    if lang == "vi":
        from src.core.report_i18n_mapper import format_verify_result
        print()
        print(format_verify_result(
            routes_scanned=len(result.runtime_routes),
            errors=len(result.errors),
            warnings=len(result.warnings),
            is_valid=result.is_valid,
            findings=result.findings,
        ))
    else:
        print()
        print(f"CAGL VERIFY — {mode}")
        print(f"  Routes scanned:  {len(result.runtime_routes)}")
        print(f"  Errors:          {len(result.errors)}")
        print(f"  Warnings:        {len(result.warnings)}")
        print(f"  Valid:           {result.is_valid}")
        print()
        if result.findings:
            for f in result.findings:
                icon = {"error": "x", "warning": "!", "info": "i"}.get(f.severity, ".")
                print(f"  {icon} [{f.category:20s}] {f.path}")
                print(f"    {f.message}")
        else:
            print("  + No findings -- all routes clean.")
    return 0 if result.is_valid else 1


def cmd_snapshot(lang: str = "vi"):
    """Capture a PSR snapshot with API route data included."""
    from src.core.psr.snapshot import SystemStateSnapshotter
    snapper = SystemStateSnapshotter()
    snap = snapper.capture()
    snapper.persist(snap)
    route_count = (snap.api_routes or {}).get("count", 70)
    api_hash = snap.api_contract_hash or "N/A"
    if lang == "vi":
        from src.core.report_i18n_mapper import format_scan_summary
        print()
        print(format_scan_summary(
            snapshot_id=snap.snapshot_id,
            route_count=route_count,
            api_hash=api_hash,
            snapshot_hash=snap.snapshot_hash,
            timestamp=snap.timestamp,
        ))
    else:
        print(f"CAGL SNAPSHOT {snap.snapshot_id}")
        print(f"  API routes:      {route_count}")
        print(f"  API contract hash: {api_hash}")
        print(f"  Snapshot hash:   {snap.snapshot_hash}")
        print(f"  Timestamp:       {snap.timestamp}")
    return 0


def cmd_freeze(version: str, notes: str = "", lang: str = "vi"):
    """Freeze current API contract + PSR version."""
    from src.core.psr.version import VersionFreeze
    vf = VersionFreeze()
    manifest = vf.freeze(version, notes=notes)
    if lang == "vi":
        from src.core.report_i18n_mapper import format_freeze_summary
        print()
        print(format_freeze_summary(
            version=manifest.version,
            api_hash=manifest.api_contract_hash,
            semantic_hash=manifest.semantic_contract_hash,
            commit=manifest.git_commit,
            created_at=manifest.created_at,
        ))
    else:
        print(f"CAGL FREEZE {manifest.version}")
        print(f"  API contract hash:   {manifest.api_contract_hash}")
        print(f"  Semantic contract:   {manifest.semantic_contract_hash}")
        print(f"  Git commit:          {manifest.git_commit}")
        print(f"  Created at:          {manifest.created_at}")
    return 0


def cmd_diff(snapshot_id: str, lang: str = "vi"):
    """Replay a snapshot and show API route changes."""
    from src.core.psr.replay import DeterministicReplayEngine
    engine = DeterministicReplayEngine()
    result = engine.replay(snapshot_id)
    if result is None:
        msg = f"KHÔNG TÌM THẤY: {snapshot_id}" if lang == "vi" else f"SNAPSHOT NOT FOUND: {snapshot_id}"
        print(msg)
        return 1
    if lang == "vi":
        from src.core.report_i18n_mapper import format_drift_report
        print()
        print(format_drift_report(
            snapshot_id=snapshot_id,
            match=result.match,
            diffs=result.diffs,
            duration_ms=result.replay_duration_ms,
        ))
    else:
        print(f"CAGL DIFF {snapshot_id}")
        print(f"  Match:           {result.match}")
        print(f"  Replay time:     {result.replay_duration_ms:.1f}ms")
        for d in result.diffs:
            icon = "+" if d.match else "x"
            print(f"  {icon} {d.field} {'match' if d.match else 'DRIFT'}")
    return 0 if result.match else 1


def main():
    parser = argparse.ArgumentParser(description="CAGL — Canonical API Gateway Layer")
    sub = parser.add_subparsers(dest="command", required=True)

    verify_parser = sub.add_parser("verify", help="Run route verification")
    verify_parser.add_argument(
        "--mode", choices=["SHADOW", "WARN", "STRICT"], default="WARN",
        help="Verification mode (default: WARN)",
    )
    verify_parser.add_argument("--lang", choices=["en", "vi"], default="vi", help="Output language")

    snap_parser = sub.add_parser("snapshot", help="Capture PSR snapshot with API routes")
    snap_parser.add_argument("--output", help="Optional output path")
    snap_parser.add_argument("--lang", choices=["en", "vi"], default="vi", help="Output language")

    freeze_parser = sub.add_parser("freeze", help="Freeze current API contract + PSR version")
    freeze_parser.add_argument("version", help="Version string (e.g. CAO v3.2)")
    freeze_parser.add_argument("--notes", default="", help="Release notes")
    freeze_parser.add_argument("--lang", choices=["en", "vi"], default="vi", help="Output language")

    diff_parser = sub.add_parser("diff", help="Replay a snapshot and check API route drift")
    diff_parser.add_argument("snapshot_id", help="Snapshot ID (e.g. SNAP_20260601_...)")
    diff_parser.add_argument("--lang", choices=["en", "vi"], default="vi", help="Output language")

    args = parser.parse_args()
    if args.command == "verify":
        sys.exit(cmd_verify(mode=args.mode, lang=args.lang))
    elif args.command == "snapshot":
        sys.exit(cmd_snapshot(lang=args.lang))
    elif args.command == "freeze":
        sys.exit(cmd_freeze(args.version, notes=args.notes, lang=args.lang))
    elif args.command == "diff":
        sys.exit(cmd_diff(args.snapshot_id, lang=args.lang))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
