"""DataIntegrityScore (DIS) — quantifies data reliability as a [0, 1] scalar.

Primary metric:
    DIS = weakest-link of per-source scores (10th percentile)
        = 1 - min(burden_per_source / 5.0, 1.0)  ... per source
        = percentile([scores], 10)

Secondary metric:
    DIVI = variance(DIS over last N snapshots)
        High DIVI → unstable pipeline even if DIS looks acceptable.

Design:
    - Always returns a score (never raises)
    - Score 1.0 = pristine pipeline, 0.0 = total degradation
    - Uses registry's sliding window + rolling history buffer
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

from src.core.data_quality.models import (
    DataIntegrityReport,
    EventSeverity,
    EventType,
)
from src.core.data_quality.registry import EventRegistry, get_registry

logger = logging.getLogger(__name__)

_EVENT_TYPE_WEIGHTS = {
    EventType.CHAINED_ASSIGNMENT: 1.0,
    EventType.SCHEMA_DRIFT: 1.2,
    EventType.TYPE_MISMATCH: 0.8,
    EventType.MISSING_DATA: 0.4,
    EventType.UPSTREAM_ANOMALY: 0.9,
    EventType.STALE_DATA: 0.5,
    EventType.BOUNDARY_EXCEPTION: 1.0,
}

_DECAY_HALFLIFE = 30.0
_DIVI_WINDOW = 20  # number of snapshots to track for volatility
_MAX_SOURCE_BURDEN = 5.0  # per-source burden cap


def _decay_weight(minutes_ago: float) -> float:
    return 2.0 ** (-minutes_ago / _DECAY_HALFLIFE)


class QualityScoreEngine:
    """Computes DIS (weakest-link) + DIVI (volatility) from registry events.

    Thread-safe (registry handles its own locking).  Maintains an internal
    rolling history of recent DIS values for DIVI computation.
    """

    def __init__(self, registry: EventRegistry | None = None):
        self._registry = registry or get_registry()
        self._dis_history: deque[float] = deque(maxlen=_DIVI_WINDOW)

    def compute_report(
        self,
        source: str | None = None,
        now: datetime | None = None,
    ) -> DataIntegrityReport:
        """Full integrity report — weakest-link DIS + DIVI."""
        now = now or datetime.now()
        try:
            events = self._registry.get_events(source=source)
        except Exception as e:  # mọi lỗi đọc sổ đều là "chưa đo được"
            logger.exception("[DQ_SCORE] get_events failed")
            # [WHY] Trước 09/11/2026 nhánh này trả `_empty_report()` với `integrity_score=1.0`
            # + `recommendation="clean (no events)"` ⇒ đúng lớp lỗi in-band: "không đọc được
            # sổ" bị báo thành "dữ liệu hoàn hảo", rồi chảy vào `activation_gate` (DIS>=0.50)
            # và `trust_accumulator`. Nay trả `None` (không biết) + `degraded=True`, KHÔNG
            # append vào `_dis_history` (một điểm 1.0 giả sẽ làm DIVI/trend sai vĩnh viễn).
            return self._degraded_report(now, reason=type(e).__name__)

        if not events:
            # [WHY] Giữ 1.0 ở đây là CÓ CHỦ ĐÍCH, không phải nới lỏng: registry SỐNG và trả
            # rỗng nghĩa là "chưa có sự kiện nào được ghi" — khác với "không đọc được sổ"
            # (nhánh except ở trên). Nếu biến ca này thành unknown/degraded thì mọi cổng đỏ
            # ngay sau khi cài (registry mới luôn rỗng), và người trực sẽ tắt cổng.
            report = DataIntegrityReport(
                integrity_score=1.0,
                divi=0.0,
                source_scores={},
                events_in_window=0,
                dominant_severity=EventSeverity.TRACE,
                recommendation="clean",
                timestamp=now,
            )
            self._append_history(1.0)
            return report

        source_burden: dict[str, float] = {}
        max_severity = EventSeverity.TRACE

        for event in events:
            type_w = _EVENT_TYPE_WEIGHTS.get(event.event_type, 0.5)
            age_m = (now - event.timestamp).total_seconds() / 60.0
            w = event.weight * type_w * _decay_weight(age_m)
            src = event.source
            source_burden[src] = source_burden.get(src, 0.0) + w
            if _severity_rank(event.severity) > _severity_rank(max_severity):
                max_severity = event.severity

        source_scores = {
            src: max(0.0, 1.0 - min(burden, _MAX_SOURCE_BURDEN) / _MAX_SOURCE_BURDEN) for src, burden in source_burden.items()
        }

        integrity = self._weakest_link(source_scores)
        divi = self._compute_divi()
        rec = self._recommendation(integrity, divi, max_severity)

        report = DataIntegrityReport(
            integrity_score=round(integrity, 4),
            divi=round(divi, 4),
            source_scores=source_scores,
            events_in_window=len(events),
            dominant_severity=max_severity,
            recommendation=rec,
            timestamp=now,
        )
        self._append_history(integrity)
        return report

    def source_scores(self) -> dict[str, float]:
        """Quick per-pipeline scores (wraps compute_report)."""
        return self.compute_report().source_scores

    def get_divi(self) -> float:
        """Return current DIVI without recomputing full report."""
        return self._compute_divi()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _weakest_link(scores: dict[str, float]) -> float:
        """10th percentile of per-source scores.

        More robust than min() — avoids single-point collapse while
        still penalizing the weakest pipeline.
        """
        if not scores:
            return 1.0
        vals = sorted(scores.values())
        n = len(vals)
        idx = max(0, min(n - 1, int(n * 0.10)))
        return vals[idx]

    def _append_history(self, score: float) -> None:
        self._dis_history.append(score)

    def _compute_divi(self) -> float:
        """Rolling variance of recent DIS snapshots, normalized to [0, 1].

        High DIVI → integrity is fluctuating (unstable pipeline).
        """
        if len(self._dis_history) < 3:
            return 0.0
        vals = list(self._dis_history)
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        # Normalize: max possible variance for [0,1] range is 0.25
        normalized = min(1.0, variance / 0.25)
        return normalized

    def _degraded_report(self, now: datetime, *, reason: str) -> DataIntegrityReport:
        """[WHY] Báo cáo khi CHƯA ĐO ĐƯỢC (sổ sự kiện lỗi). Đây KHÔNG phải "sạch" và cũng
        KHÔNG phải "tệ nhất": `integrity_score=None` + `degraded=True` buộc consumer phải xử
        lý tường minh, thay vì lặng lẽ đọc một con số 1.0 không có cơ sở đo.
        """
        return DataIntegrityReport(
            integrity_score=None,
            divi=0.0,
            source_scores={},
            events_in_window=0,
            dominant_severity=EventSeverity.TRACE,
            recommendation=f"UNKNOWN: không đọc được sổ sự kiện ({reason})",
            timestamp=now,
            degraded=True,
        )

    @staticmethod
    def _recommendation(
        score: float,
        divi: float,
        severity: EventSeverity,
    ) -> str:
        if score >= 0.95 and divi < 0.05:
            return "clean"
        if score >= 0.85 and divi < 0.15:
            return "mild_noise"
        if divi >= 0.30:
            return "unstable"
        if score >= 0.70:
            if severity in (EventSeverity.SEVERE, EventSeverity.CRITICAL):
                return "caution_severe"
            return "degraded"
        if severity in (EventSeverity.SEVERE, EventSeverity.CRITICAL):
            return "block_promotion"
        return "caution"


def _severity_rank(s: EventSeverity) -> int:
    rank = {
        EventSeverity.TRACE: 0,
        EventSeverity.MINOR: 1,
        EventSeverity.MODERATE: 2,
        EventSeverity.SEVERE: 3,
        EventSeverity.CRITICAL: 4,
    }
    return rank.get(s, 0)


_engine: QualityScoreEngine | None = None


def get_quality_engine() -> QualityScoreEngine:
    global _engine
    if _engine is None:
        _engine = QualityScoreEngine()
    return _engine


def compute_integrity_score() -> float | None:
    """Convenience: return just the DIS scalar (weakest-link) — `None` khi chưa đo được.

    [WHY] Không ép `None` về một số: người gọi phải tự quyết định fail-closed hay không, và
    một con số bịa (1.0/0.0) sẽ đi thẳng vào cổng activation mà không ai thấy.
    """
    return get_quality_engine().compute_report().integrity_score


def compute_divi() -> float:
    """Convenience: return just the DIVI scalar."""
    return get_quality_engine().compute_report().divi
