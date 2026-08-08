"""
alpha_attribution_engine.py — Alpha Attribution Engine (FAE)

Final closure layer for the decision stack.
Answers: "Which module actually creates alpha, and which just adds noise?"

Methodology:
  - Counterfactual replay over shadow log entries
  - Leave-one-out ablation (PnL delta per module)
  - Pairwise interaction estimation (Shapley-light)
  - Risk decomposition (max drawdown attribution)

No market prediction. No trade execution. Pure causal decomposition.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()

# ── Module registry ──────────────────────────────────────────────────────────

MODULES = ["driver_state", "drift_layer", "ets_validation", "early_warning"]

MODULE_LABELS = {
    "driver_state": "Driver State (dominant driver detection)",
    "drift_layer": "Drift Prevention (cognitive drift detection)",
    "ets_validation": "ETS Validation (narrative truth score)",
    "early_warning": "Early Warning (temporal stability signals)",
}

# ── Toggle application ───────────────────────────────────────────────────────


def _neutralize_driver_state(entry: dict) -> dict:
    """Neutralize driver dominance — set to unknown, uniform distribution."""
    e = copy.deepcopy(entry)
    pred = e.setdefault("prediction", {})
    pred["dominant_driver"] = "UNKNOWN"
    pred["driver_distribution"] = {}
    if "driver_state" in e:
        del e["driver_state"]
    return e


def _neutralize_drift_layer(entry: dict) -> dict:
    """Neutralize drift signals — set drift to zero."""
    e = copy.deepcopy(entry)
    pred = e.setdefault("prediction", {})
    pred["drift_score"] = 0.0
    pred["drift_status"] = "NONE"
    return e


def _neutralize_ets(entry: dict) -> dict:
    """Neutralize ETS — set semantic consistency to perfect."""
    e = copy.deepcopy(entry)
    sem = e.setdefault("semantic", {})
    sem["consistency_score"] = 1.0
    sem["drift_in_meaning"] = False
    return e


def _neutralize_early_warning(entry: dict) -> dict:
    """Neutralize early warning — set to clean, zero risk."""
    e = copy.deepcopy(entry)
    stab = e.setdefault("stability", {})
    stab["early_warning"] = "clean"
    stab["risk_of_drift"] = 0.0
    stab["drift_trend"] = "stable"
    return e


NEUTRALIZERS: dict[str, Callable] = {
    "driver_state": _neutralize_driver_state,
    "drift_layer": _neutralize_drift_layer,
    "ets_validation": _neutralize_ets,
    "early_warning": _neutralize_early_warning,
}


def _apply_toggle_set(entry: dict, active_modules: set[str]) -> dict:
    """Neutralize all modules NOT in active_modules."""
    e = copy.deepcopy(entry)
    for mod, neutralizer in NEUTRALIZERS.items():
        if mod not in active_modules:
            e = neutralizer(e)
    return e


# ── Position sizing (counterfactual-aware) ───────────────────────────────────


def _position_size(entry: dict) -> float:
    """Determine position size 0.0 (cash) to 1.0 (fully long) from shadow signals.

    Mirrors live_fund_simulation._position_size() logic exactly.
    """
    pred = entry.get("prediction", {})
    stability = entry.get("stability", {})
    semantic = entry.get("semantic", {})

    driver = pred.get("dominant_driver", "UNKNOWN")
    drift_score = pred.get("drift_score", 0.0)
    regime = pred.get("regime_status", "UNKNOWN")
    ew = stability.get("early_warning", "clean")
    risk = stability.get("risk_of_drift", 0.0)
    meaning_drift = semantic.get("drift_in_meaning", False)

    # ── Cash triggers ───────────────────────────────────────────
    if regime == "CRISIS":
        return 0.0
    if ew == "warning" or meaning_drift:
        return 0.0
    if drift_score > 0.5:
        return 0.0
    if risk > 0.6:
        return 0.0

    # ── Long triggers ───────────────────────────────────────────
    if driver in ("FLOW", "BREADTH", "STRUCTURE", "MOMENTUM"):
        if drift_score < 0.2 and ew == "clean":
            return 1.0
        if drift_score < 0.35 and ew in ("clean", "caution"):
            return 0.75
        return 0.5

    if driver == "MACRO":
        return 0.0

    return 0.5


# ── PnL simulation for a single configuration ────────────────────────────────


def _simulate_pnl(
    shadow_entries: list[dict],
    active_modules: set[str],
    annual_rf_rate: float = 0.05,
) -> dict:
    """Run PnL simulation with only the specified modules active.

    Args:
        shadow_entries: Shadow log entries (must have .realized).
        active_modules: Set of module names that are ACTIVE.
        annual_rf_rate: Risk-free rate for Sharpe.

    Returns:
        dict with pnl, sharpe, max_drawdown, win_rate, etc.
    """
    # Filter to entries that have been realized
    realized = [e for e in shadow_entries if e.get("realized") is not None]
    if not realized:
        return {
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "annualized_volatility_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "profitable_trades": 0,
            "exposure_days": 0,
            "cash_days": 0,
            "benchmark_return_pct": 0.0,
            "alpha_pct": 0.0,
            "equity_curve": [],
            "benchmark_curve": [],
        }

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    equity_curve = [equity]
    benchmark_curve = [1.0]
    wins = 0
    total = 0
    exposure_days = 0
    cash_days = 0

    for entry in realized:
        counterfactual = _apply_toggle_set(entry, active_modules)
        size = _position_size(counterfactual)
        mkt_dir = entry["realized"].get("market_direction", 0.0)

        if size > 0:
            exposure_days += 1
        else:
            cash_days += 1

        daily_pnl = size * mkt_dir / 100.0
        equity *= 1.0 + daily_pnl
        equity_curve.append(equity)

        if daily_pnl != 0:
            total += 1
            if daily_pnl > 0:
                wins += 1

        benchmark_curve.append(benchmark_curve[-1] * (1.0 + mkt_dir / 100.0))

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    total_return = (equity - 1.0) * 100.0
    _start_date = realized[0].get("date")
    _end_date = realized[-1].get("date")
    from backtest.portfolio_tracker import annualize_cagr

    ann_return = annualize_cagr(total_return / 100.0, _start_date, _end_date) * 100.0

    daily_returns = []
    for i in range(1, len(equity_curve)):
        dr = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        daily_returns.append(dr)

    if daily_returns:
        avg_daily = sum(daily_returns) / len(daily_returns)
        variance = sum((r - avg_daily) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = math.sqrt(variance)
        ann_vol = daily_vol * math.sqrt(252) * 100.0

        daily_rf = annual_rf_rate / 252
        excess_returns = [r - daily_rf for r in daily_returns]
        avg_excess = sum(excess_returns) / len(excess_returns)
        if daily_vol > 0:
            sharpe = (avg_excess / daily_vol) * math.sqrt(252)
        else:
            sharpe = 0.0
    else:
        ann_vol = 0.0
        sharpe = 0.0

    win_rate = wins / total if total > 0 else 0.0
    benchmark_return = (benchmark_curve[-1] - 1.0) * 100.0
    alpha = total_return - benchmark_return

    return {
        "total_return_pct": round(total_return, 4),
        "annualized_return_pct": round(ann_return, 4),
        "annualized_volatility_pct": round(ann_vol, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total,
        "profitable_trades": wins,
        "exposure_days": exposure_days,
        "cash_days": cash_days,
        "benchmark_return_pct": round(benchmark_return, 4),
        "alpha_pct": round(alpha, 4),
        "equity_curve": [round(e, 6) for e in equity_curve],
        "benchmark_curve": [round(b, 6) for b in benchmark_curve],
    }


# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class AlphaAttributionReport:
    """Fund-grade alpha attribution report."""

    # ── Metadata ─────────────────────────────────────────────────
    generated_at: str
    window_days: int
    sample_count: int

    # ── Top-line ──────────────────────────────────────────────────
    total_pnl_pct: float
    baseline_pnl_pct: float
    net_alpha_pct: float

    # ── Per-module attribution ────────────────────────────────────
    alpha_attribution: dict[str, float]  # module → ΔPnL (percentage points)
    sharpe_delta: dict[str, float]  # module → ΔSharpe
    mdd_delta: dict[str, float]  # module → ΔMaxDrawdown (pp)
    win_rate_delta: dict[str, float]  # module → ΔWinRate (pp)

    # ── Interaction effects ───────────────────────────────────────
    interaction_effects: dict[str, float]  # "A×B" → interaction magnitude
    total_interaction_pct: float

    # ── Risk decomposition ────────────────────────────────────────
    risk_attribution: dict[str, dict] = field(default_factory=dict)

    # ── Full config results for reference ─────────────────────────
    config_results: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            "generated_at": self.generated_at,
            "window_days": self.window_days,
            "sample_count": self.sample_count,
            "total_pnl_pct": self.total_pnl_pct,
            "baseline_pnl_pct": self.baseline_pnl_pct,
            "net_alpha_pct": self.net_alpha_pct,
            "alpha_attribution": self.alpha_attribution,
            "risk_attribution": self.risk_attribution,
            "sharpe_delta": self.sharpe_delta,
            "mdd_delta": self.mdd_delta,
            "win_rate_delta": self.win_rate_delta,
            "interaction_effects": self.interaction_effects,
            "total_interaction_pct": self.total_interaction_pct,
            "config_results": self.config_results,
        }

    def to_json(self, path: Path | None = None) -> str:
        """Serialize to JSON string, optionally writing to file."""
        data = self.to_dict()
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text


# ── Core engine ──────────────────────────────────────────────────────────────


def _build_config_key(active_modules: set[str]) -> str:
    """Build a short config key like 'full' or 'no-driver_state'."""
    if active_modules == set(MODULES):
        return "full"
    if not active_modules:
        return "baseline"
    missing = set(MODULES) - active_modules
    return "no-" + "-".join(sorted(missing))


def run_attribution(
    shadow_log: list[dict],
    window: int = 30,
    annual_rf_rate: float = 0.05,
    include_pairwise: bool = True,
) -> AlphaAttributionReport:
    """Run full alpha attribution analysis over shadow log entries.

    Args:
        shadow_log: List of shadow log entries (from shadow_metrics_schema).
        window: Number of most-recent realized entries to use.
        annual_rf_rate: Risk-free rate for Sharpe (default 5%).
        include_pairwise: If True, compute pairwise interaction effects.

    Returns:
        AlphaAttributionReport with per-module attribution.
    """
    # Use only most recent N realized entries
    realized = [e for e in shadow_log if e.get("realized") is not None]
    if not realized:
        return AlphaAttributionReport(
            generated_at=datetime.now().isoformat(),
            window_days=window,
            sample_count=0,
            total_pnl_pct=0.0,
            baseline_pnl_pct=0.0,
            net_alpha_pct=0.0,
            alpha_attribution={m: 0.0 for m in MODULES},
            sharpe_delta={m: 0.0 for m in MODULES},
            mdd_delta={m: 0.0 for m in MODULES},
            win_rate_delta={m: 0.0 for m in MODULES},
            interaction_effects={},
            total_interaction_pct=0.0,
            risk_attribution={},
            config_results={},
        )

    entries = realized[-window:]

    # ── Define configurations to run ─────────────────────────────
    configs: dict[str, set[str]] = {
        "full": set(MODULES),
        "baseline": set(),
    }
    for mod in MODULES:
        configs[_build_config_key(set(MODULES) - {mod})] = set(MODULES) - {mod}

    if include_pairwise:
        for i, m1 in enumerate(MODULES):
            for m2 in MODULES[i + 1 :]:
                missing = {m1, m2}
                key = _build_config_key(set(MODULES) - missing)
                configs[key] = set(MODULES) - missing

    # ── Run all configurations ───────────────────────────────────
    results = {}
    for key, active in configs.items():
        results[key] = _simulate_pnl(entries, active, annual_rf_rate)

    full = results.get("full", {})
    baseline = results.get("baseline", {})

    total_pnl = full.get("total_return_pct", 0.0)
    baseline_pnl = baseline.get("total_return_pct", 0.0)
    net_alpha = total_pnl - baseline_pnl

    # ── Per-module attribution (leave-one-out) ────────────────────
    alpha_attribution = {}
    sharpe_delta = {}
    mdd_delta = {}
    win_rate_delta = {}

    for mod in MODULES:
        key = _build_config_key(set(MODULES) - {mod})
        without = results.get(key, {})
        alpha_attribution[mod] = round(total_pnl - without.get("total_return_pct", 0.0), 4)
        sharpe_delta[mod] = round(full.get("sharpe_ratio", 0.0) - without.get("sharpe_ratio", 0.0), 4)
        mdd_delta[mod] = round(full.get("max_drawdown_pct", 0.0) - without.get("max_drawdown_pct", 0.0), 4)
        win_rate_delta[mod] = round((full.get("win_rate", 0.0) - without.get("win_rate", 0.0)) * 100.0, 2)

    # ── Interaction effects ───────────────────────────────────────
    interaction_effects = {}
    direct_sum = sum(alpha_attribution.values())
    total_interaction = round(net_alpha - direct_sum, 4)

    if include_pairwise:
        for i, m1 in enumerate(MODULES):
            for m2 in MODULES[i + 1 :]:
                both_off_key = _build_config_key(set(MODULES) - {m1, m2})
                m1_off_key = _build_config_key(set(MODULES) - {m1})
                m2_off_key = _build_config_key(set(MODULES) - {m2})

                both = results.get(both_off_key, {})
                m1_off = results.get(m1_off_key, {})
                m2_off = results.get(m2_off_key, {})

                # Interaction = PnL(F) - PnL(F\m1) - PnL(F\m2) + PnL(F\{m1,m2})
                interaction = (
                    total_pnl
                    - m1_off.get("total_return_pct", 0.0)
                    - m2_off.get("total_return_pct", 0.0)
                    + both.get("total_return_pct", 0.0)
                )
                if abs(interaction) > 0.01:
                    interaction_effects[f"{m1}×{m2}"] = round(interaction, 4)

    # ── Risk decomposition ────────────────────────────────────────
    risk_attribution = {
        "max_drawdown_reduction": {
            m: round(baseline.get("max_drawdown_pct", 0.0) - full.get("max_drawdown_pct", 0.0), 4) for m in MODULES
        },
        "sharpe_improvement": {m: sharpe_delta[m] for m in MODULES},
    }

    # ── Assemble report ──────────────────────────────────────────
    report = AlphaAttributionReport(
        generated_at=datetime.now().isoformat(),
        window_days=window,
        sample_count=len(entries),
        total_pnl_pct=round(total_pnl, 4),
        baseline_pnl_pct=round(baseline_pnl, 4),
        net_alpha_pct=round(net_alpha, 4),
        alpha_attribution=alpha_attribution,
        sharpe_delta=sharpe_delta,
        mdd_delta=mdd_delta,
        win_rate_delta=win_rate_delta,
        interaction_effects=interaction_effects,
        total_interaction_pct=total_interaction,
        risk_attribution=risk_attribution,
        config_results={
            k: {
                "total_return_pct": v.get("total_return_pct"),
                "sharpe_ratio": v.get("sharpe_ratio"),
                "max_drawdown_pct": v.get("max_drawdown_pct"),
                "win_rate": v.get("win_rate"),
                "alpha_pct": v.get("alpha_pct"),
            }
            for k, v in sorted(results.items())
        },
    )

    return report


# ── CLI entry point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=" * 60)
    print("  Alpha Attribution Engine (FAE) — v1.0")
    print("=" * 60)

    # Try to load shadow log from standard output path
    import src.config

    shadow_path = Path(src.config.DATA_DIR) / "output" / "shadow_log.json"
    if not shadow_path.exists():
        print(f"No shadow log found at {shadow_path}")
        print("Run the pipeline first to generate shadow data.")
        sys.exit(0)

    with open(shadow_path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries", data) if isinstance(data, dict) else data
    print(f"Loaded {len(entries)} shadow entries from {shadow_path}")

    report = run_attribution(entries, window=min(30, len(entries)))

    output_path = Path(src.config.DATA_DIR) / "output" / "alpha_attribution.json"
    report.to_json(output_path)

    print(f"\n{'─' * 50}")
    print(f"  TOTAL PnL:      {report.total_pnl_pct:>+8.2f}%")
    print(f"  BASELINE PnL:   {report.baseline_pnl_pct:>+8.2f}%")
    print(f"  NET ALPHA:      {report.net_alpha_pct:>+8.2f}%")
    print(f"{'─' * 50}")
    print("  Alpha Attribution:")
    for mod, contrib in sorted(report.alpha_attribution.items(), key=lambda x: -abs(x[1])):
        print(f"    {mod:<20s}  {contrib:>+8.4f} pp")
    print(f"{'─' * 50}")
    print(f"  Interaction:     {report.total_interaction_pct:>+8.4f} pp")
    if report.interaction_effects:
        for pair, val in report.interaction_effects.items():
            print(f"    {pair:<20s}  {val:>+8.4f} pp")
    print(f"{'─' * 50}")
    print("  Sharpe Delta:")
    for mod, delta in sorted(report.sharpe_delta.items(), key=lambda x: -abs(x[1])):
        print(f"    {mod:<20s}  {delta:>+8.4f}")
    print("  Max Drawdown Delta:")
    for mod, delta in sorted(report.mdd_delta.items(), key=lambda x: -abs(x[1])):
        print(f"    {mod:<20s}  {delta:>+8.4f} pp")
    print(f"{'─' * 50}")
    print(f"Report saved to {output_path}")
