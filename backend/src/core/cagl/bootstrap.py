"""Boot-time CAGL verifier — runs at FastAPI startup to validate route integrity.

Usage::

    from src.core.cagl import verify_cagl

    findings = verify_cagl(app, mode="WARN")

Modes:
  - ``"SHADOW"`` — observe only, print findings
  - ``"WARN"``   — log all findings (default)
  - ``"STRICT"`` — raise SystemExit on errors
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from src.core.cagl.contract import ContractValidator
from src.core.cagl.models import EndpointSpec, ScanResult
from src.core.cagl.registry import APIRegistry
from src.core.cagl.scanner import RouteScanner
from src.core.cagl.shadow_detector import ShadowDetector


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent.parent
        root = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root = current
                break
            current = current.parent
    for p in (root, root / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

logger = logging.getLogger(__name__)


def verify_cagl(
    app: FastAPI,
    mode: str = "WARN",
    expected_routes: Optional[list[EndpointSpec]] = None,
) -> ScanResult:
    """Run full CAGL verification on a FastAPI application.

    Stages:
      1. Scan runtime routes (``RouteScanner``)
      2. Validate contract (``ContractValidator``)
      3. Diff against expected registry if provided
      4. Handle findings per ``mode``

    Parameters
    ----------
    app:
        The running FastAPI application instance.
    mode:
        One of ``"SHADOW"``, ``"WARN"``, ``"STRICT"``.
    expected_routes:
        Optional list of expected ``EndpointSpec``. If provided, the scanner
        diff will also check for missing / unregistered routes.

    Returns
    -------
    ScanResult
        Aggregated result for programmatic inspection.
    """
    logger.info("[CAGL] Starting verification (mode=%s) ...", mode)

    scanner = RouteScanner()
    runtime = scanner.scan(app)
    logger.info("[CAGL] Scanned %d runtime routes", len(runtime))

    result = ScanResult(runtime_routes=runtime)

    # Stage 1 — contract validation
    validator = ContractValidator()
    contract_result = validator.validate(runtime)
    result.merge(contract_result)

    # Stage 2 — registry diff (if expected routes provided)
    if expected_routes:
        registry = APIRegistry(expected_routes)
        registry_diff = registry.diff(runtime)
        result.findings.extend(registry_diff)

    # Stage 3 — shadow / phantom detection from OpenAPI
    try:
        openapi_schema = app.openapi()
        openapi_paths = set()
        for path, methods in openapi_schema.get("paths", {}).items():
            for method in methods:
                openapi_paths.add(f"{method.upper()}:{path}")
        detector = ShadowDetector()
        phantom = detector.detect_undocumented(runtime, openapi_paths)
        result.findings.extend(phantom)
    except Exception:
        logger.warning("[CAGL] OpenAPI introspection failed, skipping phantom detection")

    # Report
    _report_findings(result, mode)

    # Strict enforcement
    if mode == "STRICT" and not result.is_valid:
        logger.error("[CAGL] STRICT mode — %d error(s) found, aborting", len(result.errors))
        raise SystemExit(1)

    logger.info("[CAGL] Verification complete: %d errors, %d warnings",
                len(result.errors), len(result.warnings))
    return result


def _report_findings(result: ScanResult, mode: str) -> None:
    for f in result.findings:
        icon = {"error": "x", "warning": "!", "info": "i"}.get(f.severity, ".")
        line = f"  {icon} [{f.category}] {f.path}"
        if f.severity == "error":
            logger.error("%s — %s", line, f.message)
        elif f.severity == "warning":
            logger.warning("%s — %s", line, f.message)
        else:
            logger.info("%s — %s", line, f.message)
