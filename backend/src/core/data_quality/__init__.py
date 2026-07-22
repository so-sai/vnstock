"""Data Quality Monitor Layer — epistemic foundation for CAO.

Turns data-integrity issues into first-class CAO signals instead of silent
suppressions or passive logs.  Three layers:

  1. Signal Capture   : chained assignment, schema drift, missing data, upstream noise
  2. Risk Quantification : DataIntegrityScore (DIS) [0, 1]
  3. CAO Integration    : DIS modulates trust accumulation + promotion thresholds

Design rules:
  - NEVER block pipeline
  - NEVER throw from event emission
  - ALWAYS emit — CAO decides impact
"""

from src.core.data_quality.aggregator import DataQualityAggregator
from src.core.data_quality.hooks import data_quality_boundary
from src.core.data_quality.models import (
    DataIntegrityReport,
    DataQualityEvent,
    DataQualitySnapshot,
    EventSeverity,
    EventType,
)
from src.core.data_quality.quality_score_engine import QualityScoreEngine
from src.core.data_quality.registry import EventRegistry

__all__ = [
    "DataQualityEvent",
    "DataQualitySnapshot",
    "DataIntegrityReport",
    "EventSeverity",
    "EventType",
    "EventRegistry",
    "QualityScoreEngine",
    "DataQualityAggregator",
    "data_quality_boundary",
]
