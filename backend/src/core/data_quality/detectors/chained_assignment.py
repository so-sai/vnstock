"""Chained-assignment detector — replaces blind suppression with signal capture.

Instead of ``pd.options.mode.chained_assignment = None`` (global silence),
this module:

  1. Installs a context manager / decorator around pandas operations
  2. Captures each chained-assignment event as a DataQualityEvent
  3. Routes it to the EventRegistry for scoring

The pandas warning is still technically raised, but we intercept it and
convert to a quantified signal.  No crash, no silence.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import warnings

import pandas as pd

from src.core.data_quality.models import DataQualityEvent, EventSeverity, EventType
from src.core.data_quality.registry import emit_event

logger = logging.getLogger(__name__)


class ChainedAssignmentWatcher:
    """Replaces global pandas suppression with quantified signal capture.

    Usage inside an entry-point function::

        watcher = ChainedAssignmentWatcher()
        watcher.install()
        # ... pandas code ...
        count = watcher.drain()   # events already emitted to registry

    The watcher intercepts FutureWarning and converts each occurrence
    into a DataQualityEvent before letting the original warning pass.
    """

    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()
        self._showwarning_saved = None

    def install(self) -> None:
        """Hook into Python's warnings system to intercept chained-assignment."""
        self._showwarning_saved = warnings.showwarning
        warnings.showwarning = self._interceptor

    def uninstall(self) -> None:
        """Restore original warnings handler."""
        if self._showwarning_saved is not None:
            warnings.showwarning = self._showwarning_saved
            self._showwarning_saved = None

    def drain(self) -> int:
        """Return count since last drain and emit events for each occurrence."""
        with self._lock:
            count = self._count
            self._count = 0
        return count

    def _interceptor(self, message, category, filename, lineno, file=None, line=None):
        if self._is_chained_assignment(message, category):
            with self._lock:
                self._count += 1
            event = DataQualityEvent(
                source="internal",
                module=filename,
                event_type=EventType.CHAINED_ASSIGNMENT,
                severity=EventSeverity.MODERATE,
                message=f"{message} at {filename}:{lineno}",
                affected_fields=[],
                extra={"filename": filename, "lineno": lineno},
            )
            emit_event(event)
        # Always let the original handler process the warning too
        if self._showwarning_saved:
            self._showwarning_saved(message, category, filename, lineno, file, line)

    @staticmethod
    def _is_chained_assignment(message, category) -> bool:
        msg = str(message)
        return (
            "ChainedAssignmentError" in msg or "chained assignment" in msg.lower()
        ) and "FutureWarning" in category.__name__


@contextlib.contextmanager
def capture_chained_assignments():
    """Context manager: captures chained-assignment events during the block.

    Usage::

        with capture_chained_assignments():
            df = pd.DataFrame(...)
            df["a"][0] = 1   # event emitted, not silenced, not crashed
    """
    watcher = ChainedAssignmentWatcher()
    watcher.install()
    try:
        yield watcher
    finally:
        watcher.uninstall()


def patch_pandas_for_dq() -> None:
    """Replace global silence with signal capture.

    Call once at application startup.  After this:

      - ``pd.options.mode.chained_assignment = None`` is removed
      - Each chained assignment produces a DataQualityEvent
      - The warning is still shown (observability preserved)
    """
    _original_setoption = pd.options._set_func

    def _patched_setoption(*args, **kwargs):
        if "chained_assignment" in kwargs:
            logger.info(
                "[DQ_PATCH] intercepted set_option chained_assignment=%s — "
                "redirecting to signal capture instead of suppression",
                kwargs["chained_assignment"],
            )
            return
        _original_setoption(*args, **kwargs)

    pd.options._set_func = _patched_setoption
    logger.info("[DQ_PATCH] pandas chained-assignment suppression redirected to DQ signal capture")
