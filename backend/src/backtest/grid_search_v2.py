"""
grid_search_v2.py — Grid Search Optimization with DecisionGuard Integration.

Grid Search v2: Multi-Factor Composite weights + thresholds + DecisionGuard
(LRI + EmergencyExitEngine) + Min Hold Period.

Architecture:
  Phase 0: Pre-compute PIT LRI per scoring day (expensive — done once, cached to disk)
  Phase 1: Pre-compute all factor scores (expensive — done once)
  Phase 2: Fast re-weighting loop over parameter combinations
           - LRI DEFENSIVE  -> BUY LOCK (he_so_giam_ty_trong = 0.0) + EmergencyExitEngine
           - LRI PROBE      -> he_so scaled by LRI (dimmer)
           - LRI AGGRESSIVE -> full allocation
  Phase 3: Rank by Sharpe, validate top candidates

Usage: python -m backend.src.backtest.grid_search_v2 [--workers N] [--step SIZE]
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


def _hydrate_path() -> Path:
    """Path Hydrator v2.2: Auto-locate Project Root (anchored on AGENTS.md + backend is_dir)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = BACKEND_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports" / "ablation_studies"
FINANCIAL_DB_PATH = DATA_DIR / "financial_facts.db"
SCREENER_DB_PATH = DATA_DIR / "screener_cache.db"
LRI_CACHE_PATH = DATA_DIR / "reports" / "ablation_studies" / "grid_lri_cache.json"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from backtest.portfolio_tracker import PortfolioTracker, annualize_cagr
from backtest.unified_system_replay import (
    UNIVERSE,
    _date_to_period,
    _get_behavioral_score,
    _get_close,
    _get_fundamental_score,
    _get_momentum_score,
    _get_sector,
    _get_trading_days,
    _vn20_gate,
)
from governor.emergency_exit_engine import EmergencyExitEngine
from governor.governor_layer import combine_alloc_multiplier
from governor.interaction_engine import InteractionEngine
from governor.macro_lag_engine import MacroLagEngine
from governor.regional_influence_engine import RegionalInfluenceEngine
from governor.sector_exposure_matrix import SectorExposureMatrix

logger = logging.getLogger(__name__)

# ── Default parameter space (5-Layer High-Conviction Matrix) ────────────────
DEFAULT_WEIGHT_RANGES = {
    "w_fund": (0.35, 0.50),  # M2 Fundamental/MoS — primary pillar
    "w_macro": (0.10, 0.20),  # M1 Macro (effective, LAW-009 lagged)
    "w_alpha": (0.15, 0.30),  # Alpha momentum
    "w_behav": (0.10, 0.25),  # M3 Behavioral/Volume — confirmation only
}

DEFAULT_ENTRY_THRESHOLDS = [0.55, 0.60, 0.65, 0.70]
DEFAULT_EXIT_THRESHOLDS = [0.30, 0.35, 0.40]
DEFAULT_TRAILING_STOPS = [0.03, 0.05, 0.07, 0.10]
DEFAULT_TRAILING_TAKES = [0.10, 0.15, 0.20, 0.25]
DEFAULT_MIN_HOLDS = [5, 10, 15]

# LRI regime thresholds (mirror liquidity_recovery_index)
LRI_DEFENSIVE = 0.30
LRI_PROBE = 0.80

# Emergency exit engine constants
EMERGENCY_TIGHTENED_STOP = -0.02


def precompute_lri_cache(dates: list[str], db_path: str | None = None, force: bool = False) -> dict[str, float]:
    """Phase 0: Pre-compute PIT LRI for each trading day, cached to disk.

    Returns: {date: lri_score}. Days missing from cache default to 1.0 (no lock).
    """
    if db_path is None:
        db_path = str(SCREENER_DB_PATH)

    if LRI_CACHE_PATH.exists() and not force:
        try:
            with open(LRI_CACHE_PATH) as f:
                cached = json.load(f)
            cached = {k: float(v) for k, v in cached.items()}
            if all(d in cached for d in dates):
                return cached
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as exc:
            print(f"  [WARN] LRI cache unreadable (recomputing): {exc}")

    from governor.liquidity_recovery_index import compute_lri

    cache: dict[str, float] = {}
    t0 = time.time()
    for i, date in enumerate(dates):
        try:
            r = compute_lri(db_path=db_path, target_date=date)
            cache[date] = round(float(r.lri), 4)
        except Exception:  # noqa: BLE001 - batch isolation: 1 ngày LRI lỗi không dừng precompute
            cache[date] = 1.0
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"  LRI precompute {i + 1}/{len(dates)} ({elapsed:.0f}s)")

    LRI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LRI_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=0)
    return cache


def _get_beta(conn: sqlite3.Connection, symbol: str) -> float:
    """Static beta vs VNINDEX from daily_ohlcv (regression of returns)."""
    try:
        rows = conn.execute(
            "SELECT a.close, b.close FROM daily_ohlcv a JOIN daily_ohlcv b "
            "ON a.date=b.date AND b.symbol='VNINDEX' WHERE a.symbol=? ORDER BY a.date DESC LIMIT 120",
            (symbol,),
        ).fetchall()
        if len(rows) < 30:
            return 1.0
        rets_stock: list[float] = []
        rets_ix: list[float] = []
        for i in range(len(rows) - 1):
            p0, p1 = rows[i + 1][0], rows[i][0]
            ix0, ix1 = rows[i + 1][1], rows[i][1]
            if p0 and p1 and ix0 and ix1 and p1 > 0 and ix1 > 0:
                rets_stock.append((p1 - p0) / p0)
                rets_ix.append((ix1 - ix0) / ix0)
        if len(rets_stock) < 30:
            return 1.0
        var_ix = float(np.var(rets_ix))
        if var_ix <= 0:
            return 1.0
        cov = float(np.cov(rets_stock, rets_ix)[0][1])
        return round(max(0.0, min(3.0, cov / var_ix)), 3)
    except sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, IndexError:
        return 1.0


def _get_mos_pct(conn: sqlite3.Connection, symbol: str, target_date: str) -> float:
    """PIT Margin of Safety (%) from valuation_scores z-scores (PE/PB)."""
    try:
        period = _date_to_period(target_date)
        rows = conn.execute(
            "SELECT ratio_name, z_score FROM fin.valuation_scores "
            "WHERE symbol=? AND ratio_name IN ('PE','PB') AND period<? "
            "ORDER BY period DESC LIMIT 2",
            (symbol, period),
        ).fetchall()
        zs = [float(r[1]) for r in rows if r[1] is not None]
        if not zs:
            return 0.0
        mos = 1.0 - (max(zs) / 3.0 + 0.5)
        return round(max(0.0, min(1.0, mos)) * 100, 1)
    except sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, IndexError:
        return 0.0


def _get_volume_avg_20d(conn: sqlite3.Connection, symbol: str, target_date: str) -> float:
    """20-day average volume up to target_date (PIT)."""
    try:
        rows = conn.execute(
            "SELECT volume FROM daily_ohlcv WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 20",
            (symbol, target_date),
        ).fetchall()
        vols = [float(r[0]) for r in rows if r[0]]
        return round(float(np.mean(vols)), 0) if vols else 0.0
    except sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, IndexError:
        return 0.0


def build_open_positions(conn: sqlite3.Connection, tracker, target_date: str) -> dict[str, dict]:
    """Build open_positions dict for EmergencyExitEngine from current tracker holdings."""
    open_positions: dict[str, dict] = {}
    for sym, shares in tracker.positions.items():
        entry_price = tracker.entry_prices.get(sym, 0.0)
        open_positions[sym] = {
            "shares": int(shares),
            "entry_price": entry_price,
            "beta": _get_beta(conn, sym),
            "mos_pct": _get_mos_pct(conn, sym, target_date),
            "volume_avg_20d": _get_volume_avg_20d(conn, sym, target_date),
        }
    return open_positions


def apply_emergency_exit(conn, tracker, target_date: str, lri_score: float) -> dict[str, str]:
    """Run EmergencyExitEngine. Returns {symbol: reason} for positions to force-sell."""
    if lri_score >= LRI_DEFENSIVE:
        return {}
    open_positions = build_open_positions(conn, tracker, target_date)
    if not open_positions:
        return {}
    engine = EmergencyExitEngine()
    result = engine.evaluate(lri_score=lri_score, open_positions=open_positions)
    orders = {}
    for o in result.exit_orders:
        orders[o.symbol] = o.reason
    return orders


def run_backtest_with_guard(
    scores: dict,
    dates: list[str],
    score_days: list[str],
    params: dict,
    db_path: str | None = None,
    lri_cache: dict[str, float] | None = None,
    capture_trade_log: bool = False,
    governor_cache: dict[str, dict] | None = None,
) -> dict:
    """Fast backtest with DecisionGuard (LRI + EmergencyExitEngine) integration.

    DecisionGuard layers applied each scoring day:
      - LRI DEFENSIVE (< 0.30): BUY LOCK (he_so=0.0) + EmergencyExitEngine
        force-sells high-risk positions (Beta/MoS ranked).
      - LRI PROBE (0.30-0.80): he_so = LRI (dimmer scaling).
      - LRI AGGRESSIVE (>= 0.80): full allocation.
      - Emergency tightened stop (-2%) applied to all holdings on DEFENSIVE days.

    governor_cache: optional {date: {"u", "confidence", "shock",
        "authority_modifier", "veto", "stress_index"}}. When provided, each
        entry's he_so is further scaled by combine_alloc_multiplier (Uncertainty
        Layer + Shock Detector + LAW-008 MarginStressNode via law_bridges).
        Keys authority_modifier/veto absent → default 1.0/False (backward compat).
        Absent → identical behaviour to LRI-only.

    capture_trade_log: True → bổ sung "trade_log" (có date) và "equity_curve" vào
    kết quả để xuất raw ledger (evidence — chống báo cáo không nguồn).
    """
    w_fund = params["w_fund"]
    w_macro = params["w_macro"]
    w_alpha = params["w_alpha"]
    w_behav = params["w_behav"]
    entry_thresh = params["entry_thresh"]
    exit_thresh = params["exit_thresh"]
    trailing_stop = params["trailing_stop"]
    trailing_take = params["trailing_take"]
    min_hold_days = params.get("min_hold_days", 5)
    sector_top_n = params.get("sector_top_n", 5)
    stocks_per_sector = params.get("stocks_per_sector", 2)
    max_positions = params.get("max_positions", 10)
    cash_reserve = params.get("cash_reserve", 0.05)
    max_buys_per_year = params.get("max_buys_per_year", 0)

    if db_path is None:
        db_path = str(SCREENER_DB_PATH)

    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")

    tracker = PortfolioTracker(
        initial_capital=100_000_000.0,
        max_positions=max_positions,
        cash_reserve=cash_reserve,
        trailing_stop=trailing_stop,
        trailing_take=trailing_take,
        entry_threshold=entry_thresh,
        exit_threshold=exit_thresh,
    )

    # Track entry date per symbol for Min Hold Period
    entry_dates: dict[str, str] = {}

    equity_curve = []
    score_set = set(score_days)
    lri_cache = lri_cache or {}
    governor_cache = governor_cache or {}

    # Guard statistics
    buy_locked_days = 0
    emergency_exits = 0
    dimmer_days = 0

    # Hard cap: tối đa N lệnh MUA trong cửa sổ trượt 365 ngày (0 = không giới hạn)
    buy_dates: list[str] = []

    for i, date in enumerate(dates):
        # Build current prices
        current_prices = {}
        for sym in tracker.positions:
            p = _get_close(conn, sym, date)
            if p:
                current_prices[sym] = p

        lri = lri_cache.get(date, 1.0)

        # ── DecisionGuard: EmergencyExitEngine (LRI DEFENSIVE) ──
        if lri < LRI_DEFENSIVE and tracker.positions:
            exit_orders = apply_emergency_exit(conn, tracker, date, lri)
            for sym, reason in exit_orders.items():
                price = _get_close(conn, sym, date)
                if price:
                    current_prices[sym] = price
                    tracker.sell(sym, price, 0.0, current_prices, date=date)
                    emergency_exits += 1
                    entry_dates.pop(sym, None)
            # Apply tightened stop to remaining holdings
            for sym in list(tracker.positions.keys()):
                price = _get_close(conn, sym, date)
                if not price:
                    continue
                entry = tracker.entry_prices.get(sym)
                if entry and (price - entry) / entry <= EMERGENCY_TIGHTENED_STOP:
                    current_prices[sym] = price
                    tracker.sell(sym, price, 0.0, current_prices, date=date)
                    emergency_exits += 1
                    entry_dates.pop(sym, None)

        if date in score_set and date in scores:
            day_scores = scores[date]

            # ── DecisionGuard: LRI dimmer scaling / BUY LOCK ──
            if lri < LRI_DEFENSIVE:
                he_so = 0.0
                buy_locked = True
                buy_locked_days += 1
            elif lri < LRI_PROBE:
                he_so = lri
                buy_locked = False
                dimmer_days += 1
            else:
                he_so = 1.0
                buy_locked = False

            # ── Governor Layer: fuse Uncertainty U + Shock severity into he_so ──
            if governor_cache:
                g = governor_cache.get(date)
                if g:
                    he_so = combine_alloc_multiplier(
                        he_so=he_so,
                        confidence=g.get("confidence", 1.0),
                        uncertainty=g.get("u", 0.0),
                        shock=g.get("shock", 0.0),
                        authority_modifier=g.get("authority_modifier", 1.0),
                        veto=g.get("veto", False),
                    )
                    if he_so <= 0.0 and not buy_locked:
                        buy_locked = True
                        buy_locked_days += 1

            # Get top sectors
            sector_macro = {}
            for sym_data in day_scores.values():
                sect = sym_data["sector"]
                if sect not in sector_macro:
                    sector_macro[sect] = sym_data["macro_eff"]
            top_sectors = sorted(sector_macro, key=lambda s: sector_macro[s], reverse=True)[:sector_top_n]

            # ── SELL (unless min_hold active) ──
            for sym in list(tracker.positions.keys()):
                price = _get_close(conn, sym, date)
                if not price:
                    continue
                entry = tracker.entry_prices.get(sym)
                data = day_scores.get(sym)
                if data:
                    composite = (
                        w_fund * data["fund"] + w_macro * data["macro_eff"] + w_alpha * data["alpha"] + w_behav * data["behav"]
                    )
                else:
                    composite = 0.5

                held = 0
                entry_date = entry_dates.get(sym)
                if entry_date:
                    held = sum(1 for d in dates if entry_date <= d <= date)

                should_sell = False
                if entry:
                    pnl = (price - entry) / entry
                    if pnl <= -trailing_stop:
                        should_sell = True
                    if pnl >= trailing_take:
                        should_sell = True
                if composite < exit_thresh and held >= min_hold_days:
                    should_sell = True

                # Sector rotation (respect min_hold)
                pos_sector = _get_sector(conn, sym)
                if pos_sector and pos_sector not in top_sectors and held >= min_hold_days:
                    should_sell = True

                if should_sell:
                    current_prices[sym] = price
                    tracker.sell(sym, price, composite, current_prices, date=date)
                    entry_dates.pop(sym, None)
                    current_prices = {s: current_prices[s] for s in tracker.positions if s in current_prices}

            # ── BUY (blocked when BUY LOCK active) ──
            if not buy_locked:
                buy_candidates: list[tuple] = []
                seen_sectors: dict[str, int] = {}
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

                # Hard cap: dừng tuyển mua nếu đã vượt max_buys_per_year trong 365 ngày trượt
                if max_buys_per_year > 0 and buy_dates:
                    cutoff = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=365)).isoformat()
                    buy_dates = [d for d in buy_dates if d >= cutoff]
                    if len(buy_dates) >= max_buys_per_year:
                        buy_candidates = []

                for sym, composite, sect in buy_candidates[:max_positions]:
                    if len(tracker.positions) >= max_positions:
                        break
                    price = _get_close(conn, sym, date)
                    if price:
                        current_prices[sym] = price
                        if tracker.buy(sym, price, composite, current_prices, alloc_multiplier=he_so, date=date):
                            entry_dates[sym] = date
                            buy_dates.append(date)

        # Mark to market
        nav = tracker.nav(current_prices)
        equity_curve.append(nav)

    conn.close()

    # ── Compute metrics ──
    curve = np.array(equity_curve) if equity_curve else np.array([100_000_000.0])
    initial_cap = float(tracker.initial_capital)
    total_ret = (curve[-1] / initial_cap) - 1.0
    daily_rets = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([0.0])
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) if np.std(daily_rets) > 0 else 0.0
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    max_dd = float(np.min(dd))
    ann_ret = annualize_cagr(total_ret, dates[0], dates[-1]) if len(dates) >= 2 else 0.0

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
        "buy_locked_days": buy_locked_days,
        "emergency_exits": emergency_exits,
        "dimmer_days": dimmer_days,
        **({"trade_log": tracker.trade_log, "equity_curve": [round(float(x), 2) for x in curve]} if capture_trade_log else {}),
    }


def generate_full_weight_grid(step: float = 0.10) -> list[dict]:
    """Generate ALL weight combinations in [0, 1] that sum to 1.0 (unbounded).

    Legacy v1 grid — dùng làm tham chiếu "full grid" khi so sánh bounded grid.
    """
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


def generate_weight_grid(ranges: dict | None = None, step: float = 0.05) -> list[dict]:
    """Generate weight combinations in bounded ranges that sum to 1.0.

    Default ranges implement the approved 5-Layer Matrix:
      M2(Fund) 0.35-0.50, M1(Macro) 0.10-0.20, Alpha 0.15-0.30, M3(Behav) 0.10-0.25.
    """
    ranges = ranges or DEFAULT_WEIGHT_RANGES
    weights: list[dict] = []
    lo_f, hi_f = ranges["w_fund"]
    lo_m, hi_m = ranges["w_macro"]
    lo_a, hi_a = ranges["w_alpha"]
    lo_b, hi_b = ranges["w_behav"]
    for w1 in np.arange(lo_f, hi_f + step, step):
        for w2 in np.arange(lo_m, hi_m + step, step):
            for w3 in np.arange(lo_a, hi_a + step, step):
                w4 = round(1.0 - w1 - w2 - w3, 6)
                if lo_b - 0.001 <= w4 <= hi_b + 0.001 and lo_f - 0.001 <= w1 <= hi_f + 0.001:
                    weights.append(
                        {
                            "w_fund": round(float(w1), 3),
                            "w_macro": round(float(w2), 3),
                            "w_alpha": round(float(w3), 3),
                            "w_behav": round(float(w4), 3),
                        }
                    )
    return weights


def _params_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def run_grid_search(
    scores: dict,
    dates: list[str],
    score_days: list[str],
    lri_cache: dict[str, float],
    step: float = 0.05,
    top_n: int = 15,
    workers: int = 1,
    db_path: str | None = None,
    checkpoint: Path | None = None,
) -> list[dict]:
    """Phase 2+3: Run staged grid search with parallel workers + checkpointing.

    Stage 1: weights only (bounded ranges) — default thresholds
    Stage 2: top-10 weight combos x entry/exit threshold grid
    Stage 3: top-5 full params x trailing stop/take grid x min-hold
    """
    if db_path is None:
        db_path = str(SCREENER_DB_PATH)

    # ── Load checkpoint ──
    done: dict[str, dict] = {}
    if checkpoint and checkpoint.exists():
        try:
            with open(checkpoint) as f:
                done = json.load(f)
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as exc:
            print(f"  [WARN] Checkpoint unreadable (starting fresh): {exc}")
            done = {}

    weight_combos = generate_weight_grid(step=step)
    print(f"  Weight combinations (bounded): {len(weight_combos)}")

    # ── Build 3-stage combo list ──
    combos: list[dict] = []

    # Stage 1: weights only
    for w in weight_combos:
        combos.append(
            {**w, "entry_thresh": 0.60, "exit_thresh": 0.35, "trailing_stop": 0.05, "trailing_take": 0.15, "min_hold_days": 5}
        )

    print(f"  [Stage 1] {len(combos)} weight combos (entry=0.60, exit=0.35, stop=-5%, take=+15%)")

    # ── Execute Stage 1 ──
    results: list[dict] = [r for r in done.values() if r.get("sharpe", -99) > -98]
    results = _run_combo_batch(
        combos,
        scores,
        dates,
        score_days,
        lri_cache,
        db_path,
        done,
        results,
        workers,
        checkpoint,
        label="Stage 1",
    )

    # Stage 2: top-10 weights x entry/exit thresholds
    stage1_ranked = sorted(results, key=lambda x: x["sharpe"], reverse=True)[:10]
    top_weights = [r["params"] for r in stage1_ranked if "error" not in r]
    combos2: list[dict] = []
    for w in top_weights:
        for entry in DEFAULT_ENTRY_THRESHOLDS:
            for exit_t in DEFAULT_EXIT_THRESHOLDS:
                combos2.append(
                    {
                        **w,
                        "entry_thresh": entry,
                        "exit_thresh": exit_t,
                        "trailing_stop": 0.05,
                        "trailing_take": 0.15,
                        "min_hold_days": 5,
                    }
                )
    print(f"\n  [Stage 2] {len(combos2)} threshold combos x top-10 weights")
    results = _run_combo_batch(
        combos2,
        scores,
        dates,
        score_days,
        lri_cache,
        db_path,
        done,
        results,
        workers,
        checkpoint,
        label="Stage 2",
    )

    # Stage 3: top-5 x stops/takes/min-hold
    stage2_ranked = sorted(results, key=lambda x: x["sharpe"], reverse=True)[:5]
    top_params = [r["params"] for r in stage2_ranked if "error" not in r]
    combos3: list[dict] = []
    for base in top_params:
        for stop in DEFAULT_TRAILING_STOPS:
            for take in DEFAULT_TRAILING_TAKES:
                for hold in DEFAULT_MIN_HOLDS:
                    combos3.append({**base, "trailing_stop": stop, "trailing_take": take, "min_hold_days": hold})
    print(f"\n  [Stage 3] {len(combos3)} stop/take/hold combos x top-5 params")
    results = _run_combo_batch(
        combos3,
        scores,
        dates,
        score_days,
        lri_cache,
        db_path,
        done,
        results,
        workers,
        checkpoint,
        label="Stage 3",
    )

    if checkpoint:
        with open(checkpoint, "w") as f:
            json.dump(done, f, indent=2, ensure_ascii=False)

    # ── Phase 3: Rank ──
    valid = [r for r in results if r.get("sharpe", -99) > -98]
    valid.sort(key=lambda x: x["sharpe"], reverse=True)
    print(f"\n  Total combos tested: {len(valid)}")
    return valid[:top_n]


def _run_combo_batch(
    combos: list[dict],
    scores: dict,
    dates: list[str],
    score_days: list[str],
    lri_cache: dict[str, float],
    db_path: str,
    done: dict[str, dict],
    results: list[dict],
    workers: int,
    checkpoint: Path | None,
    label: str,
) -> list[dict]:
    """Run a batch of combos (parallel or sequential), updating done + results."""
    pending = [c for c in combos if _params_key(c) not in done]
    print(f"  {label}: {len(combos)} total, {len(pending)} pending (checkpoint: {len(done)} done)")
    if not pending:
        return results

    t0 = time.time()

    def _collect(r: dict, c: dict) -> None:
        results.append(r)
        done[_params_key(c)] = r

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(run_backtest_with_guard, scores, dates, score_days, c, db_path, lri_cache): c for c in pending
            }
            for i, fut in enumerate(as_completed(futures), 1):
                c = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # noqa: BLE001 - batch isolation: 1 combo lỗi không dừng grid search
                    r = {"error": str(exc), "sharpe": -99}
                r["params"] = c
                _collect(r, c)
                _maybe_checkpoint(checkpoint, done, i)
                _maybe_progress(i, len(pending), results, t0)
    else:
        for i, c in enumerate(pending, 1):
            try:
                r = run_backtest_with_guard(scores, dates, score_days, c, db_path, lri_cache)
            except Exception as exc:  # noqa: BLE001 - batch isolation: 1 combo lỗi không dừng grid search
                r = {"error": str(exc), "sharpe": -99}
            r["params"] = c
            _collect(r, c)
            _maybe_checkpoint(checkpoint, done, i)
            _maybe_progress(i, len(pending), results, t0)

    return results


def _maybe_checkpoint(checkpoint: Path | None, done: dict[str, dict], i: int) -> None:
    if checkpoint and i % 25 == 0:
        with open(checkpoint, "w") as f:
            json.dump(done, f, indent=2, ensure_ascii=False)


def _maybe_progress(i: int, total: int, results: list[dict], t0: float) -> None:
    if i % 20 == 0:
        best = max((x for x in results if x.get("sharpe", -99) > -98), key=lambda x: x["sharpe"], default=None)
        elapsed = time.time() - t0
        best_s = f"Sharpe={best['sharpe']:.4f}" if best else "n/a"
        print(f"    {i}/{total} ({elapsed:.0f}s) | Best {best_s}")


def _print_top(results: list[dict], top_n: int = 15) -> None:
    print(f"\n{'=' * 100}")
    print(f"  TOP {min(top_n, len(results))} PARAMETER COMBINATIONS (by Sharpe Ratio)")
    print(f"{'=' * 100}")
    print(
        f"  {'Rank':<5} {'Sharpe':>8} {'Ret%':>9} {'MaxDD%':>8} {'WinR':>7} {'Trds':>6} "
        f"{'LRIlock':>8} | F/M/A/B | Entry Exit Stop Take Hold"
    )
    print(f"  {'-' * 96}")
    for rank, r in enumerate(results[:top_n], 1):
        p = r["params"]
        print(
            f"  {rank:<5} {r['sharpe']:>8.4f} {r['total_return']:>9.1f} "
            f"{r['max_drawdown']:>8.1f} {r['win_rate']:>6.1f}% {r['total_trades']:>6} "
            f"{r['buy_locked_days']:>8} | "
            f"{p['w_fund']:.2f}/{p['w_macro']:.2f}/{p['w_alpha']:.2f}/{p['w_behav']:.2f} "
            f"| {p['entry_thresh']:.2f} {p['exit_thresh']:.2f} "
            f"{p['trailing_stop']:.0%} {p['trailing_take']:.0%} {p['min_hold_days']}"
        )


def main(
    start: str = "2021-04-01",
    end: str = "2026-08-04",
    step: float = 0.05,
    top_n: int = 15,
    workers: int = 1,
    sample_every: int = 5,
) -> None:
    print("=" * 70)
    print("  GRID SEARCH V2: DecisionGuard (LRI + EmergencyExit) Optimization")
    print("=" * 70)
    print(f"  Period: {start} -> {end}")
    print(
        f"  Weights: M2(Fund)={DEFAULT_WEIGHT_RANGES['w_fund']}  M1(Macro)={DEFAULT_WEIGHT_RANGES['w_macro']}  "
        f"Alpha={DEFAULT_WEIGHT_RANGES['w_alpha']}  M3(Behav)={DEFAULT_WEIGHT_RANGES['w_behav']}"
    )
    print()

    db_path = str(SCREENER_DB_PATH)
    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    dates = _get_trading_days(conn, start, end)
    score_days = dates[::sample_every]
    conn.close()

    # Phase 0: LRI cache (PIT, cached to disk)
    print("[Phase 0] Pre-computing PIT LRI cache...")
    lri_cache = precompute_lri_cache(dates)

    # Phase 1: Pre-compute factor scores
    print("\n[Phase 1] Pre-computing factor scores...")
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    scores = precompute_scores(conn, dates, score_days)
    conn.close()

    # Phase 2+3: Grid search with DecisionGuard
    print("\n[Phase 2] Running grid search with DecisionGuard...")
    checkpoint = REPORTS_DIR / "grid_v2_checkpoint.json"
    top = run_grid_search(
        scores, dates, score_days, lri_cache, step=step, top_n=top_n, workers=workers, db_path=db_path, checkpoint=checkpoint
    )

    _print_top(top, top_n)

    # Save best params
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / "grid_search_v2_results.json"
    with open(report_path, "w") as f:
        json.dump({"top": top}, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved: {report_path}")

    if top:
        best = top[0]
        bp = best["params"]
        print(f"\n{'=' * 70}")
        print("  OPTIMAL PARAMETERS (DecisionGuard Grid Search)")
        print(f"{'=' * 70}")
        print(
            f"  Weights: M2(Fund)={bp['w_fund']:.2f}  M1(Macro)={bp['w_macro']:.2f}  "
            f"Alpha={bp['w_alpha']:.2f}  M3(Behav)={bp['w_behav']:.2f}"
        )
        print(
            f"  Entry={bp['entry_thresh']:.2f} Exit={bp['exit_thresh']:.2f} "
            f"Stop={bp['trailing_stop']:.0%} Take={bp['trailing_take']:.0%} Hold={bp['min_hold_days']}"
        )
        print(
            f"  Sharpe={best['sharpe']:.4f} Ret={best['total_return']:.1f}% "
            f"MaxDD={best['max_drawdown']:.1f}% WinRate={best['win_rate']:.1f}% "
            f"Trades={best['total_trades']}"
        )
        print(f"  LRI BUY LOCK days={best['buy_locked_days']} EmergencyExits={best['emergency_exits']}")
        print(f"{'=' * 70}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Grid Search V2 with DecisionGuard")
    ap.add_argument("--start", default="2021-04-01")
    ap.add_argument("--end", default="2026-08-04")
    ap.add_argument("--step", type=float, default=0.05)
    ap.add_argument("--top-n", type=int, default=15, dest="top_n")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--sample-every", type=int, default=5, dest="sample_every")
    args = ap.parse_args()
    main(
        start=args.start, end=args.end, step=args.step, top_n=args.top_n, workers=args.workers, sample_every=args.sample_every
    )
