"""SystemStateSnapshotter — captures all layer states at one point in time.

Used before every significant system decision so the exact causal context
can be reconstructed later.
"""
from __future__ import annotations
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.psr.models import PSRSnapshot

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "psr"


class SystemStateSnapshotter:
    """Captures all active system layer states into one immutable snapshot.

    Usage::

        snapper = SystemStateSnapshotter()
        snap = snapper.capture()
        snapper.persist(snap)
    """

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def capture(self) -> PSRSnapshot:
        """Gather current state from every active layer."""
        now = datetime.now()
        snap_id = now.strftime("SNAP_%Y%m%d_%H%M%S_%f")
        snapshot = PSRSnapshot(
            snapshot_id=snap_id,
            timestamp=now.isoformat(),
            regime=self._capture_regime(),
            market_state=self._capture_market_state(),
            gold=self._capture_gold(),
            trust=self._capture_trust(),
            data_quality=self._capture_data_quality(),
        )
        snapshot.snapshot_hash = self._hash(snapshot)
        return snapshot

    def persist(self, snapshot: PSRSnapshot) -> Path:
        """Write snapshot to disk as JSON."""
        path = DATA_DIR / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[PSR] Snapshot saved: %s", path)
        return path

    def load(self, snapshot_id: str) -> Optional[PSRSnapshot]:
        """Load a snapshot by ID."""
        path = DATA_DIR / f"{snapshot_id}.json"
        if not path.exists():
            logger.warning("[PSR] Snapshot not found: %s", path)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        snap = PSRSnapshot.from_dict(data)
        stored_hash = snap.snapshot_hash
        computed_hash = self._hash(snap)
        if stored_hash and stored_hash != computed_hash:
            logger.error("[PSR] Snapshot %s hash mismatch — data corrupted?", snapshot_id)
        return snap

    def list_snapshots(self) -> list[str]:
        """Return all available snapshot IDs sorted newest-first."""
        paths = sorted(DATA_DIR.glob("SNAP_*.json"), reverse=True)
        return [p.stem for p in paths]

    # ------------------------------------------------------------------
    # Internal capture helpers — each one is non-blocking
    # ------------------------------------------------------------------

    @staticmethod
    def _capture_regime() -> dict:
        try:
            from src.engine.regime_engine import detect_regime
            r = detect_regime()
            return {
                "status": r.get("status", "UNKNOWN"),
                "score": r.get("regime_score", 0.0),
                "details": str(r.get("details", {})),
            }
        except Exception as e:
            logger.warning("[PSR] Regime capture failed: %s", e)
            return {"error": str(e)}

    @staticmethod
    def _capture_market_state() -> dict:
        try:
            from src.core.market_state_coordinator import build_market_state
            s = build_market_state()
            return {
                "lci": s.get("liquidity_condition", "UNKNOWN"),
                "risk_appetite": s.get("risk_appetite", "UNKNOWN"),
                "phase": s.get("market_phase", "UNKNOWN"),
                "flow": s.get("dominant_flow", "UNKNOWN"),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _capture_gold() -> dict:
        try:
            from src.services.weekly_cognitive_report import aggregate_gold
            return aggregate_gold()
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _capture_trust() -> dict:
        try:
            from src.services.weekly_cognitive_report import aggregate_trust
            return aggregate_trust()
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _capture_data_quality() -> dict:
        try:
            from src.core.data_quality import QualityScoreEngine
            r = QualityScoreEngine().compute_report()
            return {
                "dis": r.integrity_score,
                "divi": r.divi,
                "events": r.events_in_window,
                "method": r.integrity_method,
                "recommendation": r.recommendation,
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _hash(snapshot: PSRSnapshot) -> str:
        raw = json.dumps(snapshot.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
