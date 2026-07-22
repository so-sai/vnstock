"""Optional ASGI middleware for runtime route validation."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


class CAGLMiddleware(BaseHTTPMiddleware):
    """Check every incoming request against the canonical registry.

    Modes:
      - ``"shadow"`` — observe only, no logging
      - ``"warn"``   — log mismatches as warnings (default)
      - ``"strict"`` — return 400 if route not in registry
    """

    def __init__(self, app, registry: APIRegistry, mode: str = "warn"):
        super().__init__(app)
        self.registry = registry
        self.mode = mode

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        method = request.method
        path = request.url.path
        key = f"{method}:{path}"
        spec = self.registry.get(method, path)

        if spec is None and self.mode != "shadow":
            msg = f"Route {method} {path} not found in canonical registry"
            if self.mode == "strict":
                logger.error(msg)
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=400,
                    content={"error": "route_not_registered", "detail": msg},
                )
            logger.warning(msg)

        return await call_next(request)
