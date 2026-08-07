"""interaction_engine.py — Non-linear Macro Synergy & Friction Detector.

LAW-009 Extension: Macro signals don't just transmit with lag — they INTERACT.
When multiple macro nodes move together, they create non-linear synergy or
friction effects that a simple dot product (M · W_i) cannot capture.

Mathematical Foundation:
  Linear model:  Score_i = M · W_i  (dot product)
  Enhanced model: Score_i = M · W_i × Π(synergy_k)  (multiplicative interactions)

  Where synergy_k = 1.0 + (impact_k - 1.0) × min(1.0, excess_k)
  And excess_k = (v1 - t1) × (v2 - t2) × scale_factor

  Properties:
    - Synergy is 1.0 when no interaction is active (neutral)
    - Synergy > 1.0 for POSITIVE_BOOM (amplifying)
    - Synergy < 1.0 for NEGATIVE_FRICTION (dampening)
    - Final multiplier clamped to [0.70, 1.35] to protect Bayesian framework

Interaction Rules (Expert-Calibrated):
  1. CHINA_COMMODITY_SUPER_CYCLE:
     When China_Economy > 0.55 AND Commodity_Cycle > 0.55:
     STEEL ×1.35, OIL ×1.25, TRANS ×1.20
     WHY: China demand + rising commodities = super-cycle for materials.

  2. DOUBLE_LIQUIDITY_EASING:
     When US_Liquidity > 0.55 AND Domestic_Liquidity > 0.55:
     BANK ×1.30, RE ×1.35, SEC ×1.40
     WHY: Global + local easing = credit expansion, asset price inflation.

  3. COMMODITY_COST_SQUEEZE:
     When Commodity_Cycle > 0.65 AND Domestic_Liquidity < 0.35:
     RE ×0.80, BANK ×0.85
     WHY: High input costs + tight local liquidity = margin squeeze.

  4. RISK_OFF_DIVERGENCE:
     When US_Liquidity < 0.35 AND Domestic_Liquidity > 0.55:
     BANK ×0.90, SEC ×0.85
     WHY: Fed tight but SBV loose = capital flight pressure, VNUnder pressure.

  5. STAGFLATION_SQUEEZE:
     When Commodity_Cycle > 0.70 AND US_Liquidity < 0.30:
     CONSUMER ×0.80, FOOD ×0.85, RE ×0.85
     WHY: High costs + tight global liquidity = demand destruction.

Usage:
  engine = InteractionEngine()
  result = engine.compute(macro_vector, "STEEL")
  # result.multiplier: 1.185
  # result.active_synergies: [{"rule": "CHINA_COMMODITY_SUPER_CYCLE", ...}]
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _hydrate_path() -> Path:
    """Path Hydrator v2.1: Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()

# ── Interaction Rules ─────────────────────────────────────────────────
# Each rule defines:
#   pair:       Two macro nodes that interact
#   thresholds: Minimum values for each node to activate
#   condition:  Optional custom lambda for complex conditions
#   impact:     Per-sector multiplier impact (1.0 = no effect)
#   kind (booming vs friction): POSITIVE_BOOM (amplifying) or NEGATIVE_FRICTION (dampening)
#   scale:      Excess scaling factor (default 4.0)

INTERACTION_RULES: list[dict[str, Any]] = [
    {
        "name": "CHINA_COMMODITY_SUPER_CYCLE",
        "pair": ("China_Economy", "Commodity_Cycle"),
        "thresholds": (0.55, 0.55),
        "impact": {"STEEL": 1.35, "OIL": 1.25, "TRANS": 1.20, "CONST": 1.15},
        "type": "POSITIVE_BOOM",
        "scale": 4.0,
        "description": "China demand + rising commodities = super-cycle for materials",
    },
    {
        "name": "DOUBLE_LIQUIDITY_EASING",
        "pair": ("US_Liquidity", "Domestic_Liquidity"),
        "thresholds": (0.55, 0.55),
        "impact": {"BANK": 1.30, "RE": 1.35, "SEC": 1.40, "TECH": 1.15},
        "type": "POSITIVE_BOOM",
        "scale": 4.0,
        "description": "Global + local easing = credit expansion, asset price inflation",
    },
    {
        "name": "COMMODITY_COST_SQUEEZE",
        "pair": ("Commodity_Cycle", "Domestic_Liquidity"),
        "thresholds": (0.65, 0.35),
        "condition": lambda m: m.get("Commodity_Cycle", 0) > 0.65 and m.get("Domestic_Liquidity", 0) < 0.35,
        "impact": {"RE": 0.80, "BANK": 0.85, "CONSUMER": 0.90},
        "type": "NEGATIVE_FRICTION",
        "scale": 4.0,
        "description": "High input costs + tight local liquidity = margin squeeze",
    },
    {
        "name": "RISK_OFF_DIVERGENCE",
        "pair": ("US_Liquidity", "Domestic_Liquidity"),
        "thresholds": (0.35, 0.55),
        "condition": lambda m: m.get("US_Liquidity", 0) < 0.35 and m.get("Domestic_Liquidity", 0) > 0.55,
        "impact": {"BANK": 0.90, "SEC": 0.85, "RE": 0.90},
        "type": "NEGATIVE_FRICTION",
        "scale": 4.0,
        "description": "Fed tight but SBV loose = capital flight pressure",
    },
    {
        "name": "STAGFLATION_SQUEEZE",
        "pair": ("Commodity_Cycle", "US_Liquidity"),
        "thresholds": (0.70, 0.30),
        "condition": lambda m: m.get("Commodity_Cycle", 0) > 0.70 and m.get("US_Liquidity", 0) < 0.30,
        "impact": {"CONSUMER": 0.80, "FOOD": 0.85, "RE": 0.85, "BANK": 0.90},
        "type": "NEGATIVE_FRICTION",
        "scale": 3.0,
        "description": "High costs + tight global liquidity = demand destruction",
    },
]

# ── Multiplier Bounds ─────────────────────────────────────────────────
MULTIPLIER_LOW = 0.70
MULTIPLIER_HIGH = 1.35


# ── Result Dataclass ──────────────────────────────────────────────────


@dataclass
class InteractionResult:
    """Result of non-linear interaction computation for one sector."""

    sector: str
    multiplier: float  # Clamped to [0.70, 1.35]
    raw_multiplier: float  # Before clamping
    active_synergies: list[dict[str, Any]]  # List of triggered rules
    n_positive: int  # Count of positive synergies
    n_negative: int  # Count of negative frictions
    description: str  # Human-readable summary


class InteractionEngine:
    """Detect non-linear macro synergies and frictions between sectors.

    Architecture:
      1. For each interaction rule, check if macro nodes exceed thresholds
      2. If triggered, compute excess = (v1-t1) × (v2-t2) × scale
      3. Apply sector-specific impact: multiplier *= 1.0 + (impact-1.0) × min(1.0, excess)
      4. Clamp final multiplier to [0.70, 1.35]

    This module is called AFTER MacroLagEngine (Step 2) and BEFORE
    the final sector score computation. The flow is:

      RegionalInfluenceEngine → M vector (raw)
      MacroLagEngine → M vector (lag-adjusted, per sector)
      SectorExposureMatrix → Score = M · W_i (dot product)
      InteractionEngine → Score × multiplier (non-linear adjustment)

    Usage:
      engine = InteractionEngine()
      result = engine.compute(macro_vector, "STEEL")
      adjusted_score = raw_score * result.multiplier
    """

    def __init__(self, custom_rules: list[dict[str, Any]] | None = None):
        self.rules = custom_rules or INTERACTION_RULES

    def compute(self, macro_vector: dict[str, float], sector: str) -> InteractionResult:
        """Compute non-linear interaction multiplier for a sector.

        Args:
            macro_vector: Macro state vector M ∈ [0, 1]^4
            sector: Sector code (e.g., "STEEL", "BANK")

        Returns:
            InteractionResult with multiplier and active synergies
        """
        multiplier = 1.0
        active_synergies = []

        for rule in self.rules:
            triggered = False
            excess = 0.0

            # Check condition: either pair+thresholds or custom lambda
            if "condition" in rule:
                # Custom condition (e.g., COMMODITY_COST_SQUEEZE)
                if rule["condition"](macro_vector):
                    triggered = True
                    # Compute excess from the pair values
                    f1, f2 = rule["pair"]
                    v1 = macro_vector.get(f1, 0.5)
                    v2 = macro_vector.get(f2, 0.5)
                    t1, t2 = rule["thresholds"]
                    excess = abs(v1 - t1) * abs(v2 - t2) * rule.get("scale", 4.0)
            elif "pair" in rule:
                # Simple threshold check
                f1, f2 = rule["pair"]
                v1 = macro_vector.get(f1, 0.5)
                v2 = macro_vector.get(f2, 0.5)
                t1, t2 = rule["thresholds"]

                if v1 > t1 and v2 > t2:
                    triggered = True
                    excess = (v1 - t1) * (v2 - t2) * rule.get("scale", 4.0)

            if triggered:
                impact = rule["impact"].get(sector, 1.0)
                if impact != 1.0:
                    # Smooth application: impact scaled by excess
                    applied_impact = 1.0 + (impact - 1.0) * min(1.0, excess)
                    multiplier *= applied_impact
                    active_synergies.append(
                        {
                            "rule": rule["name"],
                            "multiplier": round(applied_impact, 4),
                            "type": rule["type"],
                            "impact_raw": impact,
                            "excess": round(excess, 4),
                            "description": rule.get("description", ""),
                        }
                    )

        raw_multiplier = multiplier
        clamped_multiplier = float(np.clip(multiplier, MULTIPLIER_LOW, MULTIPLIER_HIGH))

        n_positive = sum(1 for s in active_synergies if s["type"] == "POSITIVE_BOOM")
        n_negative = sum(1 for s in active_synergies if s["type"] == "NEGATIVE_FRICTION")

        # Summary
        if not active_synergies:
            summary = "No active interactions"
        else:
            parts = [f"{s['rule']}({s['multiplier']:.3f})" for s in active_synergies]
            summary = f"{len(active_synergies)} active: {', '.join(parts)}"

        return InteractionResult(
            sector=sector,
            multiplier=round(clamped_multiplier, 4),
            raw_multiplier=round(raw_multiplier, 4),
            active_synergies=active_synergies,
            n_positive=n_positive,
            n_negative=n_negative,
            description=summary,
        )

    def compute_all_sectors(self, macro_vector: dict[str, float]) -> dict[str, InteractionResult]:
        """Compute interaction multipliers for all defined sectors.

        Args:
            macro_vector: Macro state vector M

        Returns:
            Dict mapping sector codes to InteractionResult
        """
        # Collect all unique sectors from rules
        all_sectors = set()
        for rule in self.rules:
            all_sectors.update(rule["impact"].keys())

        results = {}
        for sector in sorted(all_sectors):
            results[sector] = self.compute(macro_vector, sector)
        return results
