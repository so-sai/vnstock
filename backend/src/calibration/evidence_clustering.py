"""evidence_clustering.py — Evidence Independence (LAW: không đếm vote, đếm cụm).

Năm model cùng nhìn USD/VND → chỉ tính 1 evidence cluster, không phải 5.
Nguyên tắc: `n_independent_evidence` đếm CỤM bằng chứng độc lập, không đếm
model hay số cột dữ liệu.

Nhóm các evidence states của Governor thành cụm độc lập (mỗi cụm một nguồn
causal riêng):
  macro       macro_state + transmission_phase   [regional_influence_engine, macro_lag_engine]
  sector      sector_phase                        [sector_exposure_matrix]
  fundamental health_archetype + valuation_zone   [company_state, fair_multiple_engine]
  behavior    behavior_position                   [behavior engine]
  shock       (optional) shock band               [shock_detector]
  coverage    (optional) epistemic coverage       [law_bridges / epistemic_engine]

Mỗi cụm giữ raw provenance immutable: states + sources — để sau này trả lời
"tại sao ngày X hệ thống ra BUY" mà không cần chạy lại engine.
"""

from __future__ import annotations

# Phiên bản clustering — đổi mapping → bump version để ledger biết thế hệ.
CLUSTER_VERSION = "clustering-v1"

NEUTRAL_VALUES = {"", "UNKNOWN", "N/A", "NEUTRAL", "NA", "UNKNOWN_VAL"}

# Cụm → (evidence_state_keys, sources)
CLUSTER_DEF = {
    "macro": (("macro_state", "transmission_phase"), ("regional_influence_engine", "macro_lag_engine")),
    "sector": (("sector_phase",), ("sector_exposure_matrix",)),
    "fundamental": (("health_archetype", "valuation_zone"), ("company_state", "fair_multiple_engine")),
    "behavior": (("behavior_position",), ("behavior_engine",)),
}


def _is_active(value) -> bool:
    if value is None:
        return False
    s = str(value).strip().upper()
    return s not in NEUTRAL_VALUES


def cluster_evidence(
    evidence_states: dict,
    shock_band: str | None = None,
    coverage: float | None = None,
) -> dict:
    """Nhóm evidence states thành cụm độc lập + đếm số cụm active.

    Returns:
      {
        "clusters": {name: {"states": {...}, "sources": [...]}},
        "n_independent_evidence": int,
        "decision_quality": "Strong"|"Mixed"|"Weak",
        "version": "clustering-v1"
      }
    """
    states = evidence_states or {}
    clusters: dict = {}

    for name, (keys, sources) in CLUSTER_DEF.items():
        active_vals = {k: states.get(k) for k in keys if _is_active(states.get(k))}
        clusters[name] = {
            "states": active_vals,
            "sources": list(sources),
        }

    if shock_band is not None:
        clusters["shock"] = {
            "states": {"shock_band": shock_band} if _is_active(shock_band) else {},
            "sources": ["shock_detector"],
        }
    if coverage is not None:
        clusters["coverage"] = {
            "states": {"coverage": coverage},
            "sources": ["law_bridges", "epistemic_engine"],
        }

    n_independent = sum(1 for c in clusters.values() if c["states"])
    quality = _quality(n_independent)

    return {
        "clusters": clusters,
        "n_independent_evidence": n_independent,
        "decision_quality": quality,
        "version": CLUSTER_VERSION,
    }


def _quality(n_independent: int) -> str:
    if n_independent >= 4:
        return "Strong"
    if n_independent >= 2:
        return "Mixed"
    return "Weak"
