"""
Gate A — Engine Independence Test
==================================
Checks pairwise correlation between engine signals from historical snapshots.
High correlation (|r| > threshold) = multicollinearity → causal attribution ambiguity.
"""

import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None


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
from src.cao_readiness.models import EngineCorrelation, GateResult, IndependenceReport
from src.telemetry.storage import get_all_snapshots

DEFAULT_CORRELATION_THRESHOLD = 0.70
CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]


def _parse_engine_scores(snapshot: dict) -> dict:
    raw = snapshot.get("engine_scores")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError, TypeError:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _load_engine_vectors(snapshots: list, engine_names: list[str] | None = None) -> dict[str, list[float]]:
    if engine_names is None:
        engine_names = CANONICAL_ENGINES
    vectors = defaultdict(list)
    for snap in snapshots:
        scores = _parse_engine_scores(snap)
        if not scores:
            continue
        for eng in engine_names:
            val = scores.get(eng)
            if val is not None and isinstance(val, (int, float)):
                vectors[eng].append(float(val))
    return dict(vectors)


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 3 or len(y) < 3:
        return 0.0
    if len(x) != len(y):
        return 0.0
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y))
    sum_x2 = sum(a * a for a in x)
    sum_y2 = sum(b * b for b in y)
    denom = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
    if denom == 0:
        return 0.0
    r = (n * sum_xy - sum_x * sum_y) / denom
    return max(-1.0, min(1.0, r))


def _compute_condition_number(vectors: dict[str, list[float]], engine_names: list[str]) -> float:
    if np is None or len(engine_names) < 2:
        return float("inf")
    present = [e for e in engine_names if e in vectors and len(vectors[e]) >= 3]
    if len(present) < 2:
        return float("inf")
    min_len = min(len(vectors[e]) for e in present)
    X = np.column_stack([vectors[e][:min_len] for e in present])
    X = np.nan_to_num(X, nan=0.0)
    _, s, _ = np.linalg.svd(X, full_matrices=False)
    valid_s = s[s > 1e-10]
    if len(valid_s) < 2:
        return float("inf")
    return float(valid_s[0] / valid_s[-1])


def run_independence_test(
    snapshots: list[dict] | None = None,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
    engine_names: list[str] | None = None,
) -> IndependenceReport:
    if engine_names is None:
        engine_names = CANONICAL_ENGINES
    if snapshots is None:
        try:
            snapshots = get_all_snapshots(limit=200)
        except ImportError, AttributeError, TypeError, KeyError:
            snapshots = []
    if not snapshots:
        return IndependenceReport(
            passed=False,
            correlation_matrix={},
            flagged_pairs=[],
            max_correlation=0.0,
            condition_number=None,
            verdict="NO_DATA: no decision snapshots available. Record decisions first.",
        )
    vectors = _load_engine_vectors(snapshots, engine_names)
    present_engines = [e for e in engine_names if e in vectors and len(vectors[e]) >= 3]
    flagged_pairs = []
    corr_matrix = {}
    max_corr = 0.0
    for i, ea in enumerate(present_engines):
        for eb in present_engines[i + 1 :]:
            r = _pearson(vectors[ea], vectors[eb])
            corr_matrix[f"{ea}__{eb}"] = round(r, 4)
            flagged = abs(r) > threshold
            if flagged:
                flagged_pairs.append(EngineCorrelation(ea, eb, round(r, 4), flagged))
            if abs(r) > max_corr:
                max_corr = abs(r)
    cond_number = _compute_condition_number(vectors, present_engines)
    passed = len(flagged_pairs) == 0
    if max_corr > 0.85:
        passed = False
    flagged_engines = set()
    for fp in flagged_pairs:
        flagged_engines.add(fp.engine_a)
        flagged_engines.add(fp.engine_b)
    if passed:
        verdict = (
            f"All {len(present_engines)} engines are sufficiently independent. max|r|={max_corr:.3f} < threshold={threshold}"
        )
    elif len(flagged_pairs) <= 2 and len(flagged_engines) <= 2:
        verdict = (
            f"WARN: {len(flagged_pairs)} pair(s) correlated above {threshold}. "
            f"Flagged: {', '.join(sorted(flagged_engines))}. "
            f"Consider orthogonalization before CAO learning."
        )
    else:
        verdict = (
            f"FAIL: {len(flagged_pairs)} pair(s) with |r|>{threshold}. "
            f"Multicollinearity risk. max|r|={max_corr:.3f}. "
            f"CAO attribution would be ambiguous."
        )
    logger.info(
        "[GATE_A] %s | max_corr=%.3f | cond=%.1f | pairs=%d",
        "PASS" if passed else "FAIL",
        max_corr,
        cond_number if cond_number != float("inf") else -1,
        len(flagged_pairs),
    )
    return IndependenceReport(
        passed=passed,
        correlation_matrix=corr_matrix,
        flagged_pairs=flagged_pairs,
        max_correlation=max_corr,
        condition_number=cond_number if cond_number != float("inf") else None,
        verdict=verdict,
    )


def gate_a_check(
    snapshots: list[dict] | None = None,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> GateResult:
    report = run_independence_test(snapshots, threshold)
    if report.passed:
        status = "PASS"
    elif len(report.flagged_pairs) <= 2:
        status = "WARN"
    else:
        status = "FAIL"
    return GateResult(
        gate_name="A — Engine Independence",
        status=status,
        score=1.0 - report.max_correlation,
        threshold=threshold,
        message=report.verdict,
        details={
            "max_correlation": report.max_correlation,
            "flagged_pairs": [{"a": p.engine_a, "b": p.engine_b, "r": p.correlation} for p in report.flagged_pairs],
            "condition_number": report.condition_number,
            "engines_analyzed": list(set(e for p in report.flagged_pairs for e in (p.engine_a, p.engine_b)))
            or "all_uncorrelated",
        },
    )
