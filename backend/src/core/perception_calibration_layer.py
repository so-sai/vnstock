"""
perception_calibration_layer.py — Perception Calibration Layer.

Reads shadow log + driver history, diagnoses systematic biases,
and outputs calibration recommendations.

This is a SELF-DIAGNOSIS layer, NOT a self-tuning layer.
It never modifies kernel weights, driver distributions, or ROM.

Architecture:
    Shadow Log → Calibration Layer → Diagnosis Report
                                        ↓
                              (manual review before kernel change)
"""

from __future__ import annotations

from collections import Counter

# ── Diagnosis helpers ─────────────────────────────────────────────────────


def _build_confusion_matrix(entries: list[dict]) -> dict:
    """Build predicted vs actual driver confusion matrix from shadow log.

    Returns:
        {
            "FLOW": {"FLOW": 5, "BREADTH": 3, ...},
            "BREADTH": {"FLOW": 2, "BREADTH": 7, ...},
            ...
        }
    """
    matrix: dict[str, Counter] = {}
    for entry in entries:
        pred = entry.get("prediction", {})
        realized = entry.get("realized")
        if not realized:
            continue
        predicted = pred.get("dominant_driver", "UNKNOWN")
        actual = realized.get("dominant_driver", "UNKNOWN")
        if predicted not in matrix:
            matrix[predicted] = Counter()
        matrix[predicted][actual] += 1
    return {k: dict(v) for k, v in matrix.items()}


def _driver_switch_points(entries: list[dict]) -> list[dict]:
    """Detect points where the predicted driver switched day-to-day.

    Returns list of switch events with before/after context.
    """
    switches = []
    for i in range(1, len(entries)):
        prev = entries[i - 1].get("prediction", {})
        curr = entries[i].get("prediction", {})
        p_driver = prev.get("dominant_driver", "UNKNOWN")
        c_driver = curr.get("dominant_driver", "UNKNOWN")
        if p_driver != c_driver:
            realized_prev = entries[i - 1].get("realized")
            realized_curr = entries[i].get("realized")
            switches.append({
                "from": p_driver,
                "to": c_driver,
                "from_actual": realized_prev["dominant_driver"] if realized_prev else "?",
                "to_actual": realized_curr["dominant_driver"] if realized_curr else "?",
                "date_from": entries[i - 1].get("date", "?"),
                "date_to": entries[i].get("date", "?"),
            })
    return switches


def _regime_sensitivity(entries: list[dict]) -> dict:
    """Measure how accuracy changes per regime."""
    by_regime: dict[str, dict] = {}
    for entry in entries:
        realized = entry.get("realized")
        if not realized:
            continue
        regime = entry.get("prediction", {}).get("regime_status", "UNKNOWN")
        if regime not in by_regime:
            by_regime[regime] = {"total": 0, "correct": 0}
        by_regime[regime]["total"] += 1
        if realized.get("driver_match"):
            by_regime[regime]["correct"] += 1

    result = {}
    for regime, data in by_regime.items():
        result[regime] = {
            "total": data["total"],
            "accuracy": round(data["correct"] / data["total"], 4) if data["total"] > 0 else 0.0,
            "correct": data["correct"],
        }
    return result


# ── Calibration analysis ──────────────────────────────────────────────────


def diagnose_calibration(shadow_entries: list[dict]) -> dict:
    """Analyze shadow log to identify systematic perception biases.

    Args:
        shadow_entries: list of shadow log entries (with .realized)

    Returns:
        {
            "overall_driver_accuracy": float,
            "confusion_matrix": dict,
            "regime_sensitivity": dict,
            "switch_analysis": {...},
            "calibration_issues": [
                {
                    "issue": "...",
                    "severity": 0.0 - 1.0,
                    "detail": "...",
                    "recommendation": "...",
                    "affected_driver_pair": ["A", "B"],
                }
            ],
            "calibration_score": 0.0 - 1.0,
        }
    """
    realized = [e for e in shadow_entries if e.get("realized") is not None]
    if not realized:
        return {
            "overall_driver_accuracy": 0.0,
            "confusion_matrix": {},
            "regime_sensitivity": {},
            "switch_analysis": {},
            "calibration_issues": [],
            "calibration_score": 1.0,
        }

    # ── Confusion matrix ─────────────────────────────────────────
    matrix = _build_confusion_matrix(realized)

    # ── Accuracy ─────────────────────────────────────────────────
    total = sum(
        sum(v.values()) for v in matrix.values()
    ) if matrix else 0
    correct = sum(
        v.get(k, 0) for k, v in matrix.items()
    ) if matrix else 0
    accuracy = correct / total if total > 0 else 0.0

    # ── Regime sensitivity ───────────────────────────────────────
    regime_sens = _regime_sensitivity(realized)

    # ── Driver switches ──────────────────────────────────────────
    switches = _driver_switch_points(realized)

    # ── Calibration issues ───────────────────────────────────────
    issues = []

    # Issue 1: Driver pair confusion
    for pred_driver, actuals in matrix.items():
        total_pred = sum(actuals.values())
        for actual_driver, count in actuals.items():
            if actual_driver != pred_driver:
                confusion_rate = count / total_pred if total_pred > 0 else 0.0
                if confusion_rate >= 0.25:
                    issues.append({
                        "issue": "NHAM_VAI_TRO_DAN_DAT",
                        "severity": round(confusion_rate, 4),
                        "detail": (
                            f"khi hệ dự đoán '{pred_driver}', "
                            f"thực tế '{actual_driver}' xảy ra {count}/{total_pred} lần "
                            f"({confusion_rate:.0%})"
                        ),
                        "recommendation": (
                            f"cần hiệu chỉnh ranh giới giữa {pred_driver} và {actual_driver} "
                            f"— đặc biệt trong giai đoạn rotation"
                        ),
                        "affected_driver_pair": [pred_driver, actual_driver],
                    })

    # Issue 2: Switch instability
    if switches:
        false_switches = [
            s for s in switches
            if s["from"] == s["to_actual"] and s["to"] == s["from_actual"]
        ]
        if false_switches:
            issues.append({
                "issue": "CHUYEN_MA_DUNG_SAI",
                "severity": round(len(false_switches) / len(switches), 4),
                "detail": (
                    f"{len(false_switches)}/{len(switches)} lần chuyển driver là "
                    f"nhiễu (hệ chuyển từ A→B nhưng thực tế B→A)"
                ),
                "recommendation": (
                    "driver đang dao động giả — cần giảm độ nhạy chuyển driver "
                    "trong rotation ngắn"
                ),
                "affected_driver_pair": ["FLOW", "BREADTH"],
            })

    # Issue 3: Regime blind spot
    for regime, data in regime_sens.items():
        if data["total"] >= 3 and data["accuracy"] < 0.3:
            issues.append({
                "issue": "MU_REGIME",
                "severity": round(1.0 - data["accuracy"], 4),
                "detail": (
                    f"accuracy={data['accuracy']:.0%} trong regime {regime} "
                    f"({data['total']} mẫu) — hệ không nhìn thấy đúng driver trong chế độ này"
                ),
                "recommendation": (
                    f"regime {regime} đang là điểm mù của hệ — "
                    f"cần tăng trọng số VOLATILITY/MACRO trong chế độ này"
                ),
                "affected_driver_pair": [regime, "?"],
            })

    # ── Calibration score ────────────────────────────────────────
    if not issues:
        cal_score = 1.0
    else:
        total_sev = sum(i["severity"] for i in issues)
        cal_score = max(0.0, 1.0 - total_sev)

    return {
        "overall_driver_accuracy": round(accuracy, 4),
        "confusion_matrix": matrix,
        "regime_sensitivity": regime_sens,
        "switch_analysis": {
            "total_switches": len(switches),
            "switches": switches[:10],  # keep report size bounded
        },
        "calibration_issues": issues,
        "calibration_score": round(cal_score, 4),
    }
