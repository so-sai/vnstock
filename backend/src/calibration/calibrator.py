"""calibrator.py — Bayesian conjugate update of Likelihood Ratios.

Each evidence level maintains a Beta posterior:
  alpha = 1 + N_gains_when_evidence_active
  beta  = 1 + N_losses_when_evidence_active

LR = [alpha/(alpha+beta)] / [1 - alpha/(alpha+beta)] / prior_odds

The new LRs can be reloaded into P3 Governor to replace hard-coded LR_MACRO etc.
"""

import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from calibration.prediction_log import (get_outcomes_for_calibration,
                                         get_beta_posteriors, upsert_beta)

# Prior odds from P3 Governor (must match company_state.PRIOR_ODDS)
PRIOR_ODDS = 0.53 / (1.0 - 0.53)

# Evidence keys map from evidence_column_name → (table_name in prediction_log, value_column)
EVIDENCE_COLUMNS = [
    "macro_state",
    "transmission_phase",
    "sector_phase",
    "health_archetype",
    "valuation_zone",
    "behavior_position",
]


def update_beta_posteriors(days_back: int = 90):
    """Scan resolved predictions, update Beta(alpha, beta) per evidence level."""
    outcomes = get_outcomes_for_calibration(days_back)
    if not outcomes:
        return

    # Accumulate gains/losses per evidence level
    counts: Dict[str, Tuple[float, float]] = {}
    for row in outcomes:
        y = row["outcome"]
        for col in EVIDENCE_COLUMNS:
            val = str(row.get(col, "UNKNOWN")).strip().upper()
            key = f"{col}::{val}"
            a, b = counts.get(key, (0.0, 0.0))
            if y == 1.0:
                counts[key] = (a + 1.0, b)
            else:
                counts[key] = (a, b + 1.0)

    # Retrieve existing posteriors, add new counts, persist
    existing = get_beta_posteriors()
    for key, (add_a, add_b) in counts.items():
        old_a, old_b = existing.get(key, (1.0, 1.0))
        new_a = old_a + add_a
        new_b = old_b + add_b
        upsert_beta(key, new_a, new_b)


def compute_lr_from_beta(alpha: float, beta: float) -> float:
    """Compute LR from Beta posterior.

    LR = (alpha/(alpha+beta)) / (1 - alpha/(alpha+beta)) / PRIOR_ODDS
    """
    prob = alpha / (alpha + beta)
    if prob <= 0.0 or prob >= 1.0:
        return 1.0
    odds = prob / (1.0 - prob)
    lr = odds / PRIOR_ODDS
    return round(lr, 4)


def get_calibrated_lrs() -> Dict[str, float]:
    """Return dict of evidence_key → calibrated LR."""
    posteriors = get_beta_posteriors()
    lrs = {}
    for key, (alpha, beta) in posteriors.items():
        lrs[key] = compute_lr_from_beta(alpha, beta)
    return lrs


def calibration_summary(days_back: int = 90) -> dict:
    """Return a full calibration diagnostic report."""
    outcomes = get_outcomes_for_calibration(days_back)
    if not outcomes:
        return {"status": "NO_DATA", "n_outcomes": 0}

    # Group by evidence column
    groups: Dict[str, Dict[str, dict]] = {}
    for col in EVIDENCE_COLUMNS:
        groups[col] = {}

    for row in outcomes:
        for col in EVIDENCE_COLUMNS:
            val = str(row.get(col, "UNKNOWN")).strip().upper()
            counts = groups[col]
            if val not in counts:
                counts[val] = {"n": 0, "gains": 0}
            counts[val]["n"] += 1
            if row["outcome"] == 1.0:
                counts[val]["gains"] += 1

    details = {}
    for col in EVIDENCE_COLUMNS:
        entries = []
        for val, cnt in sorted(groups[col].items()):
            key = f"{col}::{val}"
            posteriors = get_beta_posteriors()
            alpha, beta = posteriors.get(key, (1.0, 1.0))
            lr = compute_lr_from_beta(alpha, beta)
            accuracy = cnt["gains"] / cnt["n"] if cnt["n"] > 0 else 0.5
            entries.append({
                "value": val,
                "n": cnt["n"],
                "gains": cnt["gains"],
                "accuracy": round(accuracy, 4),
                "alpha": alpha,
                "beta": beta,
                "lr_calibrated": lr,
            })
        details[col] = entries

    return {
        "status": "OK",
        "n_outcomes": len(outcomes),
        "window_days": days_back,
        "details": details,
    }
