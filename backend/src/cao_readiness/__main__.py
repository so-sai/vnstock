"""
CAO Readiness Gate — CLI Entry Point
Usage:
    python -m src.cao_readiness
    python -m src.cao_readiness --json   (machine-readable output)
"""

import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.cao_readiness.orchestrator import print_verdict, run_readiness_check


def main():
    [a for a in sys.argv[1:] if not a.startswith("-")]
    json_output = "--json" in sys.argv
    quiet = "--quiet" in sys.argv
    verdict = run_readiness_check(verbose=not quiet)
    if json_output:
        print_verdict(verdict)
    sys.exit(0 if verdict.overall_pass else 1)


if __name__ == "__main__":
    main()
