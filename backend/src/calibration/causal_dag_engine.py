"""causal_dag_engine.py — Causal DAG Engine (LAW-008, LAW-009, LAW-010).

WHY:
  - LAW-008 (Causal Independence Principle):
      Gom nhóm các nút bằng chứng thuộc cùng một chuỗi nhân quả (như Fed -> DXY -> USD/VND -> SBV).
      Nén (Collapse) cụm thành 1 Likelihood Ratio duy nhất theo công thức:
      LR_cluster = max(LR_i) + α * ∑(LR_j - 1.0)  (với α = 0.15)
      Triệt tiêu 100% rủi ro đếm trùng lặp thông tin (Double Counting Trap).

  - LAW-009 (Information Gain Principle):
      Đo lường lượng thông tin bổ sung bằng KL-Divergence giữa p_prior và p_posterior:
      IG = p_post * log2(p_post / p_prior) + (1 - p_post) * log2((1 - p_post) / (1 - p_prior))
      Khi IG >= 0.50 bits -> Bật cờ CAUSAL_ANOMALY (Thực tại đi ngược kỳ vọng chuỗi nhân quả).

  - LAW-010 (Evidence Cost Principle):
      Định lượng Chỉ số Hữu dụng Utility = ΔConviction / (Cost + 0.01).
      Ưu tiên bằng chứng có hiệu năng cao, chi phí thu thập thấp.
"""

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


ALPHA_DECAY = 0.15      # Hệ số suy hao thông tin trùng lặp trong cụm nhân quả


@dataclass
class InformationGainResult:
    ig_bits: float
    is_anomaly: bool
    status: str


class CausalDAGEngine:
    """Engine implementing Causal Independence (LAW-008), Information Gain (LAW-009), and Evidence Utility (LAW-010)."""

    def cluster_dependent_nodes(self, nodes: Dict[str, Dict]) -> Dict[str, Dict]:
        """LAW-008: Gom nhóm các nút thuộc cùng chuỗi nhân quả để tránh đếm trùng Double Counting."""
        clusters: Dict[str, List[Dict]] = {}
        unclustered: Dict[str, Dict] = {}

        for nid, info in nodes.items():
            cluster_id = info.get("cluster")
            if cluster_id:
                clusters.setdefault(cluster_id, []).append(info)
            else:
                unclustered[nid] = info

        result_nodes = dict(unclustered)

        for cid, cluster_list in clusters.items():
            lrs = [item.get("lr", 1.0) for item in cluster_list if item.get("lr") is not None]
            if not lrs:
                continue

            max_lr = max(lrs)
            remainder_sum = sum(lr - 1.0 for lr in lrs if lr != max_lr)
            cluster_lr = max_lr + (ALPHA_DECAY * remainder_sum)

            result_nodes[cid] = {
                "lr": round(cluster_lr, 4),
                "is_cluster": True,
                "node_count": len(cluster_list),
            }

        return result_nodes

    def compute_information_gain(self, p_prior: float, p_posterior: float) -> InformationGainResult:
        """LAW-009: Calculate KL-Divergence / Information Gain (bits) between Prior & Posterior."""
        p_0 = max(0.001, min(0.999, p_prior))
        p_1 = max(0.001, min(0.999, p_posterior))

        # KL-Divergence D_KL(P_1 || P_0)
        term1 = p_1 * math.log2(p_1 / p_0)
        term2 = (1.0 - p_1) * math.log2((1.0 - p_1) / (1.0 - p_0))
        ig = term1 + term2

        is_anomaly = ig >= 0.50
        status = "CAUSAL_ANOMALY" if is_anomaly else "NORMAL_INFORMATION_GAIN"

        return InformationGainResult(
            ig_bits=round(ig, 4),
            is_anomaly=is_anomaly,
            status=status,
        )

    def compute_evidence_utility(self, delta_conviction: float, compute_cost: float = 1.0) -> float:
        """LAW-010: Compute Evidence Utility Index = ΔConviction / (Cost + 0.01)."""
        cost = max(0.001, compute_cost)
        utility = abs(delta_conviction) / (cost + 0.01)
        return round(utility, 4)
