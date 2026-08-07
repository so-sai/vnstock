"""
Grid Search: Optimize Multi-Factor Composite weights + thresholds.

Phase 1: Pre-compute all factor scores (expensive — done once)
Phase 2: Fast re-weighting loop over parameter combinations
Phase 3: Rank by Sharpe, validate top candidates

Usage: python -m backend.src.backtest.grid_search
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = BACKEND_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports" / "ablation_studies"
FINANCIAL_DB_PATH = DATA_DIR / "financial_facts.db"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from backtest.portfolio_tracker import PortfolioTracker
from backtest.unified_system_replay import (
    UNIVERSE,
    _get_behavioral_score,
    _get_close,
    _get_fundamental_score,
    _get_momentum_score,
    _get_sector,
    _get_trading_days,
    _vn20_gate,
)
from governor.interaction_engine import InteractionEngine
from governor.macro_lag_engine import MacroLagEngine
from governor.regional_influence_engine import RegionalInfluenceEngine
from governor.sector_exposure_matrix import SectorExposureMatrix

logger = logging.getLogger(__name__)


def precompute_scores(conn, dates, score_days):
    """Phase 1: Pre-compute all factor scores for all symbols on scoring days.

    Returns dict: {date: {symbol: {"fund": float, "behav": float, "alpha": float,
                                     "macro_eff": float, "sector": str}}}
    """
    macro_engine = RegionalInfluenceEngine()
    lag_engine = MacroLagEngine()
    ix_engine = InteractionEngine()
    matrix = SectorExposureMatrix()

    scores = {}
    t0 = time.time()
    total = len(score_days)

    for idx, date in enumerate(score_days):
        if (idx + 1) % 20 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta = (total - idx - 1) / rate if rate > 0 else 0
            print(f"  Precompute {idx + 1}/{total} ({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)")

        day_scores = {}

        # Macro scores (per sector)
        macro_result = None
        try:
            macro_result = macro_engine.compute(date)
        except Exception:  # noqa: BLE001 - batch isolation: 1 ngày macro lỗi không dừng precompute
            logger.debug("macro_engine.compute(%s) thất bại (bỏ qua, dùng score mặc định)", date)

        sector_macro_eff = {}
        if macro_result:
            M = macro_result.macro_vector
            sector_ranking = matrix.get_sector_ranking(M)
            lag_results = lag_engine.compute_all_sectors(date)
            ix_results = ix_engine.compute_all_sectors(M)

            for sect, raw_score in sector_ranking:
                eff = lag_results.get(sect)
                eff_score = eff.effective_score if eff else raw_score
                mult = ix_results.get(sect)
                final_score = eff_score * (mult.multiplier if mult else 1.0)
                sector_macro_eff[sect] = final_score

        # Per-stock scores
        for sym in UNIVERSE:
            sector = _get_sector(conn, sym)
            if not sector:
                continue

            # Check VN20 gate
            if not _vn20_gate(conn, sym, date):
                continue

            fund = _get_fundamental_score(conn, sym, date)
            behav = _get_behavioral_score(conn, sym, date)
            alpha = _get_momentum_score(conn, sym, date)
            macro_eff = sector_macro_eff.get(sector, 0.5)

            day_scores[sym] = {
                "fund": fund,
                "behav": behav,
                "alpha": alpha,
                "macro_eff": macro_eff,
                "sector": sector,
            }

        scores[date] = day_scores

    elapsed = time.time() - t0
    print(f"  Precompute done: {len(scores)} days, {elapsed:.1f}s")
    return scores


def run_backtest_with_params(scores, dates, score_days, params, db_path=None):
    """Phase 2: Fast backtest with pre-computed scores and given parameters."""
    w_fund = params["w_fund"]
    w_macro = params["w_macro"]
    w_alpha = params["w_alpha"]
    w_behav = params["w_behav"]
    entry_thresh = params["entry_thresh"]
    exit_thresh = params["exit_thresh"]
    trailing_stop = params["trailing_stop"]
    trailing_take = params["trailing_take"]
    sector_top_n = params.get("sector_top_n", 5)
    stocks_per_sector = params.get("stocks_per_sector", 2)

    MAX_POSITIONS = 10
    CASH_RESERVE = 0.05

    if db_path is None:
        db_path = str(DATA_DIR / "screener_cache.db")

    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")

    tracker = PortfolioTracker(
        initial_capital=100_000_000.0,
        max_positions=MAX_POSITIONS,
        cash_reserve=CASH_RESERVE,
        trailing_stop=trailing_stop,
        trailing_take=trailing_take,
        entry_threshold=entry_thresh,
        exit_threshold=exit_thresh,
    )

    equity_curve = []
    score_set = set(score_days)

    for i, date in enumerate(dates):
        # Build current prices
        current_prices = {}
        for sym in tracker.positions:
            p = _get_close(conn, sym, date)
            if p:
                current_prices[sym] = p

        if date in score_set and date in scores:
            day_scores = scores[date]

            # Get top sectors
            sector_macro = {}
            for sym_data in day_scores.values():
                sect = sym_data["sector"]
                if sect not in sector_macro:
                    sector_macro[sect] = sym_data["macro_eff"]
            top_sectors = sorted(sector_macro, key=lambda s: sector_macro[s], reverse=True)[:sector_top_n]

            # Build buy candidates
            buy_candidates = []
            seen_sectors = {}
            for sym, data in day_scores.items():
                sect = data["sector"]
                if sect not in top_sectors:
                    continue
                if sym in tracker.positions:
                    continue

                composite = (
                    w_fund * data["fund"] + w_macro * data["macro_eff"] + w_alpha * data["alpha"] + w_behav * data["behav"]
                )

                if composite > entry_thresh:
                    sector_count = seen_sectors.get(sect, 0)
                    if sector_count < stocks_per_sector:
                        buy_candidates.append((sym, composite, sect))
                        seen_sectors[sect] = sector_count + 1

            buy_candidates.sort(key=lambda x: x[1], reverse=True)

            # BUY
            for sym, composite, sect in buy_candidates[:MAX_POSITIONS]:
                if len(tracker.positions) >= MAX_POSITIONS:
                    break
                price = _get_close(conn, sym, date)
                if price:
                    current_prices[sym] = price
                    tracker.buy(sym, price, composite, current_prices)

            # SELL
            for sym in list(tracker.positions.keys()):
                price = _get_close(conn, sym, date)
                if price:
                    entry = tracker.entry_prices.get(sym)
                    data = day_scores.get(sym)
                    if data:
                        composite = (
                            w_fund * data["fund"]
                            + w_macro * data["macro_eff"]
                            + w_alpha * data["alpha"]
                            + w_behav * data["behav"]
                        )
                    else:
                        composite = 0.5

                    # Exit conditions
                    should_sell = False
                    if entry:
                        pnl = (price - entry) / entry
                        if pnl <= -trailing_stop:
                            should_sell = True
                        if pnl >= trailing_take:
                            should_sell = True
                    if composite < exit_thresh:
                        should_sell = True

                    # Sector rotation
                    pos_sector = _get_sector(conn, sym)
                    if pos_sector and pos_sector not in top_sectors:
                        should_sell = True

                    if should_sell:
                        tracker.sell(sym, price, composite, current_prices)
                        current_prices = {s: current_prices[s] for s in tracker.positions if s in current_prices}

        # Mark to market
        nav = tracker.nav(current_prices)
        equity_curve.append(nav)

    conn.close()

    # Compute metrics
    curve = np.array(equity_curve) if equity_curve else np.array([100_000_000.0])
    total_ret = (curve[-1] / curve[0]) - 1.0
    daily_rets = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([0.0])
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) if np.std(daily_rets) > 0 else 0.0
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    max_dd = float(np.min(dd))
    years = max(len(dates) / 252, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1

    sells = [t for t in tracker.trade_log if t["action"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pnl_pct", 0) > 0)
    win_rate = wins / len(sells) if sells else 0.0

    return {
        "total_return": round(total_ret * 100, 2),
        "annualized_return": round(ann_ret * 100, 2),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd * 100, 2),
        "volatility": round(vol * 100, 2),
        "win_rate": round(win_rate * 100, 1),
        "total_trades": len(tracker.trade_log),
        "final_nav": round(curve[-1], 0),
    }


def generate_weight_grid(step=0.10):
    """Generate weight combinations that sum to 1.0."""
    weights = []
    vals = np.arange(0, 1.0 + step, step)
    for w1 in vals:
        for w2 in vals:
            for w3 in vals:
                w4 = 1.0 - w1 - w2 - w3
                if -0.01 <= w4 <= 1.01:
                    weights.append(
                        {
                            "w_fund": round(w1, 2),
                            "w_macro": round(w2, 2),
                            "w_alpha": round(w3, 2),
                            "w_behav": round(max(0, min(1, w4)), 2),
                        }
                    )
    return weights


def main():
    start = "2021-04-01"
    end = "2026-08-04"
    sample_every = 5

    print("=" * 70)
    print("  GRID SEARCH: Multi-Factor Weight Optimization")
    print("=" * 70)
    print(f"  Period: {start} -> {end}")
    print()

    db_path = str(DATA_DIR / "screener_cache.db")
    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")

    dates = _get_trading_days(conn, start, end)
    score_days = dates[::sample_every]
    conn.close()

    # Phase 1: Pre-compute
    print("[Phase 1] Pre-computing factor scores...")
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    scores = precompute_scores(conn, dates, score_days)
    conn.close()

    # Phase 2: Grid search
    print("\n[Phase 2] Running grid search...")

    # Weight grid
    weight_combos = generate_weight_grid(step=0.10)
    print(f"  Weight combinations: {len(weight_combos)}")

    # Threshold grid
    entry_thresholds = [0.50, 0.55, 0.60, 0.65, 0.70]
    exit_thresholds = [0.30, 0.35, 0.40]
    trailing_stops = [0.03, 0.05, 0.07, 0.10]
    trailing_takes = [0.10, 0.15, 0.20, 0.25]

    # Stage 1: coarse — fixed thresholds, vary weights
    print("\n  [Stage 1] Weights only (entry=0.55, exit=0.35, stop=-5%, take=+15%)")
    results = []
    t0 = time.time()

    for i, w in enumerate(weight_combos):
        params = {
            **w,
            "entry_thresh": 0.55,
            "exit_thresh": 0.35,
            "trailing_stop": 0.05,
            "trailing_take": 0.15,
        }
        r = run_backtest_with_params(scores, dates, score_days, params, db_path)
        r["params"] = params
        results.append(r)

        if (i + 1) % 20 == 0:
            best = max(results, key=lambda x: x["sharpe"])
            print(f"    {i + 1}/{len(weight_combos)} | Best Sharpe={best['sharpe']:.4f} | Ret={best['total_return']:.1f}%")

    elapsed = time.time() - t0
    print(f"  Stage 1 done: {len(results)} runs in {elapsed:.0f}s ({elapsed / len(results):.1f}s/run)")

    # Stage 2: vary thresholds using top 10 weight combos
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    top_weights = [r["params"] for r in results[:10]]

    print("\n  [Stage 2] Thresholds x Top 10 weights")
    stage2_results = list(results)  # keep stage 1 results
    t0 = time.time()
    count = 0

    for w_params in top_weights:
        for entry in entry_thresholds:
            for exit_t in exit_thresholds:
                params = {
                    "w_fund": w_params["w_fund"],
                    "w_macro": w_params["w_macro"],
                    "w_alpha": w_params["w_alpha"],
                    "w_behav": w_params["w_behav"],
                    "entry_thresh": entry,
                    "exit_thresh": exit_t,
                    "trailing_stop": 0.05,
                    "trailing_take": 0.15,
                }
                r = run_backtest_with_params(scores, dates, score_days, params, db_path)
                r["params"] = params
                stage2_results.append(r)
                count += 1

    elapsed = time.time() - t0
    print(f"  Stage 2 done: {count} runs in {elapsed:.0f}s")

    # Stage 3: vary stops using top 5 from stage 2
    stage2_results.sort(key=lambda x: x["sharpe"], reverse=True)
    top_params = [r["params"] for r in stage2_results[:5]]

    print("\n  [Stage 3] Stop/Take x Top 5 params")
    final_results = list(stage2_results)
    t0 = time.time()
    count = 0

    for base_params in top_params:
        for stop in trailing_stops:
            for take in trailing_takes:
                params = {**base_params, "trailing_stop": stop, "trailing_take": take}
                r = run_backtest_with_params(scores, dates, score_days, params, db_path)
                r["params"] = params
                final_results.append(r)
                count += 1

    elapsed = time.time() - t0
    print(f"  Stage 3 done: {count} runs in {elapsed:.0f}s")

    # Phase 3: Rank and report
    print(f"\n[Phase 3] Analyzing {len(final_results)} total results...")

    # Sort by Sharpe
    final_results.sort(key=lambda x: x["sharpe"], reverse=True)

    print(f"\n{'=' * 90}")
    print("  TOP 15 PARAMETER COMBINATIONS (by Sharpe Ratio)")
    print(f"{'=' * 90}")
    print(
        f"  {'Rank':<5} {'Sharpe':>8} {'Return%':>10} {'MaxDD%':>8} "
        f"{'WinRate':>8} {'Trades':>7} | Weights (F/M/A/B) | Entry Exit Stop  Take"
    )
    print(f"  {'-' * 85}")

    for rank, r in enumerate(final_results[:15], 1):
        p = r["params"]
        print(
            f"  {rank:<5} {r['sharpe']:>8.4f} {r['total_return']:>10.1f} "
            f"{r['max_drawdown']:>8.1f} {r['win_rate']:>7.1f}% {r['total_trades']:>6} "
            f"| {p['w_fund']:.2f}/{p['w_macro']:.2f}/{p['w_alpha']:.2f}/{p['w_behav']:.2f} "
            f"|  {p['entry_thresh']:.2f}  {p['exit_thresh']:.2f}  {p['trailing_stop']:.0%}   {p['trailing_take']:.0%}"
        )

    # Also sort by return
    print(f"\n{'=' * 90}")
    print("  TOP 10 BY TOTAL RETURN")
    print(f"{'=' * 90}")
    by_return = sorted(final_results, key=lambda x: x["total_return"], reverse=True)
    for rank, r in enumerate(by_return[:10], 1):
        p = r["params"]
        print(
            f"  {rank:<5} Ret={r['total_return']:>8.1f}% Sharpe={r['sharpe']:.4f} "
            f"MaxDD={r['max_drawdown']:.1f}% | "
            f"{p['w_fund']:.2f}/{p['w_macro']:.2f}/{p['w_alpha']:.2f}/{p['w_behav']:.2f} "
            f"E={p['entry_thresh']:.2f} X={p['exit_thresh']:.2f}"
        )

    # Save best params
    best = final_results[0]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "best_sharpe": best,
        "top_5": final_results[:5],
        "total_combos_tested": len(final_results),
    }
    report_path = REPORTS_DIR / "grid_search_results.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved: {report_path}")

    # Print best summary
    bp = best["params"]
    print(f"\n{'=' * 70}")
    print("  OPTIMAL PARAMETERS")
    print(f"{'=' * 70}")
    print(
        f"  Weights:  M2(Fund)={bp['w_fund']:.2f}  M1(Macro)={bp['w_macro']:.2f}  "
        f"Alpha={bp['w_alpha']:.2f}  M3(Behav)={bp['w_behav']:.2f}"
    )
    print(f"  Entry:    {bp['entry_thresh']:.2f}")
    print(f"  Exit:     {bp['exit_thresh']:.2f}")
    print(f"  Stop:     {bp['trailing_stop']:.0%}")
    print(f"  Take:     {bp['trailing_take']:.0%}")
    print(f"  Sharpe:   {best['sharpe']:.4f}")
    print(f"  Return:   {best['total_return']:.1f}%")
    print(f"  MaxDD:    {best['max_drawdown']:.1f}%")
    print(f"  WinRate:  {best['win_rate']:.1f}%")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
