"""FastAPI route scanner — introspects ``app.routes`` to produce ``EndpointSpec`` list."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from src.core.cagl.models import EndpointSpec


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


# Fast-precedence prefix list for module inference.
KNOWN_PREFIXES = [
    "/api/v1/flow",
    "/api/v1/gold",
    "/api/v1/market-state",
    "/api/v1/holdings",
    "/api/v1/telemetry",
    "/api/v1/weekly",
    "/api/macro",
    "/api/screener",
    "/api/models",
    "/api/breadth",
    "/api/portfolio",
    "/api/backtest",
    "/api/xray",
    "/api/replay",
    "/api/intelligence",
    "/api/watchlist",
]


def _infer_module(path: str, endpoint_module: str) -> str:
    """Infer the logical module name from path prefix or endpoint module path."""
    for prefix in sorted(KNOWN_PREFIXES, key=len, reverse=True):
        if path.startswith(prefix):
            name = prefix.strip("/").replace("/", "_").replace("api_v1_", "").replace("api_", "")
            return name.replace("-", "_")
    if endpoint_module and "routes." in endpoint_module:
        return endpoint_module.rsplit(".", 1)[-1]
    parts = path.strip("/").split("/")
    if parts and parts[0] == "api":
        return parts[1] if len(parts) > 1 else "unknown"
    return "unknown"


def _extract_version(path: str) -> str:
    """Extract version from path like ``/api/v1/...`` → ``v1``."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[1].startswith("v") and parts[1][1:].isdigit():
        return parts[1]
    return ""


class RouteScanner:
    """Introspect a running FastAPI application and extract all endpoints."""

    def scan(self, app: FastAPI) -> list[EndpointSpec]:
        """Iterate ``app.routes``, yield one ``EndpointSpec`` per method."""
        specs: list[EndpointSpec] = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            path = route.path
            mod_name = _infer_module(path, getattr(route.endpoint, "__module__", ""))
            handler_name = getattr(route.endpoint, "__name__", "<unknown>")
            version = _extract_version(path)
            for method in route.methods:
                specs.append(
                    EndpointSpec(
                        path=path,
                        method=method,
                        module=mod_name,
                        handler=handler_name,
                        tags=list(route.tags),
                        version=version,
                    )
                )
        return specs
