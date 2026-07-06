from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


@dataclass
class EndpointSpec:
    """Canonical representation of a single API endpoint.

    ``path`` is the full resolved path (prefix + route decorator path).
    ``module`` is the logical module name (e.g. *flow*, *gold*, *macro*).
    """
    path: str
    method: str
    module: str
    handler: str
    tags: list[str] = field(default_factory=list)
    version: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "method": self.method,
            "module": self.module,
            "handler": self.handler,
            "tags": list(self.tags),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EndpointSpec:
        return cls(**d)


@dataclass
class ValidationFinding:
    """A single validation issue discovered during CAGL verification."""
    severity: str       # "error", "warning", "info"
    category: str       # "phantom", "missing", "prefix_dup", "unregistered"
    path: str
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "path": self.path,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class ScanResult:
    """Aggregated result of a CAGL verification pass."""
    runtime_routes: list[EndpointSpec] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    def merge(self, other: ScanResult) -> ScanResult:
        self.runtime_routes.extend(other.runtime_routes)
        self.findings.extend(other.findings)
        return self
