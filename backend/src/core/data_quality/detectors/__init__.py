"""Detectors — capture specific categories of data-quality events.

Each detector:
  - Is non-intrusive (never blocks)
  - Emits to EventRegistry
  - Has a lightweight ``watch`` / ``install`` interface
"""

from src.core.data_quality.detectors.chained_assignment import (
    ChainedAssignmentWatcher,
    patch_pandas_for_dq,
)

__all__ = [
    "ChainedAssignmentWatcher",
    "patch_pandas_for_dq",
]
