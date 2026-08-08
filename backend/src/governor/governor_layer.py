"""governor_layer.py — Governor Layer (Risk Modifier Fusion).

Combines three independent risk inputs into ONE allocation sizing multiplier:

    Alloc_final = Alloc_Kelly × Confidence × (1 - U_shock)

  Alloc_Kelly  — base position sizing (e.g. LRI dimmer he_so from DecisionGuard)
  Confidence   — data completeness C from UncertaintyLayer
  U_shock      — independent fusion of Uncertainty U and Shock severity S:
                 U_shock = 1 - (1-U)·(1-S)

GOVERNANCE (orthogonality):
  - This layer outputs a SCALAR MULTIPLIER in [0,1]. It NEVER creates orders.
  - U (Uncertainty ≠ Signal) and S (Shock ≠ Macro Score) are fused with the
    independent-survival formula so the same stress is never double-counted.
  - SHOCK >= SHOCK_VETO_THRESHOLD → multiplier floor at 0.0 (entry veto).
  - LAW-008: MarginStressNode veto (CAPITAL_PRESERVATION) và authority_modifier
    được nạp qua governor.law_bridges → combine_alloc_multiplier.
"""

from __future__ import annotations

# Shock severity at which entries are VETOED (frozen). Mirrors the SHOCK band
# (0.70) of shock_detector: freezes buying during crisis, not normal drawdowns.
SHOCK_VETO_THRESHOLD = 0.70

# Backward-compat alias kept for callers that referenced the old SYSTEMIC rule.
SYSTEMIC_SHOCK_SEVERITY = 0.85


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def fuse_uncertainty_shock(uncertainty: float, shock: float) -> float:
    """Independent fusion: U_shock = 1 - (1-U)·(1-S).

    Treats U and S as independent risks; the combined chance both are absent
    is (1-U)·(1-S). No double-counting of the same stress.
    """
    u = _clamp01(uncertainty)
    s = _clamp01(shock)
    return 1.0 - (1.0 - u) * (1.0 - s)


def combine_alloc_multiplier(
    he_so: float,
    confidence: float,
    uncertainty: float,
    shock: float,
    authority_modifier: float = 1.0,
    veto: bool = False,
) -> float:
    """Fuse all risk inputs into a single allocation multiplier ∈ [0,1].

    Args:
        he_so: base Kelly sizing (LRI dimmer, 0.0=locked, 1.0=full).
        confidence: data completeness C from UncertaintyLayer ∈ [0,1].
        uncertainty: UncertaintyLayer U ∈ [0,1].
        shock: ShockDetector severity ∈ [0,1].
        authority_modifier: LAW-008 authority modifier from MarginStressNode
            ∈ [0,1] (1.0 = normal, decays toward 0.05 under elevated stress).
        veto: LAW-008 veto (MarginStressNode stress_index >= 0.80 →
            CAPITAL_PRESERVATION). Forces multiplier to 0.0.

    Returns:
        Final alloc_multiplier ∈ [0,1]. 0.0 = fully freeze this entry.
    """
    he_so_c = _clamp01(he_so)
    conf_c = _clamp01(confidence)
    mod_c = _clamp01(authority_modifier)
    u_shock = fuse_uncertainty_shock(uncertainty, shock)

    mult = he_so_c * conf_c * mod_c * (1.0 - u_shock)
    if veto or shock >= SHOCK_VETO_THRESHOLD:
        mult = 0.0
    return _clamp01(mult)
