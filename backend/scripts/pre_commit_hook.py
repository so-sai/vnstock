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
3. Phase 1 — normalize the staged subset:
   a. `ruff format` (PEP8/Black-style auto-format)
   b. `ruff check --fix` for safe lint fixes (unused imports F401, unused
      variables F841 where a safe fix exists)
   Touched files are re-staged so the commit actually contains the fixes.
4. Phase 2 — full `ruff check` on the staged subset. Remaining violations
   (unsafe/unfixable) block the commit.

Exit codes: 0 = ok, 1 = ruff violations or config error, 2 = no ruff found.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Rules with SAFE auto-fixes: applied silently before the blocking check.
AUTOFIX_SELECT = ("F401", "F841")


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
    return [f.replace("\\", "/") for f in staged if f.startswith("backend/") and f.endswith(".py")]


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
    which = (
        subprocess.run(["where", "ruff"], capture_output=True, text=True)
        if sys.platform == "win32"
        else subprocess.run(["sh", "-lc", "command -v ruff"], capture_output=True, text=True)
    )
    first = (which.stdout or "").strip().splitlines()
    return first[0] if first else None


def _re_stage_touched(files: list[str]) -> int:
    """Re-stage any of `files` ruff modified. Returns count re-staged.

    Only files that were in the original staged set may be touched — never
    re-stage unrelated unstaged work the developer left behind.
    """
    modified = _git(["diff", "--name-only", "--diff-filter=ACMR"])
    touched = [f for f in files if f in modified]
    if touched:
        subprocess.run(["git", "add", "--", *touched], cwd=str(REPO_ROOT), check=True)
        print(f"[pre-commit] ruff auto-fixed + re-staged {len(touched)} file(s).")
    return len(touched)


def main() -> int:
    files = _staged_backend_files()
    if not files:
        print("[pre-commit] ruff: no staged backend .py files — skip.")
        return 0

    ruff = _find_ruff()
    if ruff is None:
        print("[pre-commit] ERROR: ruff not found. Install: pip install ruff")
        return 2

    # ── Phase 1a: ruff format (auto-style to project PEP8 config) ──
    print(f"[pre-commit] ruff format on {len(files)} staged file(s)...")
    fmt = subprocess.run([ruff, "format", *files], cwd=str(REPO_ROOT))
    if fmt.returncode != 0:
        print("[pre-commit] ERROR: ruff format failed.")
        return 2
    _re_stage_touched(files)

    # ── Phase 1b: auto-fix safe violations (F401 unused import, F841 unused var) ──
    print(f"[pre-commit] ruff auto-fix ({','.join(AUTOFIX_SELECT)}) on {len(files)} staged file(s)...")
    fix_proc = subprocess.run(
        [ruff, "check", "--fix", f"--select={','.join(AUTOFIX_SELECT)}", *files],
        cwd=str(REPO_ROOT),
    )
    if fix_proc.returncode not in (0, 1):
        print("[pre-commit] ERROR: ruff auto-fix failed unexpectedly.")
        return 2
    _re_stage_touched(files)

    # ── Phase 2: blocking check on the (auto-fixed) staged subset ──
    print(f"[pre-commit] ruff check on {len(files)} staged file(s)...")
    proc = subprocess.run([ruff, "check", *files], cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print("\n❌ COMMIT BLOCKED by ruff. Fix remaining violations, then re-stage:")
        print("   ruff check --fix <files>")
        return 1
    print("[pre-commit] ruff: all staged backend files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
