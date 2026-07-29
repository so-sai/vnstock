"""counterfactual/engine.py — P5 Counterfactual Reasoning.

Given a BayesianGovernor assessment, asks 'What if?' for each evidence node,
reports the P(Gain) delta, and identifies leverage points.

Usage:
    from src.counterfactual.engine import CounterfactualEngine
    engine = CounterfactualEngine()
    report = engine.analyze(["FPT", "VCB"])
    print_counterfactual_report(report)
"""

import math
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Reuse Bayesian internals from Governor
from src.governor.company_state import (
    BayesianGovernor, BayesianMandate,
    PRIOR_ODDS, EVIDENCE_WEIGHTS,
    LR_MACRO, LR_TRANSMISSION, LR_SECTOR,
    LR_HEALTH, LR_VALUATION, LR_BEHAVIOR,
    _lookup_lr, compute_gain_probability,
    compute_expected_utilities, pick_best_action,
    kelly_allocation, ACTION_VN,
)


# ── Best / Worst values for each evidence node ──────────────
BEST_EVIDENCE = {
    "macro": "STABLE",
    "transmission": "HEALTHY_TRANSMISSION",
    "sector": "MID",
    "health": "HIGH_QUALITY_COMPOUNDER",
    "valuation": "ULTRA_CHEAP",
    "behavior": "IN_VA_DEMAND",
}

WORST_EVIDENCE = {
    "macro": "CREDIT_STRESS",
    "transmission": "CREDIT_CRUNCH",
    "sector": "WEAKENING",
    "health": "DISTRESSED",
    "valuation": "ULTRA_EXPENSIVE",
    "behavior": "ABOVE_VA",
}

# ── Named scenarios ─────────────────────────────────────────
SCENARIOS = {
    "STABLE_MACRO": {
        "label": "Vĩ mô ổn định",
        "desc": "Chuyển CREDIT_STRESS → STABLE, LIQUIDITY_TRAP → HEALTHY_TRANSMISSION",
        "overrides": {"macro": "STABLE", "transmission": "HEALTHY_TRANSMISSION"},
    },
    "CREDIT_UNFREEZE": {
        "label": "Tín dụng khơi thông",
        "desc": "Chỉ chuyển LIQUIDITY_TRAP → HEALTHY_TRANSMISSION",
        "overrides": {"transmission": "HEALTHY_TRANSMISSION"},
    },
    "BULLISH_SECTOR": {
        "label": "Ngành tăng trưởng mạnh",
        "desc": "Chuyển phase ngành → MID (tốt nhất)",
        "overrides": {"sector": "MID"},
    },
    "BEST_COMPANY": {
        "label": "Doanh nghiệp hoàn hảo",
        "desc": "HQC + ULTRA_CHEAP + IN_VA_DEMAND",
        "overrides": {"health": "HIGH_QUALITY_COMPOUNDER",
                      "valuation": "ULTRA_CHEAP",
                      "behavior": "IN_VA_DEMAND"},
    },
    "OPTIMISTIC": {
        "label": "Lạc quan toàn phần",
        "desc": "Mọi yếu tố ở mức tốt nhất",
        "overrides": {k: v for k, v in BEST_EVIDENCE.items()},
    },
    "PESSIMISTIC": {
        "label": "Bi quan toàn phần",
        "desc": "Mọi yếu tố ở mức tệ nhất",
        "overrides": {k: v for k, v in WORST_EVIDENCE.items()},
    },
}

EVIDENCE_KEY_MAP = {
    "macro": ("macro_state", "macro"),
    "transmission": ("transmission_phase", "transmission"),
    "sector": ("sector_phase", "sector"),
    "health": ("health_archetype", "health"),
    "valuation": ("valuation_zone", "valuation"),
    "behavior": ("behavior_position", "behavior"),
}


@dataclass
class CounterfactualResult:
    """Counterfactual for one symbol under one scenario."""
    symbol: str
    scenario: str
    scenario_label: str
    baseline_p_gain: float
    cf_p_gain: float
    delta: float
    baseline_action: str
    cf_action: str
    baseline_alloc: float
    cf_alloc: float
    cf_eu_list: List[Tuple[str, float]]
    overrides: Dict[str, str]


@dataclass
class LeveragePoint:
    """Which evidence node has most influence on P(Gain) for this symbol."""
    symbol: str
    node: str
    baseline_value: str
    best_value: str
    p_gain_at_best: float
    delta: float


class CounterfactualEngine:
    """P5: What-if simulation over the Bayesian inference network."""

    def __init__(self):
        self.gov = BayesianGovernor()

    def _get_baseline_evidence(self, symbol: str) -> dict:
        """Extract current evidence vector from Governor for a symbol."""
        m = self.gov._macro
        t = self.gov._transmission
        s = self.gov._sector
        sector_phase = s.get("top_phase", "NEUTRAL")
        health = self.gov.perception.load_health(symbol)
        val = self.gov.valuation.score_valuation(symbol)
        beh = self.gov.behavior.score_behavior(symbol)
        return {
            "macro_state": m["state"],
            "transmission_phase": t["phase"],
            "sector_phase": sector_phase,
            "health_archetype": health["archetype"],
            "valuation_zone": val.get("overall_zone", "FAIR"),
            "behavior_position": beh.get("position", "UNKNOWN"),
            "macro_entropy": m["entropy"],
            "transmission_credit": t["credit"],
        }

    def _compute_cf(self, evidence: dict, overrides: dict) -> Tuple[float, str, float, list]:
        """Recompute P(Gain), action, alloc under overridden evidence."""
        cf = dict(evidence)
        for key, val in overrides.items():
            col, _ = EVIDENCE_KEY_MAP.get(key, (key, key))
            cf[col] = val

        p_gain, _, calib_penalty = compute_gain_probability(
            macro_state=cf["macro_state"],
            transmission_phase=cf["transmission_phase"],
            sector_phase=cf["sector_phase"],
            health_archetype=cf["health_archetype"],
            valuation_zone=cf["valuation_zone"],
            behavior_position=cf["behavior_position"],
            macro_entropy=cf["macro_entropy"],
            transmission_credit=cf["transmission_credit"],
        )
        eu_list = compute_expected_utilities(p_gain)
        action, best_eu = pick_best_action(eu_list)
        alloc = kelly_allocation(p_gain, calib_penalty, cf["macro_entropy"])
        if action in ("VETO", "AVOID"):
            alloc = 0.0
        elif action == "REDUCE":
            alloc = -min(alloc, 10.0)
        return p_gain, action, alloc, eu_list

    def analyze_one(self, symbol: str) -> Tuple[dict, List[CounterfactualResult], List[LeveragePoint]]:
        """Return (baseline_evidence, cf_results, leverage_points) for one symbol."""
        evidence = self._get_baseline_evidence(symbol)

        # Baseline
        p_base, act_base, alloc_base, eu_base = self._compute_cf(evidence, {})

        cf_results = []
        for key, sc in SCENARIOS.items():
            p_cf, act_cf, alloc_cf, eu_cf = self._compute_cf(evidence, sc["overrides"])
            cf_results.append(CounterfactualResult(
                symbol=symbol,
                scenario=key,
                scenario_label=sc["label"],
                baseline_p_gain=round(p_base, 4),
                cf_p_gain=round(p_cf, 4),
                delta=round(p_cf - p_base, 4),
                baseline_action=act_base,
                cf_action=act_cf,
                baseline_alloc=round(alloc_base, 1),
                cf_alloc=round(alloc_cf, 1),
                cf_eu_list=eu_cf,
                overrides=sc["overrides"],
            ))

        # Leverage: flip each node individually to its best value
        leverage = []
        for key, best_val in BEST_EVIDENCE.items():
            ov = {key: best_val}
            p_best, _, _, _ = self._compute_cf(evidence, ov)

            col, _ = EVIDENCE_KEY_MAP.get(key, (key, key))
            baseline_val = evidence.get(col, "?")
            leverage.append(LeveragePoint(
                symbol=symbol,
                node=key,
                baseline_value=str(baseline_val),
                best_value=best_val,
                p_gain_at_best=round(p_best, 4),
                delta=round(p_best - p_base, 4),
            ))
        leverage.sort(key=lambda x: abs(x.delta), reverse=True)

        return evidence, cf_results, leverage

    def analyze(self, symbols: List[str]) -> dict:
        results = {}
        for sym in symbols:
            ev, cf, lev = self.analyze_one(sym)
            results[sym] = {"evidence": ev, "counterfactuals": cf, "leverage": lev}
        return {
            "date": str(date.today()),
            "symbols": len(symbols),
            "results": results,
        }

    def close(self):
        self.gov.close()


# ═══════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════

def print_counterfactual_report(analysis: dict):
    print(f"\n  {'='*80}")
    print(f"  P5 COUNTERFACTUAL REASONING — '{analysis['date']}'")
    print(f"  {'='*80}")

    for sym, info in sorted(analysis["results"].items()):
        ev = info["evidence"]
        print(f"\n  {'─'*80}")
        print(f"  📍 {sym}")
        print(f"  {'─'*80}")

        # Baseline
        cf0 = info["counterfactuals"][0]
        print(f"  Baseline:        P(Gain)={cf0.baseline_p_gain:.1%}  "
              f"Action={cf0.baseline_action}  Alloc={cf0.baseline_alloc:+.1f}%")
        print(f"  Macro:           {ev['macro_state']} | Transmission: {ev['transmission_phase']} | "
              f"Sector: {ev['sector_phase']}")
        print(f"  Health:          {ev['health_archetype']} | "
              f"Valuation: {ev['valuation_zone']} | Behavior: {ev['behavior_position']}")

        # Counterfactual scenarios
        print(f"\n  {'▶ KỊCH BẢN GIẢ ĐỊNH (COUNTERFACTUAL)':─<64}")
        print(f"  {'Kịch bản':<26} {'P(Gain)':>8} {'Δ':>7} {'Hành động':<12} {'Alloc':>7}")
        print(f"  {'─'*64}")
        for cf in info["counterfactuals"]:
            delta_s = f"+{cf.delta:.1%}" if cf.delta >= 0 else f"{cf.delta:.1%}"
            print(f"  {cf.scenario_label:<26} {cf.cf_p_gain:>7.1%} {delta_s:>7} "
                  f"{cf.cf_action:<12} {cf.cf_alloc:>+6.1f}%")

        # Leverage ranking
        print(f"\n  {'▶ ĐÒN BẨY (LEVERAGE — flip từng nút lên best)':─<64}")
        print(f"  {'Nút':<16} {'Hiện tại':<18} {'→ Best':<18} {'P(Best)':>8} {'Δ':>7}")
        print(f"  {'─'*64}")
        for lp in info["leverage"]:
            delta_s = f"+{lp.delta:.1%}" if lp.delta >= 0 else f"{lp.delta:.1%}"
            print(f"  {lp.node:<16} {lp.baseline_value:<18} {lp.best_value:<18} "
                  f"{lp.p_gain_at_best:>7.1%} {delta_s:>7}")

    # Summary: which scenario liberates most capital
    print(f"\n  {'='*80}")
    print(f"  TỔNG HỢP ĐÒN BẨY VĨ MÔ")
    print(f"  {'='*80}")
    # Aggregate delta per scenario across symbols
    scenario_deltas: Dict[str, List[float]] = {}
    for info in analysis["results"].values():
        for cf in info["counterfactuals"]:
            scenario_deltas.setdefault(cf.scenario, []).append(cf.delta)
    for sc_name, deltas in sorted(scenario_deltas.items()):
        avg_d = sum(deltas) / len(deltas)
        label = SCENARIOS.get(sc_name, {}).get("label", sc_name)
        print(f"  {label:<26}: ΔP(trung bình) = {avg_d:+.1%}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="P5 Counterfactual Reasoning — 'What if?' simulation")
    parser.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB",
        "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    args = parser.parse_args()

    engine = CounterfactualEngine()
    analysis = engine.analyze(args.symbols)
    engine.close()
    print_counterfactual_report(analysis)


if __name__ == "__main__":
    main()
