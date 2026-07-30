"""applicability_engine.py — Sprint 2: A_i = f(macro_state, sector_phase, entropy).

Replaces the Sprint 1 stub A_i = 1.0 with context-dependent applicability
scores for all 7 evidence nodes.

Formula:
  A_i(node, macro, sector, entropy) = base(macro, node) × sector_mod(node, sector) × entropy_factor(entropy)

Integration:
  Called from BayesianGovernor.assess() → writes A_i to evidence_registry
  via EvidenceEngine.set_applicability().

# ===================================================================
# ADR #3 — WHY Entropy Dampener = (1 - 0.5 * H)?
# ===================================================================
# Khi thi truong bat dinh (Entropy H dang cao), tu dong thu hep bien do
# A_i cua toan bo cac nut. Bat buoc Governor v3 thu hep quy mo vi the
# Kelly, ngan ngua viec hanh dong tu tin trong moi truong nhieu.
# Tai H=1.0: dampener=0.5 => moi A_i giam 50%. Tai H=0.0: khong doi.
# ===================================================================
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# ── MACRO STATE → EVIDENCE NODE BASE APPLICABILITY ──────────────────────
# Scale 0.0 (irrelevant) → 1.0 (highly relevant)
# Each row describes how important a piece of evidence is in a given macro regime.

MACRO_APPLICABILITY: Dict[str, Dict[str, float]] = {
    "CREDIT_STRESS": {
        "macro": 0.90,   "transmission": 0.85, "sector": 0.30,
        "health": 0.20,  "capital_allocation": 0.35,
        "valuation": 0.15, "behavior": 0.70,
    },
    "AI_BOOM": {
        "macro": 0.60,   "transmission": 0.40, "sector": 0.70,
        "health": 0.80,  "capital_allocation": 0.60,
        "valuation": 0.50, "behavior": 0.30,
    },
    "LIQUIDITY_EXPANSION": {
        "macro": 0.75,   "transmission": 0.90, "sector": 0.60,
        "health": 0.50,  "capital_allocation": 0.55,
        "valuation": 0.40, "behavior": 0.35,
    },
    "INFLATION_SHOCK": {
        "macro": 0.85,   "transmission": 0.60, "sector": 0.50,
        "health": 0.30,  "capital_allocation": 0.40,
        "valuation": 0.60, "behavior": 0.55,
    },
    "RECOVERY": {
        "macro": 0.65,   "transmission": 0.55, "sector": 0.80,
        "health": 0.75,  "capital_allocation": 0.55,
        "valuation": 0.35, "behavior": 0.30,
    },
    "STABLE": {
        "macro": 0.45,   "transmission": 0.45, "sector": 0.55,
        "health": 0.70,  "capital_allocation": 0.50,
        "valuation": 0.60, "behavior": 0.25,
    },
    "RISK_OFF": {
        "macro": 0.80,   "transmission": 0.70, "sector": 0.20,
        "health": 0.15,  "capital_allocation": 0.20,
        "valuation": 0.20, "behavior": 0.90,
    },
    "PRE_CREDIT_EXPANSION": {
        "macro": 0.70,   "transmission": 0.75, "sector": 0.65,
        "health": 0.50,  "capital_allocation": 0.60,
        "valuation": 0.30, "behavior": 0.35,
    },
}

# ── SECTOR PHASE MODULATORS ─────────────────────────────────────────────
# Multiply base applicability for sector-sensitive nodes.
SECTOR_MODULATORS: Dict[str, Dict[str, float]] = {
    "EARLY":  {"sector": 1.00, "health": 0.85, "capital_allocation": 0.80},
    "MID":    {"sector": 1.00, "health": 1.00, "capital_allocation": 1.00},
    "LATE":   {"sector": 0.80, "health": 0.90, "capital_allocation": 0.85},
    "WEAKENING": {"sector": 0.40, "health": 0.60, "capital_allocation": 0.60},
    "NEUTRAL":   {"sector": 0.70, "health": 0.80, "capital_allocation": 0.80},
}

# Nodes affected by sector phase modulation
SECTOR_SENSITIVE_NODES = {"sector", "health", "capital_allocation"}

# ── ENTROPY DAMPENING ──────────────────────────────────────────────────
# At entropy = 0.0 → dampener = 1.0 (no effect)
# At entropy = 1.0 → dampener = 0.5 (max halving)
# Formula: dampener = 1.0 - 0.5 × entropy

ENTROPY_DAMPEN_SLOPE = 0.5


def compute_applicability(
    macro_state: str,
    sector_phase: str,
    entropy: float,
) -> Dict[str, float]:
    """Compute A_i for all 7 evidence nodes.

    Args:
        macro_state: Current macro regime label.
        sector_phase: Current sector rotation phase.
        entropy: Normalized Shannon entropy [0.0, 1.0].

    Returns:
        {node_id: applicability_score} with scores in [0.0, 1.0].
    """
    # 1. Base applicability from macro state
    base: Dict[str, float] = dict(MACRO_APPLICABILITY.get(macro_state, {}))
    if not base:
        # Fallback: neutral 0.5 for all
        from calibration.evidence_engine import EVIDENCE_NODE_IDS
        base = {nid: 0.5 for nid in EVIDENCE_NODE_IDS}

    # 2. Sector phase modulation
    sector_mod = SECTOR_MODULATORS.get(sector_phase, {})
    for nid in SECTOR_SENSITIVE_NODES:
        mult = sector_mod.get(nid, 1.0)
        base[nid] = base.get(nid, 0.5) * mult

    # 3. Entropy dampening
    dampener = 1.0 - ENTROPY_DAMPEN_SLOPE * max(0.0, min(1.0, entropy))
    result = {nid: max(0.0, min(1.0, v * dampener)) for nid, v in base.items()}

    return result


def get_applicability_heatmap_data() -> Dict[str, Dict[str, float]]:
    """Return macro×node matrix for heatmap display.

    Returns {macro_state: {node_id: base_applicability}}.
    """
    return {ms: dict(row) for ms, row in MACRO_APPLICABILITY.items()}


def print_applicability_report(
    current_applicability: Optional[Dict[str, float]] = None,
    macro_state: str = "STABLE",
    sector_phase: str = "NEUTRAL",
    entropy: float = 0.0,
    lang_mode: str = "full",
):
    """Print applicability matrix status."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    # Compute if not provided
    if current_applicability is None:
        current_applicability = compute_applicability(macro_state, sector_phase, entropy)

    print(f"\n  {'='*80}")
    print(f"  {_('APPLICABILITY ENGINE')} — {_('Sprint 2')}")
    print(f"  {_('Macro')}: {macro_state} | {_('Sector Phase')}: {sector_phase} | {_('Entropy')}: {entropy:.3f}")
    print(f"  {'='*80}")
    print(f"  {_('Node'):<22} {_('A_i')}")
    print(f"  {'─'*40}")
    for nid, ai in sorted(current_applicability.items()):
        bar = "█" * int(ai * 30) + "░" * (30 - int(ai * 30))
        print(f"  {nid:<22} {ai:<6.3f} {bar}")
    print(f"\n  {_('Formula')}: A_i = base(macro) × sector_mod(sector) × (1 - 0.5×entropy)")
    print(f"  {_('Entropy dampener')}: {1.0 - ENTROPY_DAMPEN_SLOPE * entropy:.3f}")


def print_heatmap(lang_mode: str = "full"):
    """Print macro × node applicability heatmap."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    from calibration.evidence_engine import EVIDENCE_NODE_IDS

    print(f"\n  {'='*100}")
    print(f"  {_('APPLICABILITY HEATMAP')} — {_('Macro State × Evidence Node')}")
    print(f"  {'='*100}")
    hdr = f"  {'Macro State':<24}"
    for nid in EVIDENCE_NODE_IDS:
        hdr += f" {nid[:6]:>7}"
    print(hdr)
    print(f"  {'─'*100}")
    for ms, row in MACRO_APPLICABILITY.items():
        line = f"  {ms:<24}"
        for nid in EVIDENCE_NODE_IDS:
            v = row.get(nid, 0.5)
            bar = "█" * int(v * 8) + "░" * (8 - int(v * 8))
            line += f" {v:>5.2f}{bar}"
        print(line)
    print(f"\n{_('Legend')}: █ = high applicability, ░ = low applicability [{_('Sprint 2')}]")
