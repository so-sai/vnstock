"""Thread-safe in-memory event registry with optional DB persistence.

Events are ALWAYS accepted (never block).  Registry maintains a sliding
window of the last N minutes for score computation.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from src.core.data_quality.models import (
    DataQualityEvent,
    DataQualitySnapshot,
)

logger = logging.getLogger(__name__)


class EventRegistry:
    """Central event store for the data quality monitor.

    Thread-safe, non-blocking, sliding-window based.
    """

    def __init__(self, window_minutes: int = 60):
        self._lock = threading.Lock()
        self._window = timedelta(minutes=window_minutes)
        self._events: list[DataQualityEvent] = []

    def emit(self, event: DataQualityEvent) -> None:
        """Record an event.  Never raises."""
        try:
            with self._lock:
                self._events.append(event)
        except Exception:
            logger.exception("[DQ_REGISTRY] emit failed — event dropped")

    def get_events(
        self,
        source: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
    ) -> list[DataQualityEvent]:
        """Return matching events within the sliding window, newest first."""
        cutoff = since or (datetime.now() - self._window)
        with self._lock:
            matches = [
                e
                for e in self._events
                if e.timestamp >= cutoff
                and (source is None or e.source == source)
                and (event_type is None or e.event_type.value == event_type)
            ]
        return sorted(matches, key=lambda e: e.timestamp, reverse=True)

    def snapshot(self, source: str | None = None) -> DataQualitySnapshot:
        """Aggregate events for a source into a single snapshot."""
        events = self.get_events(source=source)
        if not events:
            return DataQualitySnapshot(
                source=source or "all",
                window_minutes=int(self._window.total_seconds() / 60),
            )
        total_weight = sum(e.weight for e in events)
        max_sv = max(e.severity for e in events)
        return DataQualitySnapshot(
            source=source or "all",
            event_count=len(events),
            total_weight=total_weight,
            max_severity=max_sv,
            window_minutes=int(self._window.total_seconds() / 60),
        )

    def source_summary(self) -> dict[str, DataQualitySnapshot]:
        """Return a snapshot per unique source."""
        with self._lock:
            sources = {e.source for e in self._events}
        return {s: self.snapshot(source=s) for s in sources}

    def clear(self, older_than_minutes: int | None = None) -> int:
        """Evict old events.  Returns count removed."""
        cutoff = datetime.now() - timedelta(minutes=older_than_minutes or 120)
        with self._lock:
            before = len(self._events)
            self._events = [e for e in self._events if e.timestamp >= cutoff]
            return before - len(self._events)


# module-level singleton
_registry: EventRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> EventRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = EventRegistry()
    return _registry


def emit_event(event: DataQualityEvent) -> None:
    """Convenience: emit to the global singleton."""
    get_registry().emit(event)
