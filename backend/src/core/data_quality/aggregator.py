"""Time-windowed aggregation — produces summary stats for dashboards.

Not the CAO integration path (that's in quality_score_engine.py).
This is the human-readable layer: dashboard snapshots, trend lines.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.core.data_quality.quality_score_engine import (
    QualityScoreEngine,
    get_quality_engine,
)
from src.core.data_quality.registry import get_registry

logger = logging.getLogger(__name__)


class DataQualityAggregator:
    """Produces dashboard-friendly summaries from registry events.

    Stateless — reads from registry, never writes.
    """

    def __init__(
        self,
        engine: QualityScoreEngine | None = None,
        registry=None,
    ):
        self._engine = engine or get_quality_engine()
        self._registry = registry or get_registry()

    def hourly_trend(self, hours: int = 24) -> list[dict]:
        """Sliding hourly DIS + DIVI values.  One point per hour."""
        now = datetime.now()
        points = []
        for h in range(hours):
            start = now - timedelta(hours=h + 1)
            end = now - timedelta(hours=h)
            report = self._engine.compute_report(now=end)
            points.append(
                {
                    "hour": start.strftime("%Y-%m-%d %H:00"),
                    "dis": report.integrity_score,
                    "divi": report.divi,
                    "events": report.events_in_window,
                    "severity": report.dominant_severity.value,
                }
            )
        return points

    def source_breakdown(self) -> list[dict]:
        """Per-pipeline integrity scores."""
        snapshots = self._registry.source_summary()
        report = self._engine.compute_report()
        # [WHY] Khi báo cáo bị `degraded` (sổ sự kiện không đọc được), `source_scores` rỗng —
        # `.get(src, 1.0)` cũ sẽ biến "mù thông tin" thành "mọi nguồn đều hoàn hảo". Nay trả
        # None cho từng nguồn để dashboard/consumer buộc phải hiển thị UNKNOWN.
        degraded = report.degraded or report.integrity_score is None
        return [
            {
                "source": src,
                "integrity_score": None if degraded else report.source_scores.get(src, 1.0),
                "events": snap.events_in_window if snap else 0,
                "max_severity": snap.max_severity.value if snap else "TRACE",
            }
            for src, snap in snapshots.items()
        ]

    def last_minutes(self, minutes: int = 15) -> dict:
        """Quick summary for the last N minutes."""
        report = self._engine.compute_report()
        return {
            "dis": report.integrity_score,
            "divi": report.divi,
            "events": report.events_in_window,
            "severity": report.dominant_severity.value,
            "recommendation": report.recommendation,
        }
