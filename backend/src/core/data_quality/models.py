"""DataQuality event models — first-class CAO signals, not log lines."""
from __future__ import annotations
import sys
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
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


class EventType(str, Enum):
    CHAINED_ASSIGNMENT = "CHAINED_ASSIGNMENT"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    MISSING_DATA = "MISSING_DATA"
    UPSTREAM_ANOMALY = "UPSTREAM_ANOMALY"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STALE_DATA = "STALE_DATA"
    BOUNDARY_EXCEPTION = "BOUNDARY_EXCEPTION"


class EventSeverity(str, Enum):
    TRACE = "TRACE"        # informational, no impact
    MINOR = "MINOR"        # mild noise, single field
    MODERATE = "MODERATE"  # degraded signal in one pipeline
    SEVERE = "SEVERE"      # systemic degradation
    CRITICAL = "CRITICAL"  # pipeline fundamentally unreliable

_SEVERITY_WEIGHTS = {
    EventSeverity.TRACE: 0.02,
    EventSeverity.MINOR: 0.10,
    EventSeverity.MODERATE: 0.25,
    EventSeverity.SEVERE: 0.50,
    EventSeverity.CRITICAL: 0.85,
}


@dataclass
class DataQualityEvent:
    """A single data-integrity observation captured at a boundary layer.

    Never raised — always emitted.  The CAO/monitor layer decides impact.
    """
    source: str                     # vnstock / yfinance / internal
    module: str                     # gold_price / regime_engine / etc.
    event_type: EventType
    severity: EventSeverity
    message: str = ""
    affected_fields: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def weight(self) -> float:
        return _SEVERITY_WEIGHTS.get(self.severity, 0.1)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "module": self.module,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "affected_fields": self.affected_fields,
            "weight": self.weight,
            "timestamp": self.timestamp.isoformat(),
            "extra": {k: str(v) for k, v in self.extra.items()},
        }


@dataclass
class DataQualitySnapshot:
    """Point-in-time health of the data pipeline (per source)."""
    source: str
    event_count: int = 0
    total_weight: float = 0.0
    max_severity: EventSeverity = EventSeverity.TRACE
    window_minutes: int = 60


@dataclass
class DataIntegrityReport:
    """Full integrity summary for CAO consumption.

    ``integrity_score`` is the DIS — modulates trust accumulation speed.
        Uses weakest-link aggregation (10th percentile of per-source scores).

    ``divi`` is the Data Integrity Volatility Index — rolling variance
        of DIS over the last N snapshots.  High DIVI → unstable pipeline
        even if DIS looks acceptable.
    """
    integrity_score: float           # weakest-link DIS ∈ [0, 1]
    divi: float                      # Data Integrity Volatility Index ∈ [0, 1]
    source_scores: dict[str, float]  # per-pipeline breakdown
    events_in_window: int
    dominant_severity: EventSeverity
    recommendation: str              # narrative for CAO
    integrity_method: str = "weakest_link_10th_pct"
    timestamp: datetime = field(default_factory=datetime.now)
