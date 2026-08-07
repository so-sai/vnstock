"""counterfactual/engine.py — P5 Counterfactual Reasoning v2.

Given a Governor v2 BayesianMandate (7 evidence nodes), asks 'What if?'
for each evidence node, reports P(Gain) delta, and identifies leverage.

v2 changes:
  - Baseline sourced from Governor v2 assess() → full 7-node Bayesian network
  - Includes capital_allocation, archetype_prior_key, lr_macro_override
  - Baseline P(Gain) exactly matches Governor v2 output

Usage:
    from src.counterfactual.engine import CounterfactualEngine
    engine = CounterfactualEngine()
    report = engine.analyze(["FPT", "VCB"])
    print_counterfactual_report(report)
"""

from dataclasses import dataclass
from datetime import date

# Reuse Bayesian internals from Governor v2
from src.governor.company_state import (
    BayesianGovernor,
    BayesianMandate,
    compute_expected_utilities,
    compute_gain_probability,
    kelly_allocation,
    pick_best_action,
)

# ── Best / Worst values for each evidence node ──────────────
# v2: includes capital_allocation (Giai đoạn 4)
BEST_EVIDENCE = {
    "macro": "STABLE",
    "transmission": "HEALTHY_TRANSMISSION",
    "sector": "MID",
    "health": "HIGH_QUALITY_COMPOUNDER",
    "valuation": "ULTRA_CHEAP",
    "behavior": "IN_VA_DEMAND",
    "capital_allocation": "VALUE_CREATOR",
}

WORST_EVIDENCE = {
    "macro": "CREDIT_STRESS",
    "transmission": "CREDIT_CRUNCH",
    "sector": "WEAKENING",
    "health": "DISTRESSED",
    "valuation": "ULTRA_EXPENSIVE",
    "behavior": "ABOVE_VA",
    "capital_allocation": "VALUE_DESTROYER",
}

# Map scenario key → compute_gain_probability() parameter name
EVIDENCE_PARAM_MAP = {
    "macro": "macro_state",
    "transmission": "transmission_phase",
    "sector": "sector_phase",
    "health": "health_archetype",
    "valuation": "valuation_zone",
    "behavior": "behavior_position",
    "capital_allocation": "capital_allocation",
}

# ── Named scenarios (v2) ────────────────────────────────────
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
    "CAP_ALLOC_BOOST": {
        "label": "Phân bổ vốn tối ưu",
        "desc": "Chuyển capital_allocation → VALUE_CREATOR",
        "overrides": {"capital_allocation": "VALUE_CREATOR"},
    },
    "BEST_COMPANY": {
        "label": "Doanh nghiệp hoàn hảo",
        "desc": "HQC + ULTRA_CHEAP + IN_VA_DEMAND + VALUE_CREATOR",
        "overrides": {
            "health": "HIGH_QUALITY_COMPOUNDER",
            "valuation": "ULTRA_CHEAP",
            "behavior": "IN_VA_DEMAND",
            "capital_allocation": "VALUE_CREATOR",
        },
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
    cf_eu_list: list[tuple[str, float]]
    overrides: dict[str, str]


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
    """P5 v2: What-if simulation aligned with Governor v2 (7-node Bayesian network)."""

    def __init__(self):
        self.gov = BayesianGovernor()

    def _get_baseline_params(self, symbol: str) -> tuple[dict, BayesianMandate]:
        """Run Governor v2 assess() and extract full parameter set.

        Returns:
            (params_dict, mandate) where params_dict contains all 11
            parameters needed by compute_gain_probability().
        """
        mandate = self.gov.assess(symbol)
        params = {
            "macro_state": mandate.macro_state,
            "transmission_phase": mandate.transmission_phase,
            "sector_phase": mandate.sector_phase,
            "health_archetype": mandate.health_archetype,
            "valuation_zone": mandate.valuation_zone,
            "behavior_position": mandate.behavior_position,
            "capital_allocation": mandate.capital_allocation_archetype,
            "macro_entropy": self.gov._macro.get("entropy", 1.5),
            "transmission_credit": self.gov._transmission.get("credit", 50.0),
            "archetype_prior_key": mandate.archetype_prior,
            "lr_macro_override": mandate.lr_macro_dynamic,
        }
        return params, mandate

    def _compute_cf(self, baseline_params: dict, overrides: dict) -> tuple[float, str, float, list]:
        """Recompute P(Gain), action, alloc under overridden evidence.

        Uses ALL 11 parameters of compute_gain_probability() — exactly
        matching Governor v2's Bayesian network.
        """
        params = dict(baseline_params)
        for key, val in overrides.items():
            param_key = EVIDENCE_PARAM_MAP.get(key)
            if param_key:
                params[param_key] = val
                # When overriding macro_state, reset lr_macro_override
                # so the new macro state uses its default LR.
                if key == "macro":
                    params["lr_macro_override"] = None

        p_gain, _, calib_penalty = compute_gain_probability(
            macro_state=params["macro_state"],
            transmission_phase=params["transmission_phase"],
            sector_phase=params["sector_phase"],
            health_archetype=params["health_archetype"],
            valuation_zone=params["valuation_zone"],
            behavior_position=params["behavior_position"],
            capital_allocation=params.get("capital_allocation", "TRANSITIONAL"),
            macro_entropy=params.get("macro_entropy", 0.0),
            transmission_credit=params.get("transmission_credit", 50.0),
            archetype_prior_key=params.get("archetype_prior_key", "UNKNOWN"),
            lr_macro_override=params.get("lr_macro_override"),
        )
        eu_list = compute_expected_utilities(p_gain)
        action, best_eu = pick_best_action(eu_list)
        alloc = kelly_allocation(p_gain, calib_penalty, params.get("macro_entropy", 0.0))
        if action in ("VETO", "AVOID"):
            alloc = 0.0
        elif action == "REDUCE":
            alloc = -min(alloc, 10.0)
        return p_gain, action, alloc, eu_list

    def analyze_one(self, symbol: str) -> tuple[dict, list[CounterfactualResult], list[LeveragePoint]]:
        """Return (baseline_params, cf_results, leverage_points) for one symbol.

        Baseline P(Gain) sourced directly from Governor v2 assess().
        """
        params, mandate = self._get_baseline_params(symbol)
        p_base = mandate.p_gain
        act_base = mandate.action
        alloc_base = mandate.allocation_pct

        cf_results = []
        for key, sc in SCENARIOS.items():
            p_cf, act_cf, alloc_cf, eu_cf = self._compute_cf(params, sc["overrides"])
            cf_results.append(
                CounterfactualResult(
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
                )
            )

        # Leverage: flip each node individually to its best value
        leverage = []
        for key, best_val in BEST_EVIDENCE.items():
            ov = {key: best_val}
            p_best, _, _, _ = self._compute_cf(params, ov)

            baseline_val = params.get(EVIDENCE_PARAM_MAP.get(key), "?")
            leverage.append(
                LeveragePoint(
                    symbol=symbol,
                    node=key,
                    baseline_value=str(baseline_val),
                    best_value=best_val,
                    p_gain_at_best=round(p_best, 4),
                    delta=round(p_best - p_base, 4),
                )
            )
        leverage.sort(key=lambda x: abs(x.delta), reverse=True)

        return params, cf_results, leverage

    def analyze(self, symbols: list[str]) -> dict:
        results = {}
        for sym in symbols:
            params, cf, lev = self.analyze_one(sym)
            results[sym] = {"params": params, "counterfactuals": cf, "leverage": lev}
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


def print_counterfactual_report(analysis: dict, lang_mode: str = "full"):
    """In báo cáo counterfactual (song ngữ)."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:

        def localize_label(label, m="full"):
            return label

    def _(x):
        return localize_label(x, lang_mode)

    print(f"\n  {'=' * 80}")
    print(f"  P5 {_('Counterfactual')} {_('Reasoning')} v2 — '{analysis['date']}'")
    print(f"  {'=' * 80}")

    for sym, info in sorted(analysis["results"].items()):
        params = info["params"]
        print(f"\n  {'─' * 80}")
        print(f"  📍 {sym}")
        print(f"  {'─' * 80}")

        # Baseline (v2 fields)
        cf0 = info["counterfactuals"][0]
        print(
            f"  {_('Baseline')}:        P(Gain)={cf0.baseline_p_gain:.1%}  "
            f"Action={cf0.baseline_action}  Alloc={cf0.baseline_alloc:+.1f}%"
        )
        print(
            f"  {_('Macro')}:           {params['macro_state']} | {_('Transmission')}: {params['transmission_phase']} | "
            f"{_('Sector')}: {params['sector_phase']}"
        )
        print(
            f"  {_('Health')}:          {params['health_archetype']} | "
            f"{_('Valuation')}: {params['valuation_zone']} | {_('Behavior')}: {params['behavior_position']}"
        )
        print(
            f"  {_('Cap.Alloc')}:       {params['capital_allocation']} | "
            f"{_('Prior')}: {params['archetype_prior_key']} | "
            f"{_('Macro LR')}: {params.get('lr_macro_override', 0):.3f}"
        )

        # Counterfactual scenarios
        print(f"\n  {'▶ ' + _('Scenario') + ' (' + _('Counterfactual') + ')':─<64}")
        print(f"  {_('Scenario'):<26} {'P(Gain)':>8} {'Δ':>7} {_('Action'):<12} {'Alloc':>7}")
        print(f"  {'─' * 64}")
        for cf in info["counterfactuals"]:
            delta_s = f"+{cf.delta:.1%}" if cf.delta >= 0 else f"{cf.delta:.1%}"
            print(f"  {cf.scenario_label:<26} {cf.cf_p_gain:>7.1%} {delta_s:>7} {cf.cf_action:<12} {cf.cf_alloc:>+6.1f}%")

        # Leverage ranking (v2: includes capital_allocation)
        print(f"\n  {'▶ ' + _('Leverage') + ' (flip từng nút lên best)':─<64}")
        print(f"  {_('Node'):<16} {_('Current'):<18} {'→ Best':<18} {'P(Best)':>8} {'Δ':>7}")
        print(f"  {'─' * 64}")
        for lp in info["leverage"]:
            delta_s = f"+{lp.delta:.1%}" if lp.delta >= 0 else f"{lp.delta:.1%}"
            print(f"  {lp.node:<16} {lp.baseline_value:<18} {lp.best_value:<18} {lp.p_gain_at_best:>7.1%} {delta_s:>7}")

    # Summary: which scenario liberates most capital
    print(f"\n  {'=' * 80}")
    print(f"  {_('LEVERAGE SUMMARY')}")
    print(f"  {'=' * 80}")
    scenario_deltas: dict[str, list[float]] = {}
    for info in analysis["results"].values():
        for cf in info["counterfactuals"]:
            scenario_deltas.setdefault(cf.scenario, []).append(cf.delta)
    for sc_name, deltas in sorted(scenario_deltas.items()):
        avg_d = sum(deltas) / len(deltas)
        label = SCENARIOS.get(sc_name, {}).get("label", sc_name)
        print(f"  {label:<26}: ΔP({_('avg')}) = {avg_d:+.1%}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="P5 Counterfactual Reasoning — 'What if?' simulation")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[
            "FPT",
            "ACB",
            "HDB",
            "MBB",
            "VCB",
            "HPG",
            "VHM",
            "DGC",
            "MWG",
            "GAS",
        ],
        help="Danh sách mã",
    )
    args = parser.parse_args()

    engine = CounterfactualEngine()
    analysis = engine.analyze(args.symbols)
    engine.close()
    print_counterfactual_report(analysis)


if __name__ == "__main__":
    main()
