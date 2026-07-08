"""Paper Context — Storage Sandboxing cho Phase 5 UAT.

Cô lập hoàn toàn mọi đường dẫn persistent:
  system_state.json → system_state_paper.json
  decision_audit.jsonl → decision_audit_paper.jsonl
  params_registry.json → params_registry_paper.json

Gọi set_paper_mode(True) trước khi chạy Paper Trading.
Gọi set_paper_mode(False) để khôi phục production.
"""

from pathlib import Path


def _resolve_project_root() -> Path:
    """Tìm project root bằng AGENTS.md anchor."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return current


PROJECT_ROOT = _resolve_project_root()
_DATA = PROJECT_ROOT / "backend" / "data"

_PAPER = {
    "system_state": _DATA / "system_state_paper.json",
    "decision_audit": _DATA / "decision_audit_paper.jsonl",
    "params_registry": _DATA / "params_registry_paper.json",
}

_PRODUCTION = {
    "system_state": _DATA / "system_state.json",
    "decision_audit": _DATA / "decision_audit.jsonl",
    "params_registry": _DATA / "params_registry.json",
}

_ACTIVE = False


def is_paper_mode() -> bool:
    return _ACTIVE


def set_paper_mode(enabled: bool = True):
    global _ACTIVE
    targets = _PAPER if enabled else _PRODUCTION
    _ACTIVE = enabled

    # Override system_state.py path
    from src.portfolio import system_state
    system_state.set_lock_path(targets["system_state"])

    # Override decision_audit.py path
    from src.portfolio import decision_audit
    decision_audit.set_audit_path(targets["decision_audit"])

    # Override params_registry.py paths
    from src.portfolio import params_registry
    params_registry.set_registry_path(targets["params_registry"])
    params_registry.set_audit_path(targets["decision_audit"])


class PaperSession:
    """Context manager — tự động set/reset paper paths."""

    def __enter__(self):
        set_paper_mode(True)
        return self

    def __exit__(self, *args):
        set_paper_mode(False)
