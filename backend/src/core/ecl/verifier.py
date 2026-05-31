"""ECL verifier — execution contract hash + dependency lock."""

import hashlib
import sys
from pathlib import Path

EXECUTION_CONTRACT_VERSION = "ECL_v1.0.0"

EXECUTION_CORE_PATHS = [
    "backend/src/portfolio/decision_tensor.py",
    "backend/src/portfolio/decision_tensor_v2.py",
    "backend/src/portfolio/decision_fusion.py",
    "backend/src/portfolio/exposure_engine.py",
    "backend/src/portfolio/memory_engine.py",
    "backend/src/portfolio/position_sizer.py",
    "backend/src/portfolio/risk_budget.py",
    "backend/src/portfolio/risk_governor_engine.py",
    "backend/src/engine/regime_engine.py",
    "backend/src/engine/rsi_regime_engine.py",
    "backend/src/engine/breadth_engine.py",
    "backend/src/engine/rs_ranker.py",
    "backend/src/engine/screener_logic.py",
    "backend/src/engine/meanrev_engine.py",
    "backend/src/engine/liquidity_wave.py",
    "backend/src/engine/sector_rotation_graph.py",
    "backend/src/engine/breakout_continuation.py",
    "backend/src/engine/flow_decay_engine.py",
    "backend/src/engine/unit_normalizer.py",
    "backend/src/core/data_quality/",
    "backend/src/cao_validation/",
    "core/macro/gold_regime_engine.py",
    "core/macro/gold_spread_engine.py",
    "core/macro/gold_provenance.py",
    "core/holdings/",
    "core/guard/",
    "core/signal_provenance/",
    "core/presentation/",
]


def _find_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path.cwd()


def compute_execution_hash(project_root: Path | None = None) -> str:
    """SHA-256 digest of every file in the execution core."""
    if project_root is None:
        project_root = _find_project_root()
    hasher = hashlib.sha256()
    hasher.update(EXECUTION_CONTRACT_VERSION.encode())
    for rel_path in sorted(EXECUTION_CORE_PATHS):
        full = project_root / rel_path
        if full.is_dir():
            for f in sorted(full.rglob("*.py")):
                if f.is_file():
                    hasher.update(f.read_bytes())
        elif full.exists():
            hasher.update(full.read_bytes())
    return hasher.hexdigest()[:32]


def verify_dependency_lock() -> bool:
    """Verify that pinned dependencies match expected versions."""
    expected = {
        "pandas": "2.3.3",
    }
    try:
        import pandas as pd
        v = pd.__version__.split("+")[0]
        if v != expected["pandas"]:
            print(f"[ECL] Pandas version mismatch: {v} != {expected['pandas']}")
            return False
    except ImportError:
        print("[ECL] Pandas not found")
        return False
    return True


def ecl_self_check() -> dict:
    """Run full ECL self-check. Returns dict with pass/fail per check."""
    root = _find_project_root()
    build_hash_file = root / "backend" / "src" / "core" / "ecl" / ".build_hash"
    stored = ""
    if build_hash_file.exists():
        stored = build_hash_file.read_text().strip()

    computed = compute_execution_hash(root)
    dep_ok = verify_dependency_lock()
    hash_ok = not stored or stored == computed

    return {
        "version": EXECUTION_CONTRACT_VERSION,
        "status": "INTACT" if (dep_ok and hash_ok) else "BROKEN",
        "stored_hash": stored,
        "computed_hash": computed,
        "hash_match": hash_ok,
        "dependency_lock": dep_ok,
        "project_root": str(root),
    }
