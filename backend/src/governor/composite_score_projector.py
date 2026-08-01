"""composite_score_projector.py — Composite Score Projector (Dual-Layer UI/UX Architecture).

WHY:
  Trong quản trị rủi ro tài chính, mô hình cộng điểm tuyến tính thuần túy dễ sập bẫy
  bù trừ (Linear Compensation Trap) — điểm kỹ thuật/nội tại bù đắp rủi ro vĩ mô khiến
  hệ thống đề xuất MUA đúng thời điểm giải chấp (Cross-margin Call).

  Giải pháp Kiến trúc 2 Lớp (Dual-Layer Architecture):
    1. Tầng Động Cơ (Bayesian Governor Engine): Tính toán P(Gain | Evidence) và áp
       dụng cờ phủ quyết (Veto Gate).
    2. Tầng Ánh Xạ & Policy (CompositeScoreProjector & ExecutionPolicy):
       - Tách biệt Target Capital Allocation % (0-100%) và Delta Position Adjustment.
       - Tách bạch Policy Layer (Conservative / Balanced / Aggressive).
       - Hiển thị Coverage (Bao phủ LAW-004) & Coherence (Đồng thuận LAW-006) trực quan trên CLI Dashboard.
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
class ExecutionPolicy:
    """Decoupled Execution Policy determining Buy & Full Margin thresholds."""
    name: str = "BALANCED"
    buy_threshold: float = 70.0
    full_margin_threshold: float = 85.0


POLICIES = {
    "CONSERVATIVE": ExecutionPolicy("CONSERVATIVE", buy_threshold=75.0, full_margin_threshold=90.0),
    "BALANCED": ExecutionPolicy("BALANCED", buy_threshold=70.0, full_margin_threshold=85.0),
    "AGGRESSIVE": ExecutionPolicy("AGGRESSIVE", buy_threshold=65.0, full_margin_threshold=80.0),
}


@dataclass
class CompositeScoreResult:
    """Output DTO of CompositeScoreProjector for one symbol."""
    symbol: str
    macro_score: float          # 0.0 - 20.0 pt
    internal_score: float       # 0.0 - 30.0 pt
    market_score: float         # 0.0 - 50.0 pt
    raw_score: float            # 0.0 - 100.0 pt (Linear sum)
    final_score: float          # 0.0 - 100.0 pt (After non-linear veto mapping)
    coverage: float             # Evidence Coverage (0.0 - 1.0)
    coherence: float            # Causal Coherence (0.0 - 1.0)
    buy_gap: float              # Points needed to reach Buy Threshold (0.0 = in buy zone)
    allocation_pct: float       # Target Capital Allocation % [0%, 100%]
    delta_pct: float            # Position Delta Adjustment %
    veto_flag: str              # NONE / OVERPRICED_VETO / CRISIS_VETO / MACRO_STRESS / DISTRESSED_VETO
    action: str                 # VETO / AVOID / REDUCE / WAIT / HOLD / SCALE_IN / OPEN
    recommendation: str         # MUA_TIC_LUY / CAN_BANG / GIAM_TY_TRONG / CAM_MUA


class CompositeScoreProjector:
    """Projector mapping BayesianMandate to a 0–100 Composite Score."""

    def __init__(self, policy: Optional[ExecutionPolicy] = None):
        self.policy = policy or POLICIES["BALANCED"]

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
        alloc_raw = getattr(mandate, "allocation_pct", 0.0)

        # Target allocation is non-negative [0%, 100%]
        target_alloc = max(0.0, alloc_raw)
        delta_pct = alloc_raw

        # Coverage & Coherence metrics
        coverage = getattr(mandate, "coverage", 0.85)
        coherence = getattr(mandate, "coherence", 0.88)

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
        buy_gap = round(max(0.0, self.policy.buy_threshold - final_score), 1)

        # Recommendation mapping
        if final_score >= self.policy.buy_threshold and veto_flag == "NONE":
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
            coverage=round(coverage, 2),
            coherence=round(coherence, 2),
            buy_gap=buy_gap,
            allocation_pct=round(target_alloc, 1),
            delta_pct=round(delta_pct, 1),
            veto_flag=veto_flag,
            action=action,
            recommendation=recommendation,
        )

    def project_batch(self, mandates: List) -> List[CompositeScoreResult]:
        """Project a list of mandates and return sorted descending by final_score."""
        results = [self.project(m) for m in mandates]
        return sorted(results, key=lambda r: r.final_score, reverse=True)


def print_composite_dashboard(results: List[CompositeScoreResult], policy_name: str = "BALANCED"):
    """Print clean 0–100 Composite Score Dashboard for CLI in Parallel Bilingual (Việt - Anh) format."""
    from src.utils.cli_theme import c_red, c_green, c_yellow, c_cyan, c_dim

    print("\n  " + "=" * 145)
    print(f"  🎯 {c_cyan('PTCK EPISTEMIC COMPOSITE SCORE & ACTION DASHBOARD (0 – 100 SCALE)')} | POLICY: {c_yellow(policy_name)}")
    print("  " + "=" * 145)
    print(f"  {'Symbol (Mã)':<10} {'Macro(20)':>9} {'Internal(30)':>12} {'Market(50)':>11} "
          f"{'Score (100)':>13}   {'Coverage':>9} {'Coherence':>10}   {'Target Alloc':>12}   {'Action Delta':>13}   {'Buy Gap (70+)':>13}   {'Veto Flag':<16} {'Recommendation (Khuyến nghị)'}")
    print("  " + "─" * 145)
    for r in results:
        if r.veto_flag in ("CRISIS_VETO", "OVERPRICED_VETO", "DISTRESSED_VETO", "HARD_VETO"):
            flag_str = c_red(f"⛔ {r.veto_flag}")
        elif r.veto_flag in ("MACRO_STRESS", "SOFT_VETO"):
            flag_str = c_yellow(f"⚠️ {r.veto_flag}")
        else:
            flag_str = c_green("🟢 OK")

        # Format Action Delta (Hành động)
        if r.delta_pct < 0.0:
            delta_str = c_yellow(f"Reduce {r.delta_pct:>+5.1f}%")
        elif r.delta_pct > 0.0:
            delta_str = c_green(f"  Buy  {r.delta_pct:>+5.1f}%")
        else:
            delta_str = c_dim("  Hold   0.0%")

        if r.final_score >= 70.0 and r.veto_flag == "NONE":
            score_str = c_green(f"{r.final_score:>5.1f}")
            rec_str = c_green(r.recommendation)
            gap_str = c_green("  IN BUY ZONE")
            alloc_str = c_green(f"{r.allocation_pct:>5.1f}%")
        elif r.final_score >= 50.0 and r.veto_flag == "NONE":
            score_str = c_yellow(f"{r.final_score:>5.1f}")
            rec_str = c_yellow(r.recommendation)
            gap_str = c_yellow(f"    +{r.buy_gap:>4.1f} pt")
            alloc_str = c_yellow(f"{r.allocation_pct:>5.1f}%")
        elif r.final_score >= 35.0:
            score_str = c_yellow(f"{r.final_score:>5.1f}")
            rec_str = c_yellow(r.recommendation)
            gap_str = c_yellow(f"    +{r.buy_gap:>4.1f} pt")
            alloc_str = c_yellow(f"{r.allocation_pct:>5.1f}%")
        else:
            score_str = c_red(f"{r.final_score:>5.1f}")
            rec_str = c_red(r.recommendation)
            gap_str = c_red(f"    +{r.buy_gap:>4.1f} pt")
            alloc_str = c_red(f"{r.allocation_pct:>5.1f}%")

        sym_str = c_cyan(r.symbol) if r.final_score >= 50.0 else r.symbol
        cov_str = c_dim(f"{r.coverage:.0%}")
        coh_str = c_dim(f"{r.coherence:.0%}")

        print(f"  {sym_str:<10} {r.macro_score:>9.1f} {r.internal_score:>12.1f} {r.market_score:>11.1f} "
              f"  {score_str} / 100   {cov_str:>8} {coh_str:>10}   {alloc_str:>12}   {delta_str:>13}   {gap_str:<13}    {flag_str:<16} {rec_str}")
    print("  " + "=" * 145 + "\n")
