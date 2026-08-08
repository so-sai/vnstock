"""law_bridges.py — nối UncertaintyLayer + ShockDetector vào engine LAW có sẵn.

Thay vì commit 2 module mới như hệ thống song song (trùng mục đích với code
đã tồn tại), file này định vị chúng là DATA-PROVIDER cho 2 engine ĐÃ CÓ trong
`calibration` nhưng CHƯA được nối vào production (chỉ test pass):

  - ShockDetector  (governor.shock_detector)      → MarginStressNode  (LAW-008)
  - UncertaintyLayer (governor.uncertainty_layer) → EpistemicEngine.compute_coverage (LAW-004/008)

No provenance = no trust: mọi hàm đều trả kèm `provenance` chỉ rõ engine LAW
được dùng. Bridge KHÔNG tạo order — chỉ sản xuất risk signal cho Governor.
"""

from __future__ import annotations

from typing import Any

from calibration.causal_dag_engine import MarginStressNode
from calibration.epistemic_engine import EpistemicEngine
from governor.shock_detector import (
    ShockScore,
    detect_shock,
    stress_fraction,
)
from governor.uncertainty_layer import (
    UncertaintyResult,
    compute_uncertainty,
)

# Importance cố định đều cho 4 macro node (4 × 0.25 = 1.0).
# Trọng số bình đẳng — tránh tự gán importance thiên lệch.
IMPORTANCE_PER_NODE = 0.25


# ── Bridge 1: ShockDetector → MarginStressNode (LAW-008) ──────────────────


def shock_to_margin_inputs(shock: ShockScore) -> dict[str, Any]:
    """Map ShockScore 5 components → raw_inputs cho MarginStressNode.evaluate_node.

    Mapping trung thực:
      system_margin_ratio ← stress_fraction(-Z_liq) — proxy thanh khoản
        (volume collapse). KHÔNG phải margin thật; khai báo rõ trong provenance.
      breadth_stress_ratio ← stress_fraction(-Z_breadth) — khớp bản chất.
      cross_contagion_index ← 0.0 — chưa có phép đo; không bịa số.
    """
    z = shock.z_scores or {}
    b_stress = stress_fraction(-float(z.get("breadth", 0.0)))
    m_sys = stress_fraction(-float(z.get("liquidity", 0.0)))
    iso = f"{shock.target_date}T00:00:00"
    return {
        "system_margin_ratio": round(m_sys, 4),
        "breadth_stress_ratio": round(b_stress, 4),
        "cross_contagion_index": 0.0,
        "margin_last_updated": iso,
        "now_time": iso,
        "provenance": {
            "system_margin_ratio": "shock_detector.liquidity Z → stress_fraction (volume-collapse proxy)",
            "breadth_stress_ratio": "shock_detector.breadth Z → stress_fraction",
            "cross_contagion_index": "NOT_MEASURED (0.0)",
            "last_updated": f"{shock.target_date} (data mới nhất tại target_date → decay=1.0)",
        },
    }


# ── Bridge 2: UncertaintyLayer → EpistemicEngine (LAW-004/008) ────────────


def uncertainty_to_nodes_data(u: UncertaintyResult) -> dict[str, dict[str, float]]:
    """Map UncertaintyResult per_node_components → nodes_data cho EpistemicEngine.compute_coverage.

    EpistemicEngine.compute_coverage cần mỗi node: {importance, available,
    freshness, reliability}. Component trust của UncertaintyLayer map 1-1:
      C (completeness) → available
      F (freshness)    → freshness
      R (reliability)  → reliability
    A (agreement) và P (persistence) giữ ở UncertaintyLayer — EpistemicEngine
    không có slot tương ứng nên KHÔNG ép nhét (tránh hallucination).
    """
    nodes: dict[str, dict[str, float]] = {}
    for node, comp in (u.per_node_components or {}).items():
        nodes[node] = {
            "importance": IMPORTANCE_PER_NODE,
            "available": float(comp.get("C", 0.0)),
            "freshness": float(comp.get("F", 0.0)),
            "reliability": float(comp.get("R", 0.0)),
        }
    return nodes


# ── Orchestration: law_governor_signal ────────────────────────────────────


def law_governor_signal(
    target_date: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Tổng hợp risk signal từ 2 engine LAW có sẵn, nạp bằng 2 data-provider mới.

    Returns:
        dict gồm:
          shock / uncertainty          — giá trị thô từ 2 detector
          stress_index / authority_modifier / veto_triggered / mandate_override
                                     — từ MarginStressNode (LAW-008)
          coverage                     — từ EpistemicEngine (LAW-004/008)
          provenance                   — nguồn chính xác từng giá trị
    """
    shock = detect_shock(target_date, db_path)
    u_result = compute_uncertainty(target_date, db_path)

    raw = shock_to_margin_inputs(shock)
    margin = MarginStressNode().evaluate_node(
        raw["system_margin_ratio"],
        raw["breadth_stress_ratio"],
        raw["cross_contagion_index"],
        raw["margin_last_updated"],
        raw["now_time"],
    )

    nodes = uncertainty_to_nodes_data(u_result)
    coverage = EpistemicEngine().compute_coverage(nodes)

    return {
        "target_date": target_date,
        "shock": round(shock.severity, 4),
        "uncertainty": round(u_result.u, 4),
        "stress_index": round(margin.stress_index, 4),
        "raw_stress_index": round(margin.raw_stress_index, 4),
        "authority_modifier": round(margin.authority_modifier, 4),
        "veto_triggered": margin.veto_triggered,
        "mandate_override": margin.mandate_override,
        "reason": margin.reason,
        "coverage": round(coverage, 4),
        "provenance": {
            "margin_stress": "causal_dag_engine.MarginStressNode (LAW-008)",
            "coverage": "epistemic_engine.EpistemicEngine.compute_coverage (LAW-004/008)",
            "shock_inputs": raw["provenance"],
        },
    }
