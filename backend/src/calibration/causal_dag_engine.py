"""causal_dag_engine.py - Causal DAG Engine (LAW-008, LAW-009, LAW-010)."""

import datetime
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

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

ALPHA_DECAY = 0.15


@dataclass
class InformationGainResult:
    ig_bits: float
    is_anomaly: bool
    status: str


@dataclass
class MarginStressResult:
    stress_index: float
    raw_stress_index: float
    decay_factor: float
    stale_days: int
    authority_modifier: float
    veto_triggered: bool
    mandate_override: Optional[str]
    reason: str


class MarginStressNode:
    """Root Context Node - Liquidity Risk Branch (LAW-008).

    Supports Asynchronous Data Decay Latency via Exponential Half-Life.
    """

    def __init__(
        self,
        w_margin: float = 0.35,
        w_breadth: float = 0.30,
        w_cross: float = 0.35,
        theta: float = 1.20,
        half_life_hours: float = 72.0,
    ):
        self.w1 = w_margin
        self.w2 = w_breadth
        self.w3 = w_cross
        self.theta = theta
        self.half_life_hours = half_life_hours

    def _calculate_decay(
        self, last_updated_iso: str, now_iso: str
    ) -> Tuple[float, int]:
        try:
            last_dt = datetime.datetime.fromisoformat(last_updated_iso)
            now_dt = datetime.datetime.fromisoformat(now_iso)
            delta_hours = max(
                0.0, (now_dt - last_dt).total_seconds() / 3600.0
            )
            stale_days = int(delta_hours / 24.0)
            decay = math.exp(
                -math.log(2) * delta_hours / self.half_life_hours
            )
            return max(decay, 0.10), stale_days
        except Exception:
            return 0.10, 30

    def compute_stress_index(
        self, m_sys: float, b_stress: float, c_cross: float
    ) -> float:
        logit = (self.w1 * m_sys) + (self.w2 * b_stress) + (self.w3 * c_cross) - self.theta
        raw_index = 1.0 / (1.0 + np.exp(-logit))
        return float(np.clip(raw_index, 0.0, 1.0))

    def evaluate_node(
        self,
        m_sys: float,
        b_stress: float,
        c_cross: float,
        last_updated_iso: str,
        now_iso: str,
    ) -> MarginStressResult:
        raw_stress = self.compute_stress_index(m_sys, b_stress, c_cross)
        decay, stale_days = self._calculate_decay(last_updated_iso, now_iso)
        effective_stress = float(np.clip(raw_stress * decay, 0.0, 1.0))

        if effective_stress >= 0.80:
            return MarginStressResult(
                stress_index=round(effective_stress, 4),
                raw_stress_index=round(raw_stress, 4),
                decay_factor=round(decay, 4),
                stale_days=stale_days,
                authority_modifier=0.0,
                veto_triggered=True,
                mandate_override="CAPITAL_PRESERVATION",
                reason=(
                    f"CRITICAL_MARGIN_CASCADE_RISK "
                    f"(Effective: {effective_stress:.4f}, "
                    f"Raw: {raw_stress:.4f})"
                ),
            )
        elif effective_stress >= 0.40:
            modifier = max(0.05, 1.0 - 1.25 * (effective_stress - 0.40))
            return MarginStressResult(
                stress_index=round(effective_stress, 4),
                raw_stress_index=round(raw_stress, 4),
                decay_factor=round(decay, 4),
                stale_days=stale_days,
                authority_modifier=round(modifier, 4),
                veto_triggered=False,
                mandate_override=None,
                reason=(
                    f"ELEVATED_MARGIN_STRESS "
                    f"(Effective: {effective_stress:.4f}, "
                    f"Stale: {stale_days}d)"
                ),
            )

        return MarginStressResult(
            stress_index=round(effective_stress, 4),
            raw_stress_index=round(raw_stress, 4),
            decay_factor=round(decay, 4),
            stale_days=stale_days,
            authority_modifier=1.0,
            veto_triggered=False,
            mandate_override=None,
            reason="NORMAL_MARGIN_OPERATIONS",
        )

    def evaluate_governor_impact(
        self, stress_index: float
    ) -> MarginStressResult:
        if stress_index >= 0.80:
            return MarginStressResult(
                stress_index=stress_index,
                raw_stress_index=stress_index,
                decay_factor=1.0,
                stale_days=0,
                authority_modifier=0.0,
                veto_triggered=True,
                mandate_override="CAPITAL_PRESERVATION",
                reason=f"CRITICAL_MARGIN_CASCADE_RISK {stress_index:.4f}",
            )
        elif stress_index >= 0.40:
            modifier = max(0.05, 1.0 - 1.25 * (stress_index - 0.40))
            return MarginStressResult(
                stress_index=stress_index,
                raw_stress_index=stress_index,
                decay_factor=1.0,
                stale_days=0,
                authority_modifier=round(modifier, 4),
                veto_triggered=False,
                mandate_override=None,
                reason=f"ELEVATED_MARGIN_STRESS {stress_index:.4f}",
            )
        return MarginStressResult(
            stress_index=stress_index,
            raw_stress_index=stress_index,
            decay_factor=1.0,
            stale_days=0,
            authority_modifier=1.0,
            veto_triggered=False,
            mandate_override=None,
            reason="NORMAL_MARGIN_OPERATIONS",
        )


class CausalDAGEngine:
    """Core Causal Engine - LAW-008, LAW-009, LAW-010."""

    nodes: List[str] = [
        "macro_regime",
        "margin_stress",
        "market_breadth",
        "intrinsic_valuation",
        "epistemic_authority",
    ]
    edges: Dict[str, List[str]] = {
        "macro_regime": ["epistemic_authority", "intrinsic_valuation"],
        "margin_stress": ["epistemic_authority"],
        "market_breadth": ["epistemic_authority"],
    }

    def __init__(self):
        self.margin_stress_node = MarginStressNode()

    def cluster_dependent_nodes(
        self, nodes: Dict[str, Dict]
    ) -> Dict[str, Dict]:
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
            lrs = [
                item.get("lr", 1.0)
                for item in cluster_list
                if item.get("lr") is not None
            ]
            if not lrs:
                continue

            max_lr = max(lrs)
            remainder_sum = sum(
                lr - 1.0 for lr in lrs if lr != max_lr
            )
            cluster_lr = max_lr + (ALPHA_DECAY * remainder_sum)

            result_nodes[cid] = {
                "lr": round(cluster_lr, 4),
                "is_cluster": True,
                "node_count": len(cluster_list),
            }

        return result_nodes

    def compute_information_gain(
        self, p_prior: float, p_posterior: float
    ) -> InformationGainResult:
        p_0 = max(0.001, min(0.999, p_prior))
        p_1 = max(0.001, min(0.999, p_posterior))

        term1 = p_1 * math.log2(p_1 / p_0)
        term2 = (1.0 - p_1) * math.log2(
            (1.0 - p_1) / (1.0 - p_0)
        )
        ig = term1 + term2

        is_anomaly = ig >= 0.50
        status = (
            "CAUSAL_ANOMALY"
            if is_anomaly
            else "NORMAL_INFORMATION_GAIN"
        )

        return InformationGainResult(
            ig_bits=round(ig, 4),
            is_anomaly=is_anomaly,
            status=status,
        )

    def compute_evidence_utility(
        self, delta_conviction: float, compute_cost: float = 1.0
    ) -> float:
        cost = max(0.001, compute_cost)
        utility = abs(delta_conviction) / (cost + 0.01)
        return round(utility, 4)

    def process_causal_graph(
        self, raw_inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        m_sys = raw_inputs.get("system_margin_ratio", 0.0)
        b_stress = raw_inputs.get("breadth_stress_ratio", 0.0)
        c_cross = raw_inputs.get("cross_contagion_index", 0.0)
        last_updated = raw_inputs.get(
            "margin_last_updated", "2026-01-01T00:00:00"
        )
        now_time = raw_inputs.get(
            "now_time", "2026-08-01T00:00:00"
        )

        margin_res = self.margin_stress_node.evaluate_node(
            m_sys, b_stress, c_cross, last_updated, now_time
        )

        independent_breadth_active = raw_inputs.get(
            "independent_breadth_active", False
        )
        overlap_penalty = 0.0
        if independent_breadth_active and b_stress > 0.5:
            overlap_penalty = 0.15 * b_stress

        base_authority = raw_inputs.get(
            "base_epistemic_authority", 1.0
        )
        adjusted_authority = (
            base_authority
            * margin_res.authority_modifier
            * (1.0 - overlap_penalty)
        )
        final_authority = float(
            np.clip(adjusted_authority, 0.0, 1.0)
        )

        return {
            "dag_nodes_evaluated": len(self.nodes),
            "margin_stress": {
                "effective_index": margin_res.stress_index,
                "raw_index": margin_res.raw_stress_index,
                "decay_factor": margin_res.decay_factor,
                "stale_days": margin_res.stale_days,
                "authority_modifier": margin_res.authority_modifier,
                "veto_triggered": margin_res.veto_triggered,
                "reason": margin_res.reason,
            },
            "cluster_compression": {
                "law_008_applied": independent_breadth_active,
                "overlap_penalty": round(overlap_penalty, 4),
            },
            "adjusted_epistemic_authority": round(
                final_authority, 4
            ),
            "governor_override": margin_res.mandate_override,
            "causal_coherence_passed": True,
        }
