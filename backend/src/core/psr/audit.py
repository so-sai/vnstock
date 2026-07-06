"""DecisionAuditTrail — append-only JSONL audit log.

Every significant system output (weekly report, gold scan, CAO gate verdict,
trust update) creates one append-only entry.  The log is:
  - Immutable (never modified in-place)
  - Sequential (time-ordered)
  - Replayable (can rebuild state up to any point)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.psr.models import PSRAuditEntry

logger = logging.getLogger(__name__)

try:
    from src.config import DATA_DIR as _BASE
    AUDIT_DIR = _BASE / "psr"
except ImportError:
    AUDIT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "psr"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"


class DecisionAuditTrail:
    """Append-only audit trail.  One JSON object per line.

    Usage::

        audit = DecisionAuditTrail()
        entry = audit.record(
            source="weekly_report",
            label_vi="thị trường đi ngang",
            explanation_vi="...",
            severity=0.3,
            regime="RANGING",
            dis=0.98,
            divi=0.02,
            trust_status="BLOCKED",
            snapshot_id=snap.snapshot_id,
        )
        entries = audit.replay(since="2026-05-01")
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or AUDIT_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        source: str,
        label_vi: str,
        explanation_vi: str,
        severity: float,
        regime: str,
        dis: float,
        divi: float,
        trust_status: str,
        snapshot_id: str = "",
        extra: dict = None,
    ) -> PSRAuditEntry:
        """Create and append a single audit entry."""
        entry = PSRAuditEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            snapshot_id=snapshot_id,
            source=source,
            label_vi=label_vi,
            explanation_vi=explanation_vi,
            severity=severity,
            regime=regime,
            dis=dis,
            divi=divi,
            trust_status=trust_status,
            psr_version=self._get_version(),
            extra=extra or {},
        )
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("[PSR_AUDIT] Write failed: %s", e)
        return entry

    def replay(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 1000,
    ) -> list[PSRAuditEntry]:
        """Read audit entries, newest first, with optional filters."""
        entries = []
        try:
            lines = self._path.read_text(encoding="utf-8").strip().split("\n")
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    entry = PSRAuditEntry.from_dict(d)
                except Exception:
                    continue
                if since and entry.timestamp < since:
                    continue
                if until and entry.timestamp > until:
                    continue
                if source and entry.source != source:
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error("[PSR_AUDIT] Read failed: %s", e)
        return entries

    def count(self) -> int:
        """Total number of audit entries."""
        try:
            lines = self._path.read_text(encoding="utf-8").strip().split("\n")
            return sum(1 for l in lines if l.strip())
        except FileNotFoundError:
            return 0

    def clear(self) -> None:
        """Truncate the audit trail (use with care — destroys history)."""
        self._path.write_text("", encoding="utf-8")
        logger.warning("[PSR_AUDIT] Cleared — history destroyed")

    @staticmethod
    def _get_version() -> str:
        try:
            from src.core.psr.version import get_current_version
            return get_current_version()
        except Exception:
            return "dev"
