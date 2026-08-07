"""
Gate C — Regime Stability Index
================================
Measures regime entropy over a rolling window.
High entropy → regime boundaries are noisy → CAO posterior estimation would
learn incorrect distribution shapes → run CAO in "soft mode" only.

Uses two data sources (prefers regime_history table, falls back to snapshots).
"""

import logging
import math
import sys
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


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
from src.cao_readiness.models import GateResult, RegimeEntropyPoint, RegimeStabilityReport
from src.database.db_core import get_connection
from src.telemetry.storage import get_all_snapshots

REGIME_LABELS = ["TRENDING", "RANGING", "CRISIS"]
DEFAULT_WINDOW = 30
ENTROPY_THRESHOLD = 0.50  # H > 0.50 → high entropy → CAO needs soft mode
STABILITY_THRESHOLD = 0.60


def _shannon_entropy(labels: list[str]) -> float:
    if not labels:
        return 0.0
    n = len(labels)
    counts = Counter(labels)
    h = 0.0
    for c in counts.values():
        p = c / n
        if p > 0:
            h -= p * math.log2(p)
    return round(h, 4)


def _normalized_entropy(labels: list[str]) -> float:
    h = _shannon_entropy(labels)
    n_classes = len(REGIME_LABELS)
    if n_classes <= 1:
        return 0.0
    h_max = math.log2(n_classes)
    return round(h / h_max, 4) if h_max > 0 else 0.0


def _load_regime_from_history(window_days: int = DEFAULT_WINDOW) -> list[dict]:
    try:
        with get_connection() as conn:
            conn.row_factory = None
            rows = conn.execute(
                "SELECT date, status, regime_score FROM regime_history WHERE date >= date('now', ?) ORDER BY date ASC",
                (f"-{window_days} days",),
            ).fetchall()
            result = [{"date": r[0], "status": r[1], "regime_score": r[2]} for r in rows if len(r) >= 3]
            if len(result) >= 3:
                return result
            fallback = conn.execute(
                "SELECT date, status, regime_score FROM regime_history ORDER BY date DESC LIMIT ?", (max(30, window_days),)
            ).fetchall()
            fallback_result = [{"date": r[0], "status": r[1], "regime_score": r[2]} for r in fallback if len(r) >= 3]
            fallback_result.reverse()
            if len(fallback_result) >= 3:
                logger.info(
                    "[GATE_C] date window (%dd) returned %d rows, using last %d rows instead",
                    window_days,
                    len(result),
                    len(fallback_result),
                )
                return fallback_result
            return result
    except Exception as e:
        logger.warning("[GATE_C] regime_history table not available: %s", e)
        return []


def _load_regime_from_snapshots(limit: int = 200) -> list[dict]:
    snapshots = get_all_snapshots(limit=limit)
    results = []
    for snap in snapshots:
        regime = snap.get("market_regime")
        if regime:
            results.append(
                {
                    "date": snap.get("timestamp", ""),
                    "status": regime,
                    "regime_score": 0.5,
                }
            )
    results.reverse()
    return results


def run_regime_stability_test(
    window: int = DEFAULT_WINDOW,
) -> RegimeStabilityReport:
    records = _load_regime_from_history(window)
    if not records:
        records = _load_regime_from_snapshots()
    if not records:
        return RegimeStabilityReport(
            passed=False,
            entropy_series=[],
            current_entropy=1.0,
            max_entropy=1.0,
            stability_index=0.0,
            regime_transition_count=0,
            window_days=window,
            verdict="NO_DATA: no regime history available for stability analysis",
        )
    records.sort(key=lambda r: r.get("date", ""))
    entropy_points = []
    all_regimes = [r["status"] for r in records if r.get("status")]
    if len(all_regimes) < 3:
        return RegimeStabilityReport(
            passed=False,
            entropy_series=[],
            current_entropy=0.5,
            max_entropy=0.5,
            stability_index=0.5,
            regime_transition_count=0,
            window_days=window,
            verdict="INSUFFICIENT_DATA: need at least 3 regime readings",
        )
    min_window = min(window, len(all_regimes) // 2)
    for i in range(min_window, len(all_regimes) + 1):
        chunk = all_regimes[i - min_window : i]
        h_norm = _normalized_entropy(chunk)
        entropy_points.append(
            RegimeEntropyPoint(
                date=records[i - 1].get("date", str(i)),
                regime=all_regimes[i - 1],
                regime_score=records[i - 1].get("regime_score", 0.5),
                entropy=h_norm,
            )
        )
    current_h = entropy_points[-1].entropy if entropy_points else 0.0
    max_h = max(p.entropy for p in entropy_points) if entropy_points else 0.0
    transitions = sum(1 for i in range(1, len(all_regimes)) if all_regimes[i] != all_regimes[i - 1])
    stability = 1.0 - current_h
    passed = stability >= STABILITY_THRESHOLD and current_h < ENTROPY_THRESHOLD
    if passed:
        verdict = (
            f"Regime stable over window={min_window}. "
            f"current_entropy(H)={current_h:.3f} < {ENTROPY_THRESHOLD}, "
            f"stability={stability:.3f} >= {STABILITY_THRESHOLD}. "
            f"CAO Bayesian core is safe."
        )
    elif stability >= 0.40:
        verdict = (
            f"WARN: moderate regime instability. "
            f"current_entropy(H)={current_h:.3f}, stability={stability:.3f}. "
            f"CAO should run in soft mode (Bayesian smoothing only)."
        )
    else:
        verdict = (
            f"FAIL: high regime entropy. "
            f"current_entropy(H)={current_h:.3f} >= {ENTROPY_THRESHOLD}, "
            f"stability={stability:.3f} < {STABILITY_THRESHOLD}. "
            f"CAO posterior estimation would learn incorrect distribution shape. "
            f"Defer Phase 1 until regime stabilizes."
        )
    logger.info(
        "[GATE_C] %s | H=%.3f | stability=%.3f | transitions=%d",
        "PASS" if passed else "FAIL",
        current_h,
        stability,
        transitions,
    )
    return RegimeStabilityReport(
        passed=passed,
        entropy_series=entropy_points,
        current_entropy=current_h,
        max_entropy=max_h,
        stability_index=round(stability, 4),
        regime_transition_count=transitions,
        window_days=min_window,
        verdict=verdict,
    )


def gate_c_check() -> GateResult:
    report = run_regime_stability_test()
    if report.passed:
        status = "PASS"
    elif report.stability_index >= 0.40:
        status = "WARN"
    else:
        status = "FAIL"
    return GateResult(
        gate_name="C — Regime Stability Index",
        status=status,
        score=report.stability_index,
        threshold=STABILITY_THRESHOLD,
        message=report.verdict,
        details={
            "current_entropy": report.current_entropy,
            "max_entropy": report.max_entropy,
            "stability_index": report.stability_index,
            "regime_transitions": report.regime_transition_count,
            "window_days": report.window_days,
            "regime_distribution": dict(Counter(p.regime for p in report.entropy_series)) if report.entropy_series else {},
        },
    )
