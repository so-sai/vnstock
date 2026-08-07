"""Production Stress Release (PSR) — reproducibility boundary.

Transitions the system from "correct" to "provably reproducible".

Four layers:
  1. SystemStateSnapshotter  — captures all layer states at decision time
  2. DecisionAuditTrail      — append-only JSONL log of every significant output
  3. DeterministicReplayEngine — load snapshot → reproduce → compare
  4. VersionFreeze           — immutable version manifest + git integration
"""

from src.core.psr.audit import DecisionAuditTrail
from src.core.psr.models import (
    PSRAuditEntry,
    PSRDiff,
    PSRReplayResult,
    PSRSnapshot,
    PSRVersion,
)
from src.core.psr.replay import DeterministicReplayEngine
from src.core.psr.snapshot import SystemStateSnapshotter
from src.core.psr.version import VersionFreeze

__all__ = [
    "PSRVersion",
    "PSRSnapshot",
    "PSRAuditEntry",
    "PSRReplayResult",
    "PSRDiff",
    "SystemStateSnapshotter",
    "DecisionAuditTrail",
    "DeterministicReplayEngine",
    "VersionFreeze",
]
