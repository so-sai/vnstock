"""
live_fund_simulation.py — Live Fund Simulation Layer.

Reads shadow log entries and simulates PnL as if the system drove
a simple long-only equity strategy. Benchmarks against VNINDEX.

This is NOT a trading engine. It is a post-hoc evaluator.
No state. No real money. Pure computation on shadow data.

Strategy rules (from shadow signals):
  - FLOW dominant + drift LOW       → long (bullish consensus)
  - BREADTH dominant + drift LOW    → long (broad-based)
  - STRUCTURE/MOMENTUM dominant     → long (trend following)
  - CRISIS regime                   → cash (risk-off)
  - drift HIGH / warning level      → cash (uncertainty)
  - MACRO dominant                  → cash (macro-driven, no edge)
  - default                         → 50% exposure (neutral)
"""

from __future__ import annotations

import math

# ── Position sizing from shadow signals ─────────────────────────────────


def _position_size(entry: dict) -> float:
    """Determine position size 0.0 (cash) to 1.0 (fully long) from shadow signals.

    Uses prediction, stability, and semantic health.
    """
    pred = entry.get("prediction", {})
    stability = entry.get("stability", {})
    semantic = entry.get("semantic", {})
    entry.get("realized")

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
        return 0.0  # macro-driven → no stock edge

    # ── Default ─────────────────────────────────────────────────
    return 0.5


# ── PnL simulation ────────────────────────────────────────────────────────


def simulate_pnl(
    shadow_entries: list[dict],
    annual_rf_rate: float = 0.05,
) -> dict:
    """Run PnL simulation over shadow log entries.

    Args:
        shadow_entries: list of shadow log entries (must have .realized)
        annual_rf_rate: risk-free rate for Sharpe calculation (default 5%)

    Returns:
        {
            "total_return_pct": float,
            "annualized_return_pct": float,
            "annualized_volatility_pct": float,
            "sharpe_ratio": float,
            "max_drawdown_pct": float,
            "win_rate": float,
            "total_trades": int,
            "profitable_trades": int,
            "exposure_days": int,
            "cash_days": int,
            "benchmark_return_pct": float,
            "alpha_pct": float,
            "equity_curve": [float, ...],
            "benchmark_curve": [float, ...],
        }
    """
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
        size = _position_size(entry)
        mkt_dir = entry["realized"].get("market_direction", 0.0)

        if size > 0:
            exposure_days += 1
        else:
            cash_days += 1

        # Daily PnL: position_size * market_return
        daily_pnl = size * mkt_dir / 100.0  # mkt_dir is in % points
        equity *= 1.0 + daily_pnl
        equity_curve.append(equity)

        # Track win/loss
        if daily_pnl != 0:
            total += 1
            if daily_pnl > 0:
                wins += 1

        # Benchmark
        benchmark_curve.append(benchmark_curve[-1] * (1.0 + mkt_dir / 100.0))

        # Drawdown
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    # ── Return metrics ──────────────────────────────────────────
    total_return = (equity - 1.0) * 100.0
    _start_date = realized[0].get("date")
    _end_date = realized[-1].get("date")
    from backtest.portfolio_tracker import annualize_cagr

    ann_return = annualize_cagr(total_return / 100.0, _start_date, _end_date) * 100.0

    # Daily returns for volatility
    daily_returns = []
    for i in range(1, len(equity_curve)):
        dr = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        daily_returns.append(dr)

    if daily_returns:
        avg_daily = sum(daily_returns) / len(daily_returns)
        variance = sum((r - avg_daily) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = math.sqrt(variance)
        ann_vol = daily_vol * math.sqrt(252) * 100.0

        # Sharpe (annualized)
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
