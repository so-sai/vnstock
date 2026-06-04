"""Shadow route detection — compare multiple sources to find drifts."""
from __future__ import annotations
import sys
import logging
from pathlib import Path
from typing import List, Optional

from src.core.cagl.models import EndpointSpec, ValidationFinding, ScanResult
from src.core.cagl.scanner import RouteScanner
from src.core.cagl.registry import APIRegistry


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


class ShadowDetector:
    """Detect route drift across multiple sources.

    Sources compared:
      - FastAPI app.routes (runtime)
      - Canonical registry (expected)
      - OpenAPI schema (generated spec)
      - Static root endpoint list (``/`` endpoint)
    """

    def detect_phantom(self, openapi_paths: set[str], runtime_routes: list[EndpointSpec]) -> list[ValidationFinding]:
        """Find routes declared in OpenAPI but missing from runtime."""
        finding: list[ValidationFinding] = []
        runtime_set = {f"{r.method}:{r.path}" for r in runtime_routes}
        for oa_key in openapi_paths:
            if oa_key not in runtime_set:
                method, path = oa_key.split(":", 1)
                finding.append(ValidationFinding(
                    severity="error",
                    category="phantom",
                    path=path,
                    message=f"OpenAPI declares {method} {path} but no runtime route found",
                ))
        return finding

    def detect_undocumented(self, runtime_routes: list[EndpointSpec], openapi_paths: set[str]) -> list[ValidationFinding]:
        """Find runtime routes that are NOT in the OpenAPI schema."""
        findings: list[ValidationFinding] = []
        runtime_set = {f"{r.method}:{r.path}" for r in runtime_routes}
        for key in runtime_set - openapi_paths:
            method, path = key.split(":", 1)
            findings.append(ValidationFinding(
                severity="warning",
                category="undocumented",
                path=path,
                message=f"Runtime route {method} {path} is missing from OpenAPI schema",
            ))
        return findings

    def detect_against_declared_list(
        self,
        declared: list[str],
        runtime_routes: list[EndpointSpec],
    ) -> list[ValidationFinding]:
        """Compare a static list of declared endpoint paths against runtime.

        Useful for checking the ``/`` root endpoint's declared path list.
        """
        findings: list[ValidationFinding] = []
        runtime_paths = {r.path for r in runtime_routes}
        for decl_path in declared:
            if decl_path not in runtime_paths:
                findings.append(ValidationFinding(
                    severity="error",
                    category="phantom",
                    path=decl_path,
                    message=f"Declared endpoint '{decl_path}' has no matching runtime route",
                ))
        return findings
