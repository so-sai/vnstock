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
    """Return Python files under backend/src/ that are staged.

    WHY scope to backend/src (2026-08-07): production code only. Tests/
    and build scripts carry pre-existing lint debt out of the Error
    Discipline refactor scope; CI lint (`ruff check backend/src`) aligns.
    """
    raw = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=str(REPO_ROOT),
    )
    files = [f.strip() for f in raw.decode().splitlines() if f.strip()]
    return [f for f in files if f.startswith("backend/src") and f.endswith(".py")]


def _re_stage_touched(files: list[str]):
    """Re-stage files that ruff format/--fix may have modified."""
    subprocess.run(["git", "add"] + files, cwd=str(REPO_ROOT))


def main() -> int:
    files = _staged_backend_files()
    if not files:
        return 0

    # Phase 1: normalize (safe mutations)
    print(f"[pre-commit] ruff format on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "format", "--config", "backend/pyproject.toml", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("[pre-commit] ruff format FAILED.")
        return 2
    _re_stage_touched(files)

    print("[pre-commit] ruff auto-fix on", len(files), "staged file(s)...")
    proc = subprocess.run(
        [ruff, "check", "--config", "backend/pyproject.toml", "--fix", *files],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return 2
    _re_stage_touched(files)

    # WHY (2026-08-07): dọn noqa thừa (RUF100) trên STAGED files.
    #   QUAN TRỌNG: KHÔNG dùng `--select RUF100` — dưới select hạn chế, ruff
    #   xem MỌI noqa không phải RUF100 là "unused" và strip sạch cả noqa
    #   BLE001/S110/E501 chủ đích → phá vỡ Error Discipline refactor.
    #   Chạy với full config: ruff chỉ xoá noqa thật-sự-thừa trong ngữ cảnh
    #   đầy đủ rule set, bảo toàn noqa intentional.
    print("[pre-commit] ruff auto-fix (unused noqa) on", len(files), "staged file(s)...")
    proc = subprocess.run(
        [ruff, "check", "--config", "backend/pyproject.toml", "--fix", *files],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return 2
    _re_stage_touched(files)

    # Phase 2: blocking check on the (auto-fixed) staged subset
    print(f"[pre-commit] ruff check on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "check", "--config", "backend/pyproject.toml", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("\n[BLOCKED] COMMIT BLOCKED by ruff. Fix remaining violations, then re-stage:")
        print("   ruff check --fix <files>")
        return 1
    print("[pre-commit] ruff: all staged backend files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
