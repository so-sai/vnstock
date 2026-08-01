"""epistemic_engine.py — Epistemic Engine (LAW-004 Dynamic Coverage & LAW-006 Causal Coherence).

WHY:
  - LAW-004 (Epistemic Authority):
      Coverage không chỉ là đếm số node có dữ liệu, mà được tính động theo:
      Coverage = ∑ Importance_i × Availability_i × Freshness_i × Reliability_i
      Nếu Coverage < 0.50, vị thế Kelly tối đa bị khống chế tự động ở mức ≤ 20% vốn.

  - LAW-006 (Causal Coherence / Evidence Consistency):
      Không chỉ đo độ lệch chuẩn thống kê (StdDev) giữa các node, mà kiểm tra tính
      đồng thuận dọc theo chuỗi nhân quả:
      Macro → Sector → Business → Health → Valuation → Behavior
      Nếu các nút mâu thuẫn chiều nhân quả (Macro CRISIS nhưng Technical BULLISH),
      Coherence giảm xuống, chiết khấu Conviction để chống tự tin thái quá.
"""

import math
import sys
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


class EpistemicEngine:
    """Epistemic authority and causal coherence engine."""

    def compute_coverage(self, nodes_data: Dict[str, Dict]) -> float:
        """LAW-004: Dynamic Coverage = ∑ Importance_i × Availability_i × Freshness_i × Reliability_i."""
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
            return 0.50

        return max(0.0, min(1.0, effective_coverage / total_importance))

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
