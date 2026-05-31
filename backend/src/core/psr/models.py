"""PSR data models — snapshot, audit, version, replay diff."""
from __future__ import annotations
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


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


@dataclass
class PSRVersion:
    """Immutable version manifest for a CAO release."""
    version: str                     # e.g. "CAO v3.1"
    git_commit: str                  # current HEAD
    snapshot_hash: str               # hash of the frozen snapshot
    semantic_contract_hash: str      # hash of USCL label_vi mappings
    created_at: str
    notes: str = ""


@dataclass
class PSRSnapshot:
    """All system layer states at one point in time.

    Fields are dicts so the snapshot is JSON-serializable without a schema
    dependency — every layer serialises via ``to_dict()``.
    """
    snapshot_id: str                 # timestamp-based unique ID
    timestamp: str
    regime: dict                     # output of detect_regime()
    market_state: dict               # output of build_market_state()
    gold: dict                       # aggregate_gold() output (excl. semantic)
    trust: dict                      # aggregate_trust() output (excl. semantic)
    data_quality: dict               # DIS + DIVI + events
    weekly_report: Optional[dict] = None  # full build_weekly_report() output
    snapshot_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> PSRSnapshot:
        return PSRSnapshot(**d)


@dataclass
class PSRAuditEntry:
    """Single append-only audit record for one significant system output."""
    entry_id: str                    # unique ID
    timestamp: str
    snapshot_id: str                 # links to PSRSnapshot
    source: str                      # "weekly_report" | "gold" | "cao_gate" | etc.
    label_vi: str
    explanation_vi: str
    severity: float
    regime: str
    dis: float
    divi: float
    trust_status: str
    psr_version: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> PSRAuditEntry:
        return PSRAuditEntry(**d)


@dataclass
class PSRDiff:
    """Difference between two snapshots or a replay comparison."""
    field: str
    original: object
    replayed: object
    match: bool


@dataclass
class PSRReplayResult:
    """Result of a deterministic replay run."""
    snapshot_id: str
    timestamp: str
    match: bool
    diffs: list[PSRDiff]
    replay_duration_ms: float
