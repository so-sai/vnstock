"""Execution Contract Layer v1 — Deployment determinism boundary."""

EXECUTION_CONTRACT_VERSION = "ECL_v1.0.0"

from src.core.ecl.verifier import (
    compute_execution_hash,
    verify_dependency_lock,
    ecl_self_check,
    EXECUTION_CORE_PATHS,
)
from src.core.ecl.classifier import is_execution_core, is_observability

__all__ = [
    "EXECUTION_CONTRACT_VERSION",
    "EXECUTION_CORE_PATHS",
    "compute_execution_hash",
    "verify_dependency_lock",
    "ecl_self_check",
    "is_execution_core",
    "is_observability",
]
