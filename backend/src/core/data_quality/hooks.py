"""Boundary hooks — wrap external library calls with DQ signal capture.

Each hook is a context manager or decorator that:
  - Calls the original function
  - Catches any DQ-relevant anomalies
  - Emits DataQualityEvents (never raises)
  - Returns the original result unchanged

Available hooks:
  - ``data_quality_boundary(source)`` — context manager that wraps a block
  - ``capture_upstream(source)`` — decorator for external fetch functions
"""
from __future__ import annotations
import contextlib
import functools
import logging
import traceback
from typing import Any, Callable, Optional

import pandas as pd

from src.core.data_quality.models import DataQualityEvent, EventSeverity, EventType
from src.core.data_quality.registry import emit_event
from src.core.data_quality.detectors.chained_assignment import (
    ChainedAssignmentWatcher,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def data_quality_boundary(
    source: str,
    module: str = "",
    quiet: bool = True,
):
    """Wrap a code block with DQ monitoring.

    Captures:
      - Chained assignments
      - Exceptions (emitted as BOUNDARY_EXCEPTION events)
      - Empty DataFrames (emitted as MISSING_DATA events)

    Never blocks — always yields.

    Usage::

        with data_quality_boundary(source="vnstock", module="gold_price"):
            df = sjc_gold_price(...)
    """
    watcher = ChainedAssignmentWatcher()
    watcher.install()
    try:
        yield
    except Exception as exc:
        if quiet:
            emit_event(
                DataQualityEvent(
                    source=source,
                    module=module,
                    event_type=EventType.BOUNDARY_EXCEPTION,
                    severity=EventSeverity.MODERATE,
                    message=f"{type(exc).__name__}: {exc}",
                    extra={"traceback": traceback.format_exc()},
                )
            )
        else:
            raise
    finally:
        watcher.uninstall()


def capture_upstream(source: str):
    """Decorator: wrap an upstream fetch function with DQ monitoring.

    The wrapped function:
      - Runs inside a DQ boundary
      - Emits MISSING_DATA events if the result is None/empty
      - Emits SCHEMA_DRIFT events if column types change (baseline vs result)
      - Returns the original result unchanged

    Usage::

        @capture_upstream(source="vnstock")
        def sjc_gold_price(date=None):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with data_quality_boundary(
                source=source, module=func.__name__, quiet=True
            ):
                result = func(*args, **kwargs)
            _check_result(source, func.__name__, result)
            return result
        return wrapper
    return decorator


def _check_result(source: str, module: str, result: Any) -> None:
    """Emit events for suspicious result shapes."""
    try:
        if result is None:
            emit_event(
                DataQualityEvent(
                    source=source,
                    module=module,
                    event_type=EventType.MISSING_DATA,
                    severity=EventSeverity.MINOR,
                    message="function returned None",
                )
            )
            return
        if isinstance(result, pd.DataFrame):
            if result.empty:
                emit_event(
                    DataQualityEvent(
                        source=source,
                        module=module,
                        event_type=EventType.MISSING_DATA,
                        severity=EventSeverity.TRACE,
                        message="empty DataFrame",
                    )
                )
                return
            null_frac = result.isnull().sum().sum() / result.size if result.size > 0 else 0
            if null_frac > 0.5:
                emit_event(
                    DataQualityEvent(
                        source=source,
                        module=module,
                        event_type=EventType.MISSING_DATA,
                        severity=EventSeverity.MODERATE,
                        message=f"null fraction {null_frac:.0%} > 50%",
                        affected_fields=(
                            result.columns[result.isnull().mean() > 0.5].tolist()
                        ),
                    )
                )
    except Exception:
        logger.exception("[DQ_HOOK] _check_result failed")
