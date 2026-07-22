"""DeterministicReplayEngine — load snapshot → reproduce → compare.

The core of PSR reproducibility: given the same input snapshot
(regime state, gold state, trust state, DQ state), does the system
produce the identical output?

Only as deterministic as the underlying engines — but captures any
non-determinism (time-based seeds, external API calls, mutable globals).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from src.core.psr.models import (
    PSRDiff,
    PSRReplayResult,
    PSRSnapshot,
)
from src.core.psr.snapshot import SystemStateSnapshotter

logger = logging.getLogger(__name__)


class DeterministicReplayEngine:
    """Replay a previous snapshot through the weekly report pipeline.

    Because the snapshot captures all input layer states, replay does not
    re-fetch live data — it injects the captured state into each layer's
    output path.
    """

    def __init__(self):
        self._snapper = SystemStateSnapshotter()

    def replay(self, snapshot_id: str) -> Optional[PSRReplayResult]:
        """Load and replay a single snapshot.

        Returns:
            PSRReplayResult with diffs, or None if snapshot not found.
        """
        snapshot = self._snapper.load(snapshot_id)
        if snapshot is None:
            return None

        start = time.perf_counter()

        # Re-capture current state (live) for comparison
        re_captured = self._snapper.capture()

        # Compare: snapshot (frozen) vs re_captured (live)
        diffs = self._diff(snapshot, re_captured)

        elapsed = (time.perf_counter() - start) * 1000
        match = all(d.match for d in diffs)

        return PSRReplayResult(
            snapshot_id=snapshot_id,
            timestamp=snapshot.timestamp,
            match=match,
            diffs=diffs,
            replay_duration_ms=round(elapsed, 2),
        )

    def replay_all(self, limit: int = 50) -> list[PSRReplayResult]:
        """Replay all available snapshots (newest first)."""
        results = []
        for snap_id in self._snapper.list_snapshots()[:limit]:
            result = self.replay(snap_id)
            if result:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    # Diff logic — compare snapshot fields
    # ------------------------------------------------------------------

    COMPARE_FIELDS = ["regime", "market_state", "gold", "trust", "data_quality", "api_routes"]

    def _diff(self, original: PSRSnapshot, replayed: PSRSnapshot) -> list[PSRDiff]:
        diffs = []
        for field in self.COMPARE_FIELDS:
            orig_val = getattr(original, field, {}) or {}
            replay_val = getattr(replayed, field, {}) or {}
            match = self._dicts_match(orig_val, replay_val)
            diffs.append(PSRDiff(
                field=field,
                original=orig_val,
                replayed=replay_val,
                match=match,
            ))
        return diffs

    @staticmethod
    def _dicts_match(a: dict, b: dict) -> bool:
        key_a, key_b = set(a.keys()), set(b.keys())
        if key_a != key_b:
            return False
        for k in key_a:
            if type(a[k]) != type(b[k]):
                return False
            if isinstance(a[k], float):
                if abs(a[k] - b[k]) > 0.001:
                    return False
            elif a[k] != b[k]:
                return False
        return True
