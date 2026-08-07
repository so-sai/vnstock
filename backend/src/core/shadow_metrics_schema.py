"""
shadow_metrics_schema.py — Shadow Deploy Metrics Store.

Log-only comparator. Never affects the main pipeline.
Captures every snapshot output and compares against next-day proxy data.

3 metric families:
  1. DRIVER_ACCURACY   — predicted dominant driver vs realized next-day proxy
  2. NARRATIVE_VALIDITY — explanation alignment (ETS + semantic score)
  3. DRIFT_STABILITY   — temporal drift curve + early warning frequency

Architecture:
  LIVE MARKET → CORE SYSTEM → SHADOW LAYER (this) → METRICS STORE
                                                      ↓
                                               (read-only dashboard data)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────

_SHADOW_LOG: list[dict] = []
_MAX_LOG_SIZE = 365  # keep 1 year of daily shadow data


# ── Schema builders ──────────────────────────────────────────────────────────


def build_shadow_entry(
    *,
    date_label: str,
    snapshot: dict,
    consistency_result: dict,
    early_warning: dict,
    temporal_drift: dict,
) -> dict:
    """Capture a single shadow observation from the current pipeline outputs.

    Args:
        date_label:      "2026-06-06" format
        snapshot:        raw snapshot dict that went into the pipeline
        consistency_result: output from check_semantic_consistency()
        early_warning:   output from compute_early_warning()
        temporal_drift:  output from compute_temporal_drift()

    Returns:
        A shadow log entry (also stored internally).
    """
    driver_state = snapshot.get("driver_state", {})
    drift_result = snapshot.get("drift_result", {})

    entry = {
        "date": date_label,
        "timestamp": datetime.now().isoformat(),
        # ── Prediction snapshot ────────────────────────────────────
        "prediction": {
            "dominant_driver": driver_state.get("dominant", "UNKNOWN"),
            "driver_distribution": dict(driver_state.get("distribution", {})),
            "drift_score": drift_result.get("drift_score", 0.0),
            "drift_status": drift_result.get("drift_status", "NONE"),
            "regime_status": snapshot.get("regime_status", "UNKNOWN"),
        },
        # ── Semantic health ─────────────────────────────────────────
        "semantic": {
            "consistency_score": consistency_result.get("semantic_consistency_score", 1.0),
            "drift_in_meaning": consistency_result.get("drift_in_meaning", False),
            "violations": [{"type": v["type"], "severity": v["severity"]} for v in consistency_result.get("violations", [])],
        },
        # ── Temporal / early warning ────────────────────────────────
        "stability": {
            "early_warning": early_warning.get("early_warning", "clean"),
            "risk_of_drift": early_warning.get("risk_of_drift", 0.0),
            "drift_trend": early_warning.get("drift_trend", "stable"),
            "temporal_drift_score": temporal_drift.get("temporal_drift_score", 0.0),
            "meaning_is_drifting": temporal_drift.get("meaning_is_drifting", False),
        },
        # ── Placeholder: filled by compare_next_day() ───────────────
        "realized": None,
    }

    _SHADOW_LOG.append(entry)
    if len(_SHADOW_LOG) > _MAX_LOG_SIZE:
        _SHADOW_LOG.pop(0)

    return entry


def compare_next_day(
    date_label: str,
    *,
    actual_dominant_driver: str,
    actual_driver_proxy: dict | None = None,
    actual_regime: str | None = None,
    market_direction: float | None = None,
) -> dict | None:
    """Attach realized next-day data to a shadow entry.

    Args:
        date_label:          "2026-06-06" — the prediction date (not today)
        actual_dominant_driver: "BREADTH" — what actually dominated next day
        actual_driver_proxy:  full distribution if available
        actual_regime:        realized regime
        market_direction:     VNINDEX % change

    Returns:
        The updated shadow entry (with .realized filled) or None if not found.
    """
    for entry in _SHADOW_LOG:
        if entry["date"] == date_label:
            predicted = entry.get("prediction", {})
            entry["realized"] = {
                "dominant_driver": actual_dominant_driver,
                "driver_proxy": actual_driver_proxy or {},
                "regime": actual_regime,
                "market_direction": market_direction,
                "driver_match": predicted.get("dominant_driver") == actual_dominant_driver,
            }
            return entry
    return None


# ── Metrics aggregation ─────────────────────────────────────────────────────


def compute_shadow_metrics(window_days: int = 30) -> dict:
    """Aggregate shadow metrics over the last N days.

    Only counts entries that have been "realized" (compared with next-day data).

    Returns:
        {
            "driver_accuracy": 0.0 - 1.0,
            "narrative_alignment": 0.0 - 1.0,
            "drift_risk": 0.0 - 1.0,
            "early_warning_frequency": 0.0,
            "sample_count": int,
            "metrics_by_type": {...},
        }
    """
    realized = [e for e in _SHADOW_LOG if e.get("realized") is not None]
    if not realized:
        return {
            "driver_accuracy": 0.0,
            "narrative_alignment": 0.0,
            "drift_risk": 0.0,
            "early_warning_frequency": 0.0,
            "sample_count": 0,
            "metrics_by_type": {},
        }

    # Take only last window_days
    recent = realized[-window_days:]

    # 1. Driver accuracy
    matches = sum(1 for e in recent if e["realized"]["driver_match"])
    driver_accuracy = matches / len(recent)

    # 2. Narrative alignment = avg consistency_score
    alignment = sum(e["semantic"]["consistency_score"] for e in recent) / len(recent)

    # 3. Drift risk = avg risk_of_drift
    drift_risk = sum(e["stability"]["risk_of_drift"] for e in recent) / len(recent)

    # 4. Early warning frequency
    warnings = sum(1 for e in recent if e["stability"]["early_warning"] != "clean")
    warning_freq = warnings / len(recent)

    # 5. Metrics by type
    metrics_by_type = {}
    for entry in recent:
        for v in entry["semantic"]["violations"]:
            t = v["type"]
            metrics_by_type.setdefault(t, {"count": 0, "total_severity": 0.0})
            metrics_by_type[t]["count"] += 1
            metrics_by_type[t]["total_severity"] += v["severity"]

    return {
        "driver_accuracy": round(driver_accuracy, 4),
        "narrative_alignment": round(alignment, 4),
        "drift_risk": round(drift_risk, 4),
        "early_warning_frequency": round(warning_freq, 4),
        "sample_count": len(recent),
        "metrics_by_type": metrics_by_type,
    }


def get_shadow_log() -> list[dict]:
    return list(_SHADOW_LOG)


def clear_shadow_log() -> None:
    _SHADOW_LOG.clear()


def export_shadow_log(path: Path) -> None:
    """Persist shadow log to JSON file for dashboard or review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "exported_at": datetime.now().isoformat(),
                "total_entries": len(_SHADOW_LOG),
                "entries": _SHADOW_LOG,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
