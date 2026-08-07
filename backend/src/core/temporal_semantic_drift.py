"""
temporal_semantic_drift.py — Temperal Semantic Drift Index + Early Warning.

Tracks how "meaning" changes across time windows of snapshots.
Not a layer. Not an engine. A lightweight stateful tracker.

Two analysis modes:
  - compute_temporal_drift()   → "đã drift" (reactive, existing)
  - compute_early_warning()    → "sắp drift" (leading, new)

Early warning reads 3 leading indicators from the same history:
  1. drift_slope     — drift_score đang tăng dần
  2. acceleration    — tốc độ tăng đang nhanh hơn
  3. label_density   — mật độ LABEL_MISMATCH trong window
"""

from __future__ import annotations

from datetime import datetime

# ── In-memory window (ephemeral — resets on restart) ────────────────────
# In production this would persist to .kit/local_brain.db or equivalent.

_history: list[dict] = []
MAX_HISTORY = 30  # snapshots kept
WINDOW_SIZE = 5  # consecutive snapshots for trend
MIN_WARNING_HISTORY = 3  # minimum snapshots before early warning is valid


def push_snapshot(result: dict, kernel_drift_score: float | None = None) -> None:
    """Push a semantic consistency result into the history window.

    Args:
        result: output from check_semantic_consistency()
        kernel_drift_score: optional drift_score from drift_prevention.assess_drift()
                            for early warning slope analysis.
    """
    violations = list(result.get("violations", []))
    label_count = sum(1 for v in violations if v.get("type") == "LABEL_MISMATCH")

    _history.append(
        {
            "timestamp": datetime.now().isoformat(),
            "consistency_score": result.get("semantic_consistency_score", 1.0),
            "violations": violations,
            "drift_in_meaning": result.get("drift_in_meaning", False),
            "mismatched_concepts": list(result.get("mismatched_concepts", [])),
            "kernel_drift_score": kernel_drift_score,
            "label_mismatch_count": label_count,
        }
    )
    if len(_history) > MAX_HISTORY:
        _history.pop(0)


def clear_history() -> None:
    _history.clear()


def get_history() -> list[dict]:
    return list(_history)


# ── Signal 1: DRIVER_SHIFT ─────────────────────────────────────────────


def _driver_shift() -> dict | None:
    """Check if the kernel's dominant driver has changed across the window.

    Returns shift info if a change is detected, None otherwise.
    """
    if len(_history) < 2:
        return None

    # We look at violation detail strings for CAUSAL_MISMATCH patterns
    # that indicate a persistent driver mismatch across multiple snapshots.
    recent = _history[-WINDOW_SIZE:]
    causal_count = 0
    for entry in recent:
        for v in entry.get("violations", []):
            if v.get("type") == "CAUSAL_MISMATCH":
                causal_count += 1

    if causal_count >= 2:
        return {
            "signal": "DRIVER_SHIFT",
            "severity": 0.6,
            "detail": (
                f"phát hiện CAUSAL_MISMATCH ở {causal_count}/{len(recent)} snapshot gần nhất "
                f"— narrative không theo kịp sự thay đổi driver"
            ),
            "snapshots_affected": causal_count,
        }
    return None


# ── Signal 2: TREND_DRIFT ──────────────────────────────────────────────


def _trend_drift() -> dict | None:
    """Check if consistency score is trending downward over the window.

    Also checks if kernel drift_score is trending upward.
    """
    if len(_history) < WINDOW_SIZE:
        return None

    window = _history[-WINDOW_SIZE:]
    scores = [e["consistency_score"] for e in window]

    # Linear trend: if first half avg > second half avg → degrading
    mid = len(scores) // 2
    first_half = sum(scores[:mid]) / mid if mid > 0 else 1.0
    second_half = sum(scores[mid:]) / (len(scores) - mid) if (len(scores) - mid) > 0 else 1.0

    if first_half > second_half + 0.15:
        return {
            "signal": "TREND_DRIFT",
            "severity": 0.5,
            "detail": (
                f"consistency_score giảm từ {first_half:.2f} → {second_half:.2f} trong {WINDOW_SIZE} snapshot gần nhất"
            ),
            "first_half_avg": round(first_half, 4),
            "second_half_avg": round(second_half, 4),
        }
    return None


# ── Signal 3: NOVELTY_ALERT ────────────────────────────────────────────


def _novelty_alert() -> dict | None:
    """Check if new violation types have appeared that never existed before."""
    if len(_history) < 2:
        return None

    older = _history[:-1]
    current = _history[-1]
    current_violations = current.get("violations", [])

    # Collect all previously seen violation types
    seen_types = set()
    for entry in older:
        for v in entry.get("violations", []):
            seen_types.add(v.get("type", ""))

    # Check if current has any type never seen before
    novel = [v for v in current_violations if v.get("type", "") not in seen_types]
    if novel:
        novel_types = list({v["type"] for v in novel})
        return {
            "signal": "NOVELTY_ALERT",
            "severity": 0.4,
            "detail": f"loại violation mới xuất hiện: {', '.join(novel_types)}",
            "novel_types": novel_types,
        }
    return None


# ── Main entry ─────────────────────────────────────────────────────────


def compute_temporal_drift() -> dict:
    """Compute the Temporal Semantic Drift Index over the history window.

    Returns:
        {
            "temporal_drift_score": 0.0 - 1.0,
            "signals": [],
            "meaning_is_drifting": False,
            "snapshots_analyzed": int,
            "time_window_hours": float,
        }
    """
    if len(_history) < 2:
        return {
            "temporal_drift_score": 0.0,
            "signals": [],
            "meaning_is_drifting": False,
            "snapshots_analyzed": len(_history),
            "time_window_hours": 0.0,
        }

    signals: list[dict] = []

    s1 = _driver_shift()
    if s1:
        signals.append(s1)

    s2 = _trend_drift()
    if s2:
        signals.append(s2)

    s3 = _novelty_alert()
    if s3:
        signals.append(s3)

    # Score: weighted by signal severity, capped at 1.0
    if not signals:
        score = 0.0
    else:
        total = sum(s["severity"] for s in signals)
        score = min(1.0, total)

    # Meaning is drifting if any signal ≥ 0.5
    meaning_is_drifting = any(s["severity"] >= 0.5 for s in signals)

    # Time window
    try:
        t0 = datetime.fromisoformat(_history[0]["timestamp"])
        t1 = datetime.fromisoformat(_history[-1]["timestamp"])
        hours = (t1 - t0).total_seconds() / 3600
    except Exception:
        hours = 0.0

    return {
        "temporal_drift_score": round(score, 4),
        "signals": signals,
        "meaning_is_drifting": meaning_is_drifting,
        "snapshots_analyzed": len(_history),
        "time_window_hours": round(hours, 2),
    }


# ====================================================================
# EARLY WARNING — Leading indicators (predictive, not reactive)
# ====================================================================


def _safe_slope(values: list[float]) -> float:
    """Simple linear slope over values using least-squares.

    Returns slope per step. Positive = rising, negative = falling.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def _drift_slope() -> dict | None:
    """Signal 1: Is kernel drift_score trending upward?"""
    scores = [e["kernel_drift_score"] for e in _history[-WINDOW_SIZE:] if e["kernel_drift_score"] is not None]
    if len(scores) < MIN_WARNING_HISTORY:
        return None

    slope = _safe_slope(scores)
    if slope > 0.03:
        return {
            "indicator": "DRIFT_SLOPE",
            "value": round(slope, 4),
            "detail": f"drift_score đang tăng {slope:+.4f}/step trong {len(scores)} snapshot gần nhất",
        }
    return None


def _acceleration() -> dict | None:
    """Signal 2: Is the drift acceleration positive? (slope of the slope)"""
    scores = [e["kernel_drift_score"] for e in _history[-WINDOW_SIZE:] if e["kernel_drift_score"] is not None]
    if len(scores) < MIN_WARNING_HISTORY + 1:
        return None

    # First differences (velocity)
    diffs = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
    # Slope of differences (acceleration)
    accel = _safe_slope(diffs)
    if accel > 0.02:
        return {
            "indicator": "ACCELERATION",
            "value": round(accel, 4),
            "detail": f"tốc độ drift đang tăng nhanh (accel={accel:+.4f}) — nguy cơ sắp mất kiểm soát",
        }
    return None


def _label_density() -> dict | None:
    """Signal 3: Is LABEL_MISMATCH density rising in the window?"""
    recent = _history[-WINDOW_SIZE:]
    total = len(recent)
    if total < MIN_WARNING_HISTORY:
        return None

    label_counts = [e.get("label_mismatch_count", 0) for e in recent]
    density = sum(label_counts) / total

    if density >= 0.6:
        return {
            "indicator": "LABEL_DENSITY",
            "value": round(density, 2),
            "detail": (
                f"mật độ LABEL_MISMATCH = {density:.1f}/snapshot trong {total} gần nhất — ontology đang bị bào mòn dần"
            ),
        }
    return None


def compute_early_warning() -> dict:
    """Evaluate leading indicators for semantic drift risk.

    Pure analysis — reads history, writes nothing, triggers nothing.

    Returns:
        {
            "early_warning": "clean" | "caution" | "warning",
            "risk_of_drift": 0.0 - 1.0,
            "drift_trend": "rising" | "stable" | "declining",
            "indicators": [],
        }

    Levels:
        clean   → no leading indicator triggered
        caution → 1 indicator triggered
        warning → ≥ 2 indicators triggered
    """
    if len(_history) < MIN_WARNING_HISTORY:
        return {
            "early_warning": "clean",
            "risk_of_drift": 0.0,
            "drift_trend": "stable",
            "indicators": [],
        }

    indicators: list[dict] = []

    i1 = _drift_slope()
    if i1:
        indicators.append(i1)

    i2 = _acceleration()
    if i2:
        indicators.append(i2)

    i3 = _label_density()
    if i3:
        indicators.append(i3)

    # ── Risk score ──────────────────────────────────────────────
    weights = {"DRIFT_SLOPE": 0.3, "ACCELERATION": 0.5, "LABEL_DENSITY": 0.2}
    risk = sum(weights.get(i["indicator"], 0.3) for i in indicators)
    risk = min(1.0, risk)

    # ── Level ───────────────────────────────────────────────────
    count = len(indicators)
    if count >= 2:
        level = "warning"
    elif count == 1:
        level = "caution"
    else:
        level = "clean"

    # ── Trend ───────────────────────────────────────────────────
    scores = [e["kernel_drift_score"] for e in _history[-WINDOW_SIZE:] if e["kernel_drift_score"] is not None]
    if len(scores) >= 2:
        slope = _safe_slope(scores)
        if slope > 0.02:
            trend = "rising"
        elif slope < -0.02:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return {
        "early_warning": level,
        "risk_of_drift": round(risk, 4),
        "drift_trend": trend,
        "indicators": indicators,
    }
