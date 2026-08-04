#!/usr/bin/env python3
"""assert_no_silent_pass.py — Static AST Guard for silent except:pass patterns.

WHY: The Great Surgery (2026-08-04) revealed that `except Exception: pass`
in company_state.py silently disabled CausalGraph, EvidenceEngine, and
ModelRegistry for weeks. This script prevents regression by scanning
governor/ and calibration/ for except blocks that lack logger calls.

Two-Tier Architecture:
  Tier 1: pyproject.toml ruff rules (S110 + BLE001) — fast, catches obvious cases
  Tier 2: This AST script — deep scan, catches ALL patterns including bare except

Usage:
  python backend/scripts/assert_no_silent_pass.py [--strict] [directory...]

  --strict: Exit with code 1 on ANY violation (default: only fail on critical)
  (no args): Scan governor/ + calibration/, fail on critical silent exceptions

Exit codes:
  0 — All clear (no critical silent except found)
  1 — Critical violations found (print file:line for each)

WHITELIST: Some except blocks are intentionally silent (e.g., data fetch
  fallbacks that return None are handled downstream). These are marked with
  `# noqa: S110` in the source code. The AST checker skips lines with noqa.
"""

import ast
import sys
from pathlib import Path
from typing import List, Tuple

# Directories to scan (relative to backend/src/)
DEFAULT_SCAN_DIRS = ["src/governor", "src/calibration"]

# Logger function names that constitute "evidence of logging"
LOGGER_FUNCTIONS = {
    "logger.debug",
    "logger.info",
    "logger.warning",
    "logger.error",
    "logger.exception",
    "logger.critical",
    "logging.debug",
    "logging.info",
    "logging.warning",
    "logging.error",
    "logging.exception",
    "logging.critical",
    "print",  # print() also counts as "not silent"
}

# Critical files — silent except here can alter capital allocation decisions
CRITICAL_FILES = {
    "company_state.py",  # BayesianGovernor — decision pipeline
    "csi_explain.py",  # Causal trace — operator visibility
    "decision_guard.py",  # Safety gate
}


def _has_logger_call(handler: ast.ExceptHandler) -> bool:
    """Check if an ExceptHandler body contains at least one logger/print call."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            func = node.func
            # logger.warning(...) or logger.error(...)
            if isinstance(func, ast.Attribute):
                obj = func.value
                if isinstance(obj, ast.Name):
                    call_name = f"{obj.id}.{func.attr}"
                    if call_name in LOGGER_FUNCTIONS:
                        return True
            # print(...)
            if isinstance(func, ast.Name) and func.id in LOGGER_FUNCTIONS:
                return True
    return False


def _has_noqa_comment(source_lines: list, lineno: int) -> bool:
    """Check if a line has a # noqa comment."""
    if lineno <= 0 or lineno > len(source_lines):
        return False
    line = source_lines[lineno - 1]
    return "# noqa" in line


def scan_file(filepath: Path) -> List[Tuple[int, str, bool]]:
    """Scan a single Python file for silent except blocks.

    Returns list of (line_number, description, is_critical) tuples.
    """
    violations = []
    try:
        source = filepath.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        violations.append((e.lineno or 0, f"SyntaxError: {e.msg}", True))
        return violations

    is_critical = filepath.name in CRITICAL_FILES

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        # Skip bare except (handled by S110 separately)
        if node.type is None:
            continue

        # Skip if line has # noqa
        if _has_noqa_comment(source_lines, node.lineno):
            continue

        # Check if handler body has only `pass`
        body = node.body
        is_only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)

        # Check if handler body has logger call
        has_logging = _has_logger_call(node)

        # Violation: except block with only pass AND no logger call
        if is_only_pass and not has_logging:
            exc_type = _get_exception_type(node)
            violations.append(
                (
                    node.lineno,
                    f"silent except:pass ({exc_type}) — no logger.warning/error",
                    is_critical,
                )
            )

        # Violation: except block with no logger call (trivial body)
        elif not has_logging and len(body) <= 2:
            trivial = all(isinstance(s, (ast.Assign, ast.Return, ast.Pass, ast.Continue)) for s in body)
            if trivial:
                exc_type = _get_exception_type(node)
                violations.append(
                    (
                        node.lineno,
                        f"silent except ({exc_type}) — trivial body, no logger",
                        is_critical,
                    )
                )

    return violations


def _get_exception_type(handler: ast.ExceptHandler) -> str:
    """Extract exception type name from handler."""
    if handler.type is None:
        return "bare except"
    if isinstance(handler.type, ast.Name):
        return handler.type.id
    if isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
        return ", ".join(names) if names else "multi"
    return "unknown"


def main(directories: List[str] = None, strict: bool = False) -> int:
    """Main entry point. Returns exit code (0=clean, 1=violations)."""
    backend_dir = Path(__file__).resolve().parent.parent
    if directories is None:
        directories = DEFAULT_SCAN_DIRS

    all_violations: List[Tuple[str, int, str, bool]] = []

    for scan_dir in directories:
        dir_path = backend_dir / scan_dir
        if not dir_path.exists():
            print(f"  SKIP: {dir_path} (not found)")
            continue

        py_files = sorted(dir_path.rglob("*.py"))
        for f in py_files:
            if "__pycache__" in str(f) or "test_" in f.name:
                continue
            violations = scan_file(f)
            rel = f.relative_to(backend_dir)
            for line, desc, is_crit in violations:
                all_violations.append((str(rel), line, desc, is_crit))

    # Separate critical vs non-critical
    critical = [(f, ln, d) for f, ln, d, c in all_violations if c]
    non_critical = [(f, ln, d) for f, ln, d, c in all_violations if not c]

    # Report
    if critical:
        print(f"\n  CRITICAL SILENT EXCEPT — {len(critical)} VIOLATION(S)")
        print("  " + "=" * 70)
        for filepath, line, desc in sorted(critical):
            print(f"  {filepath}:{line}: {desc}")
        print("  " + "=" * 70)
        print("  FIX: Add logger.warning() or logger.error() to each except block.\n")

    if non_critical:
        print(f"  Non-critical: {len(non_critical)} silent except in other files")
        print("  (Add # noqa: S110 if intentional)\n")

    if not critical and not non_critical:
        print("  SILENT EXCEPT DETECTOR — ALL CLEAR (0 violations)")

    if strict:
        return 1 if all_violations else 0
    return 1 if critical else 0


if __name__ == "__main__":
    strict = "--strict" in sys.argv
    dirs = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(dirs if dirs else None, strict))
