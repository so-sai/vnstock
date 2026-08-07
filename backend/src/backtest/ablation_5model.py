"""
Ablation Study: Test each of the 5 models individually and in combination.

Uses grid search's pre-computed scores for fast iteration.
Tests: M1(Macro), M2(Fundamental), M3(Behavioral), Alpha(Momentum), VN20(Gate)
"""

from __future__ import annotations

import json
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


def precompute_scores(conn, dates, score_days):
    """Pre-compute all factor scores (reused from grid_search)."""
    macro_engine = RegionalInfluenceEngine()
    lag_engine = MacroLagEngine()
    ix_engine = InteractionEngine()
    matrix = SectorExposureMatrix()

    scores = {}
    t0 = time.time()

    for idx, date in enumerate(score_days):
        if (idx + 1) % 50 == 0:
            print(f"  Precompute {idx + 1}/{len(score_days)} ({time.time() - t0:.0f}s)")

        day_scores = {}
        macro_result = None
        try:
            macro_result = macro_engine.compute(date)
        except Exception:
            pass

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

        for sym in UNIVERSE:
            sector = _get_sector(conn, sym)
            if not sector:
                continue
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

    print(f"  Precompute done: {len(scores)} days in {time.time() - t0:.1f}s")
    return scores


def run_ablation(scores, dates, score_days, weights, label, entry=0.55, exit_t=0.35, stop=0.05, take=0.20, db_path=None):
    """Run backtest with given weights (zero-weight = model disabled)."""
    if db_path is None:
        db_path = str(DATA_DIR / "screener_cache.db")

    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")

    tracker = PortfolioTracker(
        initial_capital=100_000_000.0,
        max_positions=10,
        cash_reserve=0.05,
        trailing_stop=stop,
        trailing_take=take,
        entry_threshold=entry,
        exit_threshold=exit_t,
    )

    equity_curve = []
    score_set = set(score_days)

    for i, date in enumerate(dates):
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
            top_sectors = sorted(sector_macro, key=lambda s: sector_macro[s], reverse=True)[:5]

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
                    weights["fund"] * data["fund"]
                    + weights["macro"] * data["macro_eff"]
                    + weights["alpha"] * data["alpha"]
                    + weights["behav"] * data["behav"]
                )

                if composite > entry:
                    sector_count = seen_sectors.get(sect, 0)
                    if sector_count < 2:
                        buy_candidates.append((sym, composite, sect))
                        seen_sectors[sect] = sector_count + 1

            buy_candidates.sort(key=lambda x: x[1], reverse=True)

            # BUY
            for sym, composite, sect in buy_candidates[:10]:
                if len(tracker.positions) >= 10:
                    break
                price = _get_close(conn, sym, date)
                if price:
                    current_prices[sym] = price
                    tracker.buy(sym, price, composite, current_prices)

            # SELL
            for sym in list(tracker.positions.keys()):
                price = _get_close(conn, sym, date)
                if price:
                    entry_price = tracker.entry_prices.get(sym)
                    data = day_scores.get(sym)
                    if data:
                        composite = (
                            weights["fund"] * data["fund"]
                            + weights["macro"] * data["macro_eff"]
                            + weights["alpha"] * data["alpha"]
                            + weights["behav"] * data["behav"]
                        )
                    else:
                        composite = 0.5

                    should_sell = False
                    if entry_price:
                        pnl = (price - entry_price) / entry_price
                        if pnl <= -stop:
                            should_sell = True
                        if pnl >= take:
                            should_sell = True
                    if composite < exit_t:
                        should_sell = True

                    pos_sector = _get_sector(conn, sym)
                    if pos_sector and pos_sector not in top_sectors:
                        should_sell = True

                    if should_sell:
                        tracker.sell(sym, price, composite, current_prices)
                        current_prices = {s: current_prices[s] for s in tracker.positions if s in current_prices}

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
        "label": label,
        "total_return": round(total_ret * 100, 2),
        "annualized_return": round(ann_ret * 100, 2),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd * 100, 2),
        "volatility": round(vol * 100, 2),
        "win_rate": round(win_rate * 100, 1),
        "total_trades": len(tracker.trade_log),
        "final_nav": round(curve[-1], 0),
    }


def main():
    start = "2021-04-01"
    end = "2026-08-04"
    sample_every = 5

    print("=" * 80)
    print("  ABLATION STUDY: 5-Model Individual & Combined Performance")
    print(f"  Period: {start} -> {end} (5+ years)")
    print("=" * 80)

    db_path = str(DATA_DIR / "screener_cache.db")
    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")

    dates = _get_trading_days(conn, start, end)
    score_days = dates[::sample_every]
    conn.close()

    # Phase 1: Pre-compute
    print("\n[Phase 1] Pre-computing factor scores...")
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    scores = precompute_scores(conn, dates, score_days)
    conn.close()

    # Phase 2: Ablation scenarios
    print("\n[Phase 2] Running ablation scenarios...")
    t0 = time.time()

    scenarios = [
        # Single models (only 1 factor nonzero)
        {"weights": {"fund": 1.0, "macro": 0.0, "alpha": 0.0, "behav": 0.0}, "label": "M2 ONLY (Fundamental)"},
        {"weights": {"fund": 0.0, "macro": 1.0, "alpha": 0.0, "behav": 0.0}, "label": "M1 ONLY (Macro)"},
        {"weights": {"fund": 0.0, "macro": 0.0, "alpha": 0.0, "behav": 1.0}, "label": "M3 ONLY (Behavioral)"},
        {"weights": {"fund": 0.0, "macro": 0.0, "alpha": 1.0, "behav": 0.0}, "label": "ALPHA ONLY (Momentum)"},
        # Dual models
        {"weights": {"fund": 0.5, "macro": 0.0, "alpha": 0.0, "behav": 0.5}, "label": "M2+M3 (Fund+Behav)"},
        {"weights": {"fund": 0.5, "macro": 0.0, "alpha": 0.5, "behav": 0.0}, "label": "M2+Alpha (Fund+Mom)"},
        {"weights": {"fund": 0.0, "macro": 0.0, "alpha": 0.5, "behav": 0.5}, "label": "M3+Alpha (Behav+Mom)"},
        {"weights": {"fund": 0.33, "macro": 0.34, "alpha": 0.33, "behav": 0.0}, "label": "M1+M2+Alpha (Equal)"},
        # Triple models
        {"weights": {"fund": 0.33, "macro": 0.0, "alpha": 0.33, "behav": 0.34}, "label": "M2+M3+Alpha (Equal)"},
        {"weights": {"fund": 0.25, "macro": 0.25, "alpha": 0.25, "behav": 0.25}, "label": "ALL 4 Equal (0.25)"},
        # Grid Search optimal
        {"weights": {"fund": 0.20, "macro": 0.00, "alpha": 0.10, "behav": 0.70}, "label": "GRID SEARCH OPTIMAL"},
        # Equal weight baseline
        {"weights": {"fund": 0.25, "macro": 0.25, "alpha": 0.25, "behav": 0.25}, "label": "EQUAL WEIGHT"},
    ]

    results = []
    for sc in scenarios:
        print(f"  Running: {sc['label']}")
        r = run_ablation(scores, dates, score_days, sc["weights"], sc["label"])
        results.append(r)
        print(f"    -> Sharpe={r['sharpe']:.4f} Return={r['total_return']:.1f}% MaxDD={r['max_drawdown']:.1f}%")

    elapsed = time.time() - t0
    print(f"\n  All scenarios done in {elapsed:.0f}s")

    # Phase 3: Report
    print(f"\n{'=' * 100}")
    print("  ABLATION STUDY RESULTS — 5 Models (2021-04 to 2026-08)")
    print(f"{'=' * 100}")
    print(f"  {'Scenario':<35} {'Sharpe':>8} {'Return%':>10} {'MaxDD%':>8} {'WinRate':>8} {'Trades':>7} {'Final NAV':>12}")
    print(f"  {'-' * 95}")

    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    for r in results:
        marker = " <-- BEST" if r == results[0] else ""
        print(
            f"  {r['label']:<35} {r['sharpe']:>8.4f} {r['total_return']:>10.1f} "
            f"{r['max_drawdown']:>8.1f} {r['win_rate']:>7.1f}% {r['total_trades']:>6} "
            f"{r['final_nav'] / 1e6:>10.1f}M{marker}"
        )

    # Save
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "period": {"start": start, "end": end},
        "results": results,
    }
    path = REPORTS_DIR / "ablation_5model_results.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved: {path}")


if __name__ == "__main__":
    main()
