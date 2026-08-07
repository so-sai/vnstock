# -*- coding: utf-8 -*-
"""pre_commit_hook.py - Git pre-commit hook: lint staged backend files.

Version-controlled so the gate is reproducible. Installed into
.git/hooks/pre-commit by ptck.py hooks-install, which delegates here.
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ruff = shutil.which("ruff") or "ruff"


def _staged_backend_files() -> list[str]:
    """Return Python files under backend/ that are staged."""
    raw = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=str(REPO_ROOT),
    )
    files = [f.strip() for f in raw.decode().splitlines() if f.strip()]
    return [f for f in files if f.startswith("backend") and f.endswith(".py")]


def _re_stage_touched(files: list[str]):
    """Re-stage files that ruff format/--fix may have modified."""
    subprocess.run(["git", "add"] + files, cwd=str(REPO_ROOT))


def main() -> int:
    files = _staged_backend_files()
    if not files:
        return 0

    # Phase 1: normalize (safe mutations)
    print(f"[pre-commit] ruff format on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "format", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("[pre-commit] ruff format FAILED.")
        return 2
    _re_stage_touched(files)

    print("[pre-commit] ruff auto-fix (F401,F841) on", len(files), "staged file(s)...")
    proc = subprocess.run([ruff, "check", "--fix", "--select", "F401,F841", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        return 2
    _re_stage_touched(files)

    # WHY (2026-08-07): tự động dọn RUF100 (noqa thừa) trên STAGED files —
    #   rule AN TOÀN 100% auto-fix, dọn DẦN theo commit, không gây merge
    #   conflict ồ ạt. KHÔNG auto-fix UP035 (deprecated typing → builtin generics):
    #   ruff xem là unsafe-fix và không sửa được import/hint đồng bộ → sửa tay.
    print("[pre-commit] ruff auto-fix (RUF100) on", len(files), "staged file(s)...")
    proc = subprocess.run(
        [ruff, "check", "--fix", "--select", "RUF100", *files],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return 2
    _re_stage_touched(files)

    # Phase 2: blocking check on the (auto-fixed) staged subset
    print(f"[pre-commit] ruff check on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "check", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("\n[BLOCKED] COMMIT BLOCKED by ruff. Fix remaining violations, then re-stage:")
        print("   ruff check --fix <files>")
        return 1
    print("[pre-commit] ruff: all staged backend files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
