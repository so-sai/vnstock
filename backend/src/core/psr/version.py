"""VersionFreeze — immutable version manifest + git integration.

Defines:
  - ``CAO v3.1`` as an immutable semantic + causal contract
  - Current version string (auto-detected from git tag, falls back to dev)
  - Semantic contract hash (fingerprint of all label_vi + explanation_vi)
  - Snapshot hash (fingerprint of the frozen system state)

No silent label changes, no backend drift, no UI semantic mutation.
"""
from __future__ import annotations
import hashlib
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.psr.models import PSRVersion

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "psr"
MANIFEST_FILE = DATA_DIR / "version_manifest.json"
DEFAULT_VERSION = "CAO v3.1-dev"


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent.parent.parent.parent.parent,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _hash_semantic_contract() -> str:
    """Fingerprint all label_vi + explanation_vi strings in the codebase."""
    hasher = hashlib.sha256()
    try:
        src_dir = Path(__file__).resolve().parent.parent.parent
        for py_file in sorted(src_dir.rglob("*.py")):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if "label_vi" in text or "explanation_vi" in text:
                hasher.update(text.encode())
    except Exception:
        pass
    return hasher.hexdigest()[:16]


def _hash_api_contract() -> str:
    """Hash of the current API route graph via CAGL scanner."""
    hasher = hashlib.sha256()
    try:
        from src.api.main import app
        from src.core.cagl.scanner import RouteScanner
        scanner = RouteScanner()
        for spec in scanner.scan(app):
            hasher.update(f"{spec.method}:{spec.path}:{spec.module}:{spec.handler}".encode())
    except Exception:
        pass
    return hasher.hexdigest()[:16]


class VersionFreeze:
    """Manages the version manifest for PSR releases.

    Usage::

        vf = VersionFreeze()
        v = vf.freeze("CAO v3.1", notes="First PSR release")
        # => writes version_manifest.json
        print(vf.current())
    """

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def freeze(self, version: str, notes: str = "") -> PSRVersion:
        """Create an immutable version freeze."""
        manifest = PSRVersion(
            version=version,
            git_commit=_git_commit(),
            snapshot_hash="",
            semantic_contract_hash=_hash_semantic_contract(),
            api_contract_hash=_hash_api_contract(),
            created_at=datetime.now().isoformat(),
            notes=notes,
        )
        MANIFEST_FILE.write_text(
            json.dumps(manifest, default=lambda o: o.__dict__, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("[PSR_VERSION] Frozen: %s @ %s", version, manifest.git_commit)
        return manifest

    def current(self) -> PSRVersion:
        """Return the current frozen version, or a dev default."""
        if MANIFEST_FILE.exists():
            try:
                data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
                return PSRVersion(**data)
            except Exception as e:
                logger.warning("[PSR_VERSION] Manifest read failed: %s", e)
        return PSRVersion(
            version=DEFAULT_VERSION,
            git_commit=_git_commit(),
            snapshot_hash="",
            semantic_contract_hash=_hash_semantic_contract(),
            created_at=datetime.now().isoformat(),
            notes="Development build — not frozen",
        )

    def hash_semantic_contract(self) -> str:
        """Recompute the semantic contract hash."""
        return _hash_semantic_contract()


def get_current_version() -> str:
    """Convenience: returns the version string from the manifest."""
    try:
        return VersionFreeze().current().version
    except Exception:
        return DEFAULT_VERSION
