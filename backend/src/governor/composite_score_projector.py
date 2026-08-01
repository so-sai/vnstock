"""composite_score_projector.py — Composite Score Projector (Dual-Layer UI/UX Architecture).

WHY:
  Trong quản trị rủi ro tài chính, mô hình cộng điểm tuyến tính thuần túy dễ sập bẫy
  bù trừ (Linear Compensation Trap) — điểm kỹ thuật/nội tại bù đắp rủi ro vĩ mô khiến
  hệ thống đề xuất MUA đúng thời điểm giải chấp (Cross-margin Call).

  Giải pháp Kiến trúc 2 Lớp (Dual-Layer Architecture):
    1. Tầng Động Cơ (Bayesian Governor Engine): Tính toán P(Gain | Evidence) và áp
       dụng cờ phủ quyết (Veto Gate).
    2. Tầng Hiển Thị (CompositeScoreProjector): Ánh xạ phi tuyến (Non-linear Mapping)
       từ kết quả suy luận Bayes và cờ Veto sang Thang điểm 0–100 trực quan cho CLI & REST API.
"""

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


@dataclass
class CompositeScoreResult:
    """Output DTO of CompositeScoreProjector for one symbol."""
    symbol: str
    macro_score: float          # 0.0 - 20.0 pt
    internal_score: float       # 0.0 - 30.0 pt
    market_score: float         # 0.0 - 50.0 pt
    raw_score: float            # 0.0 - 100.0 pt (Linear sum)
    final_score: float          # 0.0 - 100.0 pt (After non-linear veto mapping)
    veto_flag: str              # NONE / OVERPRICED_VETO / CRISIS_VETO / MACRO_STRESS / DISTRESSED_VETO
    action: str                 # VETO / AVOID / REDUCE / WAIT / HOLD / SCALE_IN / OPEN
    recommendation: str         # MUA_TIC_LUY / CAN_BANG / GIAM_TY_TRONG / CAM_MUA


class CompositeScoreProjector:
    """Projector mapping BayesianMandate to a 0–100 Composite Score."""

    def project(self, mandate) -> CompositeScoreResult:
        symbol = getattr(mandate, "symbol", "UNKNOWN")
        action = getattr(mandate, "action", "WAIT")
        p_gain = getattr(mandate, "p_gain", 0.5)
        macro_state = getattr(mandate, "macro_state", "UNKNOWN")
        health_archetype = getattr(mandate, "health_archetype", "UNKNOWN")
        mos = getattr(mandate, "margin_of_safety", None)
        health_score = getattr(mandate, "contextual_health_score", 0.5)
        behavior_pos = getattr(mandate, "behavior_position", "NEUTRAL")
        cb_level = getattr(mandate, "circuit_breaker_level", 0)

        # 1. Macro Score (0 - 20 pt)
        macro_score = 20.0 * min(1.0, max(0.0, p_gain * 1.2))
        if macro_state in ("CREDIT_STRESS", "LIQUIDITY_CRUNCH", "CRISIS"):
            macro_score = min(macro_score, 8.0)

        # 2. Internal / Business Score (0 - 30 pt)
        base_internal = 30.0 * (health_score if health_score is not None else 0.5)
        if mos is not None:
            if mos > 50.0:
                mos_bonus = 5.0
            elif mos > 0.0:
                mos_bonus = 2.5
            elif mos < -50.0:
                mos_bonus = -15.0
            else:
                mos_bonus = -5.0
            base_internal += mos_bonus
        internal_score = max(0.0, min(30.0, base_internal))

        # 3. Market / Behavior Score (0 - 50 pt)
        market_score = 50.0 * max(0.0, min(1.0, p_gain))
        if behavior_pos == "BEARISH":
            market_score *= 0.6
        elif behavior_pos == "BULLISH":
            market_score *= 1.1
        market_score = max(0.0, min(50.0, market_score))

        raw_score = macro_score + internal_score + market_score

        # 4. Veto Gate & Non-linear Hard Caps
        veto_flag = "NONE"
        cap = 100.0

        if action == "VETO" or cb_level > 0:
            veto_flag = "CRISIS_VETO"
            cap = 30.0
        elif health_archetype == "DISTRESSED":
            veto_flag = "DISTRESSED_VETO"
            cap = 25.0
        elif mos is not None and mos < -50.0:
            veto_flag = "OVERPRICED_VETO"
            cap = 45.0
        elif macro_state in ("CREDIT_STRESS", "LIQUIDITY_CRUNCH"):
            veto_flag = "MACRO_STRESS"
            cap = 58.0

        final_score = round(min(raw_score, cap), 1)

        # Recommendation mapping
        if final_score >= 70.0 and veto_flag == "NONE":
            recommendation = "MUA_TIC_LUY (Scale In / Open)"
        elif final_score >= 50.0 and veto_flag == "NONE":
            recommendation = "CAN_BANG (Hold)"
        elif final_score >= 35.0:
            recommendation = "GIAM_TY_TRONG (Reduce / Wait)"
        else:
            recommendation = "CAM_MUA (Veto / Avoid)"

        return CompositeScoreResult(
            symbol=symbol,
            macro_score=round(macro_score, 1),
            internal_score=round(internal_score, 1),
            market_score=round(market_score, 1),
            raw_score=round(raw_score, 1),
            final_score=final_score,
            veto_flag=veto_flag,
            action=action,
            recommendation=recommendation,
        )

    def project_batch(self, mandates: List) -> List[CompositeScoreResult]:
        """Project a list of mandates and return sorted descending by final_score."""
        results = [self.project(m) for m in mandates]
        return sorted(results, key=lambda r: r.final_score, reverse=True)


def print_composite_dashboard(results: List[CompositeScoreResult]):
    """Print clean 0–100 Composite Score Dashboard for CLI."""
    print("\n  " + "=" * 90)
    print("  🎯 PTCK COMPOSITE SCORE & ACTION DASHBOARD (0 – 100 SCALE)")
    print("  " + "=" * 90)
    print(f"  {'Mã':<6} {'Macro(20)':>9} {'Internal(30)':>12} {'Market(50)':>11} "
          f"{'SCORE TOTAL':>13}   {'VETO FLAG':<16} {'KHUYẾN NGHỊ'}")
    print("  " + "─" * 90)
    for r in results:
        flag_str = f"⛔ {r.veto_flag}" if r.veto_flag != "NONE" else "🟢 OK"
        print(f"  {r.symbol:<6} {r.macro_score:>9.1f} {r.internal_score:>12.1f} {r.market_score:>11.1f} "
              f"  {r.final_score:>5.1f} / 100    {flag_str:<16} {r.recommendation}")
    print("  " + "=" * 90 + "\n")
