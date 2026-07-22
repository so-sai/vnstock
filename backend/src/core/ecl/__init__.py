"""Execution Contract Layer v1 — Deployment determinism boundary."""

EXECUTION_CONTRACT_VERSION = "ECL_v1.0.0"

from src.core.ecl.classifier import is_execution_core, is_observability
from src.core.ecl.verifier import (
    EXECUTION_CORE_PATHS,
    compute_execution_hash,
    ecl_self_check,
    verify_dependency_lock,
)

__all__ = [
    "EXECUTION_CONTRACT_VERSION",
    "EXECUTION_CORE_PATHS",
    "compute_execution_hash",
    "verify_dependency_lock",
    "ecl_self_check",
    "is_execution_core",
    "is_observability",
]
