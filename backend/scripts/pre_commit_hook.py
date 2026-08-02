"""pre_commit_hook.py — Git pre-commit hook: lint staged backend files with ruff.

Version-controlled so the gate is reproducible. Installed into
`.git/hooks/pre-commit` by `ptck.py hooks-install`, which delegates here.

Behavior
--------
1. Collect staged Python files (`git diff --cached --name-only`).
2. Keep only files under `backend/` — ruff config (line-length=127, py314)
   lives in `backend/pyproject.toml`; root files (ptck.py, scripts/) have no
   ruff config and pre-existing violations, so linting them here would
   produce false positives and block unrelated commits.
3. Run `ruff check` on the staged subset. Violations block the commit.

Exit codes: 0 = ok, 1 = ruff violations or config error, 2 = no ruff found.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(args: list[str]) -> list[str]:
    """Run git, return stdout lines (stripped), raising on failure."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _staged_backend_files() -> list[str]:
    """Staged .py files that live under backend/ (config-covered)."""
    staged = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return [f.replace("\\", "/") for f in staged
            if f.startswith("backend/") and f.endswith(".py")]


def _find_ruff() -> str | None:
    """Locate ruff: repo venv first, then PATH."""
    candidates = [
        REPO_ROOT / "backend" / ".venv" / "Scripts" / "ruff.exe",
        REPO_ROOT / "backend" / ".venv" / "bin" / "ruff",
        REPO_ROOT / "backend" / ".venv" / "Scripts" / "ruff",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    which = subprocess.run(["where", "ruff"], capture_output=True, text=True) \
        if sys.platform == "win32" else subprocess.run(
            ["sh", "-lc", "command -v ruff"], capture_output=True, text=True)
    first = (which.stdout or "").strip().splitlines()
    return first[0] if first else None


def main() -> int:
    files = _staged_backend_files()
    if not files:
        print("[pre-commit] ruff: no staged backend .py files — skip.")
        return 0

    ruff = _find_ruff()
    if ruff is None:
        print("[pre-commit] ERROR: ruff not found. Install: pip install ruff")
        return 2

    print(f"[pre-commit] ruff check on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "check", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("\n❌ COMMIT BLOCKED by ruff. Fix violations above, then re-stage:")
        print("   ruff check --fix <files>  # or python ptck.py hooks-install --fix-all")
        return 1
    print("[pre-commit] ruff: all staged backend files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
