"""Canonical API registry — single source of truth for expected endpoints."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from src.core.cagl.models import EndpointSpec, ValidationFinding


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


class APIRegistry:
    """Stores expected endpoint specs and supports diff against runtime.

    The registry can be populated:
      - manually (canonical definition)
      - from a JSON snapshot
      - from a ``RouteScanner`` result (adoption mode)
    """

    def __init__(self, routes: Optional[list[EndpointSpec]] = None):
        self._routes: dict[str, EndpointSpec] = {}
        if routes:
            for r in routes:
                self.add(r)

    def add(self, spec: EndpointSpec) -> None:
        key = f"{spec.method}:{spec.path}"
        self._routes[key] = spec

    def get(self, method: str, path: str) -> Optional[EndpointSpec]:
        return self._routes.get(f"{method}:{path}")

    def all(self) -> list[EndpointSpec]:
        return list(self._routes.values())

    def keys(self) -> set[str]:
        return set(self._routes.keys())

    def size(self) -> int:
        return len(self._routes)

    def diff(self, runtime: list[EndpointSpec]) -> list[ValidationFinding]:
        """Compare registry against a runtime scan.

        Returns findings for:
          - routes in registry but missing at runtime  (``missing``)
          - routes at runtime but not in registry      (``unregistered``)
        """
        runtime_keys = {f"{r.method}:{r.path}" for r in runtime}
        registry_keys = self.keys()
        findings: list[ValidationFinding] = []

        for key in registry_keys - runtime_keys:
            spec = self._routes[key]
            findings.append(ValidationFinding(
                severity="error",
                category="missing",
                path=spec.path,
                message=f"Expected endpoint {spec.method} {spec.path} ({spec.module}) not found at runtime",
            ))

        for key in runtime_keys - registry_keys:
            spec = next(r for r in runtime if f"{r.method}:{r.path}" == key)
            findings.append(ValidationFinding(
                severity="warning",
                category="unregistered",
                path=spec.path,
                message=f"Runtime endpoint {spec.method} {spec.path} ({spec.module}) not declared in registry",
            ))

        return findings

    def save_json(self, path: Path) -> None:
        data = [r.to_dict() for r in self._routes.values()]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Registry saved (%d routes) → %s", len(data), path)

    @classmethod
    def load_json(cls, path: Path) -> APIRegistry:
        data = json.loads(path.read_text(encoding="utf-8"))
        routes = [EndpointSpec.from_dict(d) for d in data]
        logger.info("Registry loaded (%d routes) ← %s", len(routes), path)
        return cls(routes)
