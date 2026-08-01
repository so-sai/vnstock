"""epistemic_engine.py — Epistemic Engine (LAW-004, LAW-006, LAW-008, Surprise Engine).

WHY:
  - LAW-004 (Epistemic Authority):
      Coverage = ∑ Importance_i × Availability_i × Freshness_i × Reliability_i

  - LAW-006 (Causal Coherence / Evidence Consistency):
      Đo tính nhất quán dọc theo chuỗi nhân quả DAG:
      Macro → Sector → Business → Health → Valuation → Behavior

  - LAW-008 (Open World Principle / Unknown Unknowns):
      Không bao giờ giả định mô hình hoàn hảo (Coverage max = 0.90, luôn dành 10% cho Unknown Unknowns).
      Epistemic Alloc Factor = Coverage × Coherence × OpenWorldDiscount.

  - Surprise Engine (Động cơ đo độ bất ngờ Epistemic Surprise):
      Shannon Surprise S = -log2(P(Outcome | Prediction)).
      Khi P_predict = 0.90 nhưng Outcome = Fail → S >= 3.32 bits (HIGH SURPRISE / ANOMALY)
      -> Báo hiệu có biến ẩn chưa được mô hình hóa, kích hoạt ModelRegistry re-weighting.
"""

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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


COVERAGE_MAX = 0.90             # LAW-008: Max coverage capped at 90% (10% reserved for Unknown Unknowns)
OPEN_WORLD_DISCOUNT = 0.90      # LAW-008: Open world discount multiplier


@dataclass
class SurpriseResult:
    """Output DTO of Surprise Engine."""
    surprise_bits: float
    p_outcome: float
    is_high_surprise: bool
    status: str                 # HIGH_SURPRISE_ANOMALY / NORMAL_SURPRISE


class EpistemicEngine:
    """Epistemic authority, causal coherence, and surprise engine."""

    def compute_coverage(self, nodes_data: Dict[str, Dict]) -> float:
        """LAW-004 & LAW-008: Dynamic Coverage capped at COVERAGE_MAX (0.90)."""
        total_importance = 0.0
        effective_coverage = 0.0

        for nid, info in nodes_data.items():
            imp = info.get("importance", 0.20)
            avail = 1.0 if info.get("available", False) else 0.0
            fresh = max(0.0, min(1.0, info.get("freshness", 1.0)))
            rel = max(0.0, min(1.0, info.get("reliability", 0.90)))

            total_importance += imp
            effective_coverage += imp * avail * fresh * rel

        if total_importance <= 0.0:
            raw_coverage = 0.50
        else:
            raw_coverage = effective_coverage / total_importance

        # LAW-008: Hard cap at COVERAGE_MAX = 0.90 (10% for Unknown Unknowns)
        return max(0.0, min(COVERAGE_MAX, raw_coverage))

    def compute_causal_coherence(self, causal_chain: List[Dict]) -> float:
        """LAW-006: Causal Coherence along the DAG (Macro -> Sector -> Health -> Behavior)."""
        if not causal_chain or len(causal_chain) < 2:
            return 1.0

        # Check directional alignment
        directions = [node.get("direction", "NEUTRAL") for node in causal_chain if node.get("direction") != "NEUTRAL"]
        if not directions:
            return 0.80

        bullish_count = sum(1 for d in directions if d == "BULLISH")
        bearish_count = sum(1 for d in directions if d == "BEARISH")
        total = len(directions)

        # Dominant agreement ratio
        alignment_ratio = max(bullish_count, bearish_count) / float(total)

        # Log-likelihood ratio dispersion penalty
        lrs = [node.get("lr", 1.0) for node in causal_chain if node.get("lr") is not None]
        lr_std = 0.0
        if len(lrs) >= 2:
            mean_lr = sum(lrs) / len(lrs)
            variance = sum((x - mean_lr) ** 2 for x in lrs) / len(lrs)
            lr_std = math.sqrt(variance)

        # Non-linear coherence mapping
        coherence = (0.8 * alignment_ratio) + (0.2 * math.exp(-0.5 * lr_std))
        return max(0.0, min(1.0, coherence))

    def compute_surprise(self, p_predict: float, y_outcome: int) -> SurpriseResult:
        """Surprise Engine: Shannon Surprise S = -log2(P(Outcome | Prediction))."""
        p = max(0.001, min(0.999, p_predict))
        p_act = p if y_outcome == 1 else (1.0 - p)

        # Shannon Surprise S = -log2(P(Outcome))
        s_bits = -math.log2(p_act)
        is_high = s_bits >= 2.5
        status = "HIGH_SURPRISE_ANOMALY" if is_high else "NORMAL_SURPRISE"

        return SurpriseResult(
            surprise_bits=round(s_bits, 2),
            p_outcome=round(p_act, 4),
            is_high_surprise=is_high,
            status=status,
        )

    def compute_alloc_factor(self, nodes_data: Dict[str, Dict], causal_chain: List[Dict]) -> float:
        """LAW-004 + LAW-006 + LAW-008: Epistemic Alloc Factor = Coverage * Coherence * OpenWorldDiscount."""
        cov = self.compute_coverage(nodes_data)
        coh = self.compute_causal_coherence(causal_chain)
        factor = cov * coh * OPEN_WORLD_DISCOUNT
        return max(0.0, min(0.81, factor))
