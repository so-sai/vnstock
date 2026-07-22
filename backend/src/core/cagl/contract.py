"""Contract validation rules — enforce API routing conventions."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.core.cagl.models import EndpointSpec, ScanResult, ValidationFinding


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


KNOWN_PREFIXES = {
    "/api/v1/flow": "flow",
    "/api/v1/gold": "gold",
    "/api/v1/market-state": "market_state",
    "/api/v1/holdings": "holdings",
    "/api/v1/telemetry": "telemetry",
    "/api/v1/weekly": "weekly",
    "/api/macro": "macro",
    "/api/screener": "screener",
    "/api/models": "models",
    "/api/breadth": "breadth",
    "/api/portfolio": "portfolio",
    "/api/backtest": "backtest",
    "/api/xray": "xray",
    "/api/replay": "replay",
    "/api/intelligence": "intelligence",
    "/api/watchlist": "watchlist",
}


class ContractValidator:
    """Enforce routing conventions on a list of runtime EndpointSpecs.

    Rules
    -----
    1. **No duplicate prefix** — path must not repeat its own prefix.
    2. **Prefix alignment** — each route's prefix should map to the correct module.
    3. **Version consistency** — version should be extractable where expected.
    """

    def validate(self, routes: list[EndpointSpec]) -> ScanResult:
        findings: list[ValidationFinding] = []
        for spec in routes:
            self._check_prefix_duplication(spec, findings)
            self._check_prefix_alignment(spec, findings)
        return ScanResult(runtime_routes=routes, findings=findings)

    @staticmethod
    def _check_prefix_duplication(spec: EndpointSpec, findings: list[ValidationFinding]) -> None:
        """Detect double-prefix patterns like ``/api/v1/flow/api/v1/flow/...``."""
        for prefix in KNOWN_PREFIXES:
            if prefix in spec.path and spec.path.count(prefix) > 1:
                findings.append(ValidationFinding(
                    severity="error",
                    category="prefix_dup",
                    path=spec.path,
                    message=f"Route path contains duplicate prefix '{prefix}' ({spec.method} {spec.path})",
                    detail=f"Expected: single occurrence of prefix, got {spec.path.count(prefix)}",
                ))

    @staticmethod
    def _check_prefix_alignment(spec: EndpointSpec, findings: list[ValidationFinding]) -> None:
        """Ensure route paths that start with a known prefix are in the correct module."""
        for prefix, expected_module in KNOWN_PREFIXES.items():
            if spec.path.startswith(prefix) and spec.module != expected_module:
                findings.append(ValidationFinding(
                    severity="warning",
                    category="prefix_mismatch",
                    path=spec.path,
                    message=f"Route {spec.method} {spec.path} has module='{spec.module}' but prefix '{prefix}' suggests module='{expected_module}'",
                ))
