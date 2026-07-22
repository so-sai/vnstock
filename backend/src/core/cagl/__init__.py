"""Canonical API Gateway Layer (CAGL) — API integrity & contract enforcement.

Provides:
  - Route scanning (runtime introspection)
  - Canonical route registry (single source of truth)
  - Contract validation (prefix, version, naming rules)
  - Shadow detection (phantom / undocumented endpoints)
  - Boot-time verification (STRICT / WARN / SHADOW modes)
  - Optional ASGI middleware for request-level validation

Usage::

    from src.core.cagl import verify_cagl

    findings = verify_cagl(app, mode="WARN")
"""
from src.core.cagl.bootstrap import verify_cagl
from src.core.cagl.contract import ContractValidator
from src.core.cagl.models import EndpointSpec, ScanResult, ValidationFinding
from src.core.cagl.registry import APIRegistry
from src.core.cagl.scanner import RouteScanner
from src.core.cagl.shadow_detector import ShadowDetector

__all__ = [
    "EndpointSpec",
    "ValidationFinding",
    "ScanResult",
    "RouteScanner",
    "APIRegistry",
    "ContractValidator",
    "ShadowDetector",
    "verify_cagl",
]
