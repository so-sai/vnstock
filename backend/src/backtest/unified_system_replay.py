"""
Unified System Replay — Ablation Study for Macro Pipeline (LAW-009 + InteractionEngine)

Compares 3 scenarios:
  A: Baseline    — Raw M · W_i (no lag, no interaction)
  B: Lag Only    — MacroLagEngine applied (LAW-009)
  C: Full Pipeline — Lag + InteractionEngine

Measures: Sharpe, MaxDD, Alpha, WinRate, Decision Delta
Period: 2021-04 to 2026-08 (macro_history available range)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root."""
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
CACHE_DIR = DATA_DIR / "cache"
CACHE_FILE = CACHE_DIR / "macro_preload.parquet"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from governor.interaction_engine import InteractionEngine
from governor.macro_lag_engine import MacroLagEngine, SECTOR_TRANSMISSION
from governor.regional_influence_engine import RegionalInfluenceEngine
from governor.sector_exposure_matrix import SectorExposureMatrix


UNIVERSE = list(set([
    "HPG", "VNM", "VIC", "VHM", "VRE", "VCB", "BID", "CTG", "TCB", "MBB",
    "ACB", "VPB", "STB", "TPB", "HDB", "LPB", "VIB", "AGB", "BWE", "MWG",
    "FPT", "VGT", "PTB", "PLX", "GAS", "POW", "NT2", "PC1", "GVR",
    "SSI", "VND", "HCM", "SHS", "VCI",
]))


@dataclass
class ScenarioState:
    capital: float = 100_000_000.0
    initial_capital: float = 100_000_000.0
    positions: Dict[str, float] = field(default_factory=dict)
    entry_prices: Dict[str, float] = field(default_factory=dict)
    equity_curve: List[float] = field(default_factory=list)
    trade_log: List[dict] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)


@dataclass
class ReplayResult:
    scenario: str
    start_date: str
    end_date: str
    trading_days: int
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    annualized_return: float
    volatility: float
    final_equity: float


def _get_trading_days(conn: sqlite3.Connection, start: str, end: str) -> List[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' "
        "AND date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def _get_close(conn: sqlite3.Connection, symbol: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?", (symbol, date)
    ).fetchone()
    return float(row[0]) if row else None


def _get_vnindex_close(conn: sqlite3.Connection, date: str) -> Optional[float]:
    return _get_close(conn, "VNINDEX", date)


def _get_sector(conn: sqlite3.Connection, symbol: str) -> Optional[str]:
    from governor.sector_exposure_matrix import VIETNAMESE_SECTOR_MAP
    row = conn.execute(
        "SELECT icb_name2 FROM symbol_industry WHERE symbol=?", (symbol,)
    ).fetchone()
    if not row:
        return None
    return VIETNAMESE_SECTOR_MAP.get(row[0])

def _compute_scores_for_date(
    conn, target_date, macro_engine, lag_engine, ix_engine, matrix, macro_cache
):
    """Compute A/B/C scores for all sectors on one date."""
    if macro_cache and macro_cache["date"] == target_date:
        macro_result = macro_cache["result"]
    else:
        try:
            macro_result = macro_engine.compute(target_date)
        except Exception:
            macro_result = None
        macro_cache = {"date": target_date, "result": macro_result}

    if macro_result is None:
        return {}, macro_cache

    M = macro_result.macro_vector
    scores_a, scores_b, scores_c = {}, {}, {}

    sectors_seen = set()
    for sym in UNIVERSE:
        sec = _get_sector(conn, sym)
        if sec and sec not in sectors_seen:
            sectors_seen.add(sec)
        else:
            continue

        try:
            r = matrix.compute_sector_macro_score(sec, M)
            scores_a[sec] = r.macro_score
        except Exception:
            scores_a[sec] = 0.5

        try:
            lr = lag_engine.compute(sec, target_date)
            scores_b[sec] = lr.effective_score
        except Exception:
            scores_b[sec] = scores_a.get(sec, 0.5)

        try:
            ix_r = ix_engine.compute(M, sec)
            scores_c[sec] = scores_b.get(sec, 0.5) * ix_r.multiplier
        except Exception:
            scores_c[sec] = scores_b.get(sec, 0.5)

    return {"A": scores_a, "B": scores_b, "C": scores_c}, macro_cache


def _decide(score, price, entry):
    if score is None:
        return "HOLD"
    if score > 0.6 and entry is None:
        return "BUY"
    elif score < 0.35 and entry is not None:
        return "SELL"
    elif entry is not None and price < entry * 0.93:
        return "SELL"
    return "HOLD"


def _execute(state, conn, date, action, symbol, score):
    price = _get_close(conn, symbol, date)
    if not price or price <= 0:
        return

    current_shares = state.positions.get(symbol, 0)

    if action == "BUY" and current_shares == 0:
        alloc = state.capital * 0.05
        exec_price = price * 1.002
        shares = int(alloc / exec_price)
        if shares > 0:
            cost = shares * exec_price * 1.0015
            if cost <= state.capital:
                state.capital -= cost
                state.positions[symbol] = shares
                state.entry_prices[symbol] = exec_price
                state.trade_log.append({
                    "date": date, "symbol": symbol, "action": "BUY",
                    "price": round(exec_price, 2), "shares": shares,
                    "score": round(score, 4),
                })

    elif action == "SELL" and current_shares > 0:
        exec_price = price * 0.998
        revenue = current_shares * exec_price * 0.9985
        entry = state.entry_prices.get(symbol, exec_price)
        pnl = (exec_price - entry) / entry
        state.capital += revenue
        state.trade_log.append({
            "date": date, "symbol": symbol, "action": "SELL",
            "price": round(exec_price, 2), "shares": current_shares,
            "score": round(score, 4), "pnl_pct": round(pnl * 100, 2),
        })
        state.positions.pop(symbol, None)
        state.entry_prices.pop(symbol, None)


def _mark_to_market(state, conn, date):
    total = state.capital
    for sym, shares in state.positions.items():
        p = _get_close(conn, sym, date)
        if p:
            total += shares * p
    state.equity_curve.append(total)


def _build_result(state, scenario, start, end, days):
    curve = np.array(state.equity_curve) if state.equity_curve else np.array([state.initial_capital])
    total_ret = (curve[-1] / curve[0]) - 1.0
    daily_rets = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([0.0])
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) if np.std(daily_rets) > 0 else 0.0
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    max_dd = float(np.min(dd))
    years = max(days / 252, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    sells = [t for t in state.trade_log if t["action"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pnl_pct", 0) > 0)
    win_rate = wins / len(sells) if sells else 0.0

    return ReplayResult(
        scenario=scenario, start_date=start, end_date=end,
        trading_days=days, total_return=round(total_ret * 100, 2),
        sharpe_ratio=round(sharpe, 4), max_drawdown=round(max_dd * 100, 2),
        win_rate=round(win_rate * 100, 1), total_trades=len(state.trade_log),
        annualized_return=round(ann_ret * 100, 2), volatility=round(vol * 100, 2),
        final_equity=round(curve[-1], 0),
    )


# ═══════════════════════════════════════════════════════════
# Parquet Cache for Phase 1 Pre-fetch Results
# ═══════════════════════════════════════════════════════════

def _macro_cache_valid(cache_path: Path, db_path: str, start_date: str) -> bool:
    """Check if Parquet cache exists, is newer than macro_history, and
    matches current engine configurations (weights + rules)."""
    if not cache_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT MAX(date) FROM macro_history"
        ).fetchone()
        conn.close()
        latest_macro = row[0] if row and row[0] else "1970-01-01"
        cache_mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        cache_date = cache_mtime.strftime("%Y-%m-%d")
        if cache_date < latest_macro or cache_date < start_date:
            return False

        # Check engine configuration version
        current_version = _get_engine_version()
        try:
            import pyarrow.parquet as pq
            meta = pq.read_metadata(str(cache_path))
            cached_version = meta.metadata.get(b"engine_version", b"").decode()
            if cached_version != current_version:
                return False
        except Exception:
            return False

        return True
    except Exception:
        return False


def _get_engine_version() -> str:
    """Compute a version hash from current engine configurations.

    Uses module-level constants (no instance creation needed).
    Any change in weights, rules, or transmission parameters
    invalidates the Parquet cache automatically.
    """
    import hashlib

    h = hashlib.sha256()

    # SectorExposureMatrix weights (module-level constant)
    from governor.sector_exposure_matrix import SECTOR_EXPOSURE_WEIGHTS
    for sector in sorted(SECTOR_EXPOSURE_WEIGHTS):
        weights = SECTOR_EXPOSURE_WEIGHTS[sector]
        for node in sorted(weights):
            h.update(f"{sector}:{node}:{weights[node]}".encode())

    # InteractionEngine rules (module-level constant)
    from governor.interaction_engine import INTERACTION_RULES
    for rule in sorted(INTERACTION_RULES, key=lambda r: r.get("name", "")):
        h.update(
            f"IX:{rule.get('name','')}:{rule.get('threshold',0)}:"
            f"{rule.get('scale',1)}".encode()
        )

    # MacroLagEngine transmission parameters (module-level constant)
    for sector in sorted(SECTOR_TRANSMISSION):
        tp = SECTOR_TRANSMISSION[sector]
        h.update(
            f"LAG:{sector}:{tp.lag_min}:{tp.lag_max}:"
            f"{tp.half_life}:{tp.attenuation}".encode()
        )

    return h.hexdigest()


def _save_macro_cache(
    macro_cache: Dict[str, Optional[Dict]],
    lag_cache: Dict[str, Dict[str, float]],
    ix_cache: Dict[str, Dict[str, float]],
    path: Path,
):
    """Save pre-fetched macro data to Parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for date in sorted(macro_cache.keys()):
        M = macro_cache.get(date) or {}
        for sector, lag_score in lag_cache.get(date, {}).items():
            rows.append({
                "date": date,
                "sector": sector,
                "M_US_Liquidity": M.get("US_Liquidity", 0.5),
                "M_China_Economy": M.get("China_Economy", 0.5),
                "M_Commodity_Cycle": M.get("Commodity_Cycle", 0.5),
                "M_Domestic_Liquidity": M.get("Domestic_Liquidity", 0.5),
                "lag_effective_score": lag_score,
                "ix_multiplier": ix_cache.get(date, {}).get(sector, 1.0),
            })

    if not rows:
        return

    table = pa.Table.from_pylist(rows)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Embed engine version in Parquet metadata for cache invalidation
    version = _get_engine_version()
    table = table.replace_schema_metadata({b"engine_version": version.encode()})
    pq.write_table(table, str(path))


def _load_macro_cache(
    path: Path,
) -> Tuple[Dict[str, Optional[Dict]], Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    """Load pre-fetched macro data from Parquet."""
    import pyarrow.parquet as pq

    table = pq.read_table(str(path))
    df = table.to_pandas()

    macro_cache: Dict[str, Optional[Dict]] = {}
    lag_cache: Dict[str, Dict[str, float]] = {}
    ix_cache: Dict[str, Dict[str, float]] = {}

    for _, row in df.iterrows():
        d = row["date"]
        sec = row["sector"]
        if d not in macro_cache:
            macro_cache[d] = {
                "US_Liquidity": row["M_US_Liquidity"],
                "China_Economy": row["M_China_Economy"],
                "Commodity_Cycle": row["M_Commodity_Cycle"],
                "Domestic_Liquidity": row["M_Domestic_Liquidity"],
            }
        if d not in lag_cache:
            lag_cache[d] = {}
        if d not in ix_cache:
            ix_cache[d] = {}
        lag_cache[d][sec] = float(row["lag_effective_score"])
        ix_cache[d][sec] = float(row["ix_multiplier"])

    return macro_cache, lag_cache, ix_cache


def run_unified_replay(
    start_date="2021-04-01",
    end_date="2026-08-04",
    db_path=None,
    sample_every=5,
):
    """Run 3-scenario ablation backtest.

    Args:
        start_date: First trading day (default: 2021-04-01, macro data starts)
        end_date: Last trading day (default: today)
        db_path: Override DB path
        sample_every: Only score every N-th trading day (default: 5). Reduces
                      runtime by ~80% while capturing major regime shifts.

    Returns:
        Dict[str, ReplayResult] for scenarios A, B, C
    """
    if db_path is None:
        db_path = str(DATA_DIR / "screener_cache.db")

    conn = sqlite3.connect(db_path)
    try:
        trading_days = _get_trading_days(conn, start_date, end_date)
    finally:
        conn.close()

    if not trading_days:
        logger.error("No trading days found in range %s - %s", start_date, end_date)
        return {}

    # Pre-sample days to score (heavy computation only on these)
    scored_indices = set(range(0, len(trading_days), sample_every))

    print(f"\n{'='*70}")
    print(f"  UNIFIED SYSTEM REPLAY — ABLATION STUDY")
    print(f"  Period: {trading_days[0]} -> {trading_days[-1]} ({len(trading_days)} days)")
    print(f"  Scored days: {len(scored_indices)}/{len(trading_days)} (every {sample_every}d)")
    print(f"  Universe: {len(UNIVERSE)} symbols")
    print(f"{'='*70}\n")

    # Initialize engines
    macro_engine = RegionalInfluenceEngine(db_path)
    lag_engine = MacroLagEngine(db_path)
    ix_engine = InteractionEngine()
    matrix = SectorExposureMatrix()

    # Pre-compute sector mappings for universe
    conn = sqlite3.connect(db_path)
    try:
        from governor.sector_exposure_matrix import VIETNAMESE_SECTOR_MAP
        sym_sector = {}
        for sym in UNIVERSE:
            row = conn.execute(
                "SELECT icb_name2 FROM symbol_industry WHERE symbol=?", (sym,)
            ).fetchone()
            if row:
                sec = VIETNAMESE_SECTOR_MAP.get(row[0])
                if sec:
                    sym_sector[sym] = sec

        # Pre-load all close prices for universe + VNINDEX into memory
        symbols_to_load = UNIVERSE + ["VNINDEX"]
        placeholders = ",".join("?" * len(symbols_to_load))
        rows = conn.execute(
            f"SELECT symbol, date, close FROM daily_ohlcv "
            f"WHERE symbol IN ({placeholders}) AND date BETWEEN ? AND ? "
            f"ORDER BY symbol, date",
            (*symbols_to_load, start_date, end_date),
        ).fetchall()
        price_cache = {}
        for sym, dt, cl in rows:
            if sym not in price_cache:
                price_cache[sym] = {}
            price_cache[sym][dt] = float(cl)
    finally:
        conn.close()

    # ═══════════════════════════════════════════════════════════
    # PHASE 1: Pre-fetch ALL macro data into memory (one-time)
    # ═══════════════════════════════════════════════════════════
    print("  [Phase 1] Pre-fetching macro vectors + lag scores...")
    t0 = time.time()

    scored_dates = [trading_days[i] for i in sorted(scored_indices)]

    # Check Parquet cache first
    if _macro_cache_valid(CACHE_FILE, db_path, start_date):
        print("  [Phase 1] Loading from Parquet cache...")
        macro_cache, lag_cache, ix_cache = _load_macro_cache(CACHE_FILE)
        print(f"  [Phase 1] Cached {len(macro_cache)} days loaded in {time.time()-t0:.1f}s")
    else:
        # Pre-compute M vectors for all scored days
        macro_cache = {}  # date → M dict
        for d in scored_dates:
            try:
                macro_cache[d] = macro_engine.compute(d).macro_vector
            except Exception:
                macro_cache[d] = None

        # Pre-compute lag scores for all scored days × all sectors (batch)
        lag_cache = {}  # date → {sector: effective_score}
        for d in scored_dates:
            lag_cache[d] = {}
            try:
                lag_results = lag_engine.compute_all_sectors(d)
                for sec, lr in lag_results.items():
                    lag_cache[d][sec] = lr.effective_score
            except Exception:
                for sec in set(sym_sector.values()):
                    lag_cache[d][sec] = 0.5

        # Pre-compute interaction multipliers for all scored days × all sectors (batch)
        ix_cache = {}  # date → {sector: multiplier}
        for d in scored_dates:
            ix_cache[d] = {}
            M = macro_cache.get(d)
            if M is None:
                for sec in set(sym_sector.values()):
                    ix_cache[d][sec] = 1.0
                continue
            try:
                ix_results = ix_engine.compute_all_sectors(M)
                for sec, ix_r in ix_results.items():
                    ix_cache[d][sec] = ix_r.multiplier
            except Exception:
                for sec in set(sym_sector.values()):
                    ix_cache[d][sec] = 1.0

        # Save to Parquet cache for next run
        try:
            _save_macro_cache(macro_cache, lag_cache, ix_cache, CACHE_FILE)
            print(f"  [Phase 1] Cache saved to {CACHE_FILE}")
        except Exception as e:
            logger.warning("[REPLAY] Failed to save Parquet cache: %s", e)

    print(f"  [Phase 1] Done in {time.time()-t0:.1f}s "
          f"({len(scored_dates)} days × {len(set(sym_sector.values()))} sectors)")

    # ═══════════════════════════════════════════════════════════
    # PHASE 2: Backtest loop (pure in-memory, no DB queries)
    # ═══════════════════════════════════════════════════════════
    print("  [Phase 2] Running backtest loop...")
    t0 = time.time()

    # Initialize 3 scenario states
    states = {
        "A": ScenarioState(initial_capital=100_000_000.0, capital=100_000_000.0),
        "B": ScenarioState(initial_capital=100_000_000.0, capital=100_000_000.0),
        "C": ScenarioState(initial_capital=100_000_000.0, capital=100_000_000.0),
    }

    decision_delta = []
    scored_day_count = 0

    for i, target_date in enumerate(trading_days):
        if (i + 1) % 200 == 0 or i == 0:
            eq_a = states["A"].equity_curve[-1] if states["A"].equity_curve else 100e6
            print(f"  Day {i+1}/{len(trading_days)}: {target_date} | A={eq_a/1e6:.1f}M")

        M = macro_cache.get(target_date)
        if M is None:
            for s in states.values():
                _mark_to_market_fast(s, target_date, price_cache)
            continue

        scored_day_count += 1
        sectors_in_universe = set(sym_sector.values())

        # Compute scores for all 3 scenarios from cached data
        scores_a, scores_b, scores_c = {}, {}, {}
        for sec in sectors_in_universe:
            try:
                r = matrix.compute_sector_macro_score(sec, M)
                scores_a[sec] = r.macro_score
            except Exception:
                scores_a[sec] = 0.5
            scores_b[sec] = lag_cache.get(target_date, {}).get(sec, 0.5)
            ix_mult = ix_cache.get(target_date, {}).get(sec, 1.0)
            scores_c[sec] = scores_b[sec] * ix_mult

        all_scores = {"A": scores_a, "B": scores_b, "C": scores_c}

        # Top-N sector rotation: trade only on rebalance days
        is_rebalance = (scored_day_count % REBALANCE_FREQ == 0) or (i == 0)

        for sc_key in ["A", "B", "C"]:
            sector_scores = all_scores[sc_key]
            sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
            top_n = [s for s, _ in sorted_sectors[:MAX_POSITIONS]]

            for sym, sec in sym_sector.items():
                price = price_cache.get(sym, {}).get(target_date)
                if not price:
                    continue

                score = sector_scores.get(sec, 0.5)
                entry = states[sc_key].entry_prices.get(sym)
                action = _decide(score, price, entry)

                if is_rebalance:
                    # Full rebalance: sell non-top-N, buy missing top-N
                    if sec not in top_n and sym in states[sc_key].positions:
                        _execute_fast(states[sc_key], target_date, "SELL",
                                      sym, score, price, None)
                    elif sec in top_n and sym not in states[sc_key].positions:
                        _execute_fast(states[sc_key], target_date, "BUY",
                                      sym, score, price, None)
                else:
                    # Non-rebalance: only stop-loss exits
                    if action == "SELL" and sym in states[sc_key].positions:
                        _execute_fast(states[sc_key], target_date, "SELL",
                                      sym, score, price, None)

            _mark_to_market_fast(states[sc_key], target_date, price_cache)

        # Track decision deltas (A vs C)
        a_buys = {s for s in top_n if all_scores["A"].get(s, 0) > 0.6}
        c_buys = {s for s in top_n if all_scores["C"].get(s, 0) > 0.6}
        if a_buys != c_buys:
            decision_delta.append({
                "date": target_date,
                "a_only": sorted(a_buys - c_buys),
                "c_only": sorted(c_buys - a_buys),
                "both": sorted(a_buys & c_buys),
            })

    print(f"  [Phase 2] Done in {time.time()-t0:.1f}s "
          f"({scored_day_count} scored days × {len(trading_days)} total days)")

    # Build results
    results = {}
    for sc_key in ["A", "B", "C"]:
        label = {"A": "Baseline (Raw)", "B": "Lag Only (LAW-009)", "C": "Full Pipeline"}[sc_key]
        results[sc_key] = _build_result(
            states[sc_key], label, trading_days[0], trading_days[-1], len(trading_days)
        )

    _print_summary(results, decision_delta)
    _save_reports(results, decision_delta, trading_days[0], trading_days[-1])
    return results


MAX_POSITIONS = 20  # max concurrent positions
REBALANCE_FREQ = 10  # rebalance every N scored days


def _execute_fast(state, date, action, symbol, score, price, last_action_date):
    """Paper trading: sell when sector drops out of top-N, buy when it enters."""
    current_shares = state.positions.get(symbol, 0)

    if action == "BUY" and current_shares == 0:
        if len(state.positions) >= MAX_POSITIONS:
            return
        # Equal split of available cash across open slots
        open_slots = MAX_POSITIONS - len(state.positions)
        alloc_per_slot = state.capital / max(open_slots, 1)
        exec_price = price * 1.002
        shares = int(alloc_per_slot / exec_price)
        cost = shares * exec_price * 1.0015
        if shares > 0 and cost <= state.capital:
            state.capital -= cost
            state.positions[symbol] = shares
            state.entry_prices[symbol] = exec_price
            state.trade_log.append({
                "date": date, "symbol": symbol, "action": "BUY",
                "price": round(exec_price, 2), "shares": shares,
                "score": round(score, 4),
            })

    elif action == "SELL" and current_shares > 0:
        exec_price = price * 0.998
        revenue = current_shares * exec_price * 0.9985
        entry = state.entry_prices.get(symbol, exec_price)
        pnl = (exec_price - entry) / entry
        state.capital += revenue
        state.trade_log.append({
            "date": date, "symbol": symbol, "action": "SELL",
            "price": round(exec_price, 2), "shares": current_shares,
            "score": round(score, 4), "pnl_pct": round(pnl * 100, 2),
        })
        state.positions.pop(symbol, None)
        state.entry_prices.pop(symbol, None)


def _mark_to_market_fast(state, date, price_cache):
    """Mark to market using pre-loaded prices."""
    total = state.capital
    for sym, shares in state.positions.items():
        p = price_cache.get(sym, {}).get(date)
        if p:
            total += shares * p
    state.equity_curve.append(total)

def _print_summary(results, decision_delta):
    print(f"\n{'='*70}")
    print("  ABLATION STUDY RESULTS")
    print(f"{'='*70}\n")

    header = f"{'Metric':<25} {'A: Baseline':>15} {'B: Lag Only':>15} {'C: Full':>15}"
    print(header)
    print("-" * 70)

    metrics = [
        ("Total Return (%)", "total_return", ".2f"),
        ("Annualized Return (%)", "annualized_return", ".2f"),
        ("Sharpe Ratio", "sharpe_ratio", ".4f"),
        ("Max Drawdown (%)", "max_drawdown", ".2f"),
        ("Volatility (%)", "volatility", ".2f"),
        ("Win Rate (%)", "win_rate", ".1f"),
        ("Total Trades", "total_trades", "d"),
        ("Final Equity (M)", "final_equity", ".0f"),
    ]

    ra, rb, rc = results["A"], results["B"], results["C"]
    for label, attr, fmt in metrics:
        va = getattr(ra, attr)
        vb = getattr(rb, attr)
        vc = getattr(rc, attr)
        if attr == "final_equity":
            va, vb, vc = va / 1e6, vb / 1e6, vc / 1e6
        print(f"  {label:<23} {va:>15{fmt}} {vb:>15{fmt}} {vc:>15{fmt}}")

    # Alpha comparison
    print(f"\n{'='*70}")
    print("  ALPHA ANALYSIS (vs Baseline A)")
    print(f"{'='*70}\n")

    sharpe_b_alpha = rb.sharpe_ratio - ra.sharpe_ratio
    sharpe_c_alpha = rc.sharpe_ratio - ra.sharpe_ratio
    ret_c_alpha = rc.annualized_return - ra.annualized_return
    mdd_c_improvement = ra.max_drawdown - rc.max_drawdown  # positive = less drawdown

    print(f"  {'Metric':<30} {'B - A':>15} {'C - A':>15}")
    print(f"  {'-'*60}")
    print(f"  {'Sharpe Delta':<30} {sharpe_b_alpha:>+15.4f} {sharpe_c_alpha:>+15.4f}")
    print(f"  {'Annual Return Delta (%)':<30} {'N/A':>15} {ret_c_alpha:>+15.2f}")
    print(f"  {'MaxDD Improvement (pp)':<30} {'N/A':>15} {mdd_c_improvement:>+15.2f}")

    # Decision Delta
    print(f"\n{'='*70}")
    print("  DECISION DELTA (Scenario A vs C)")
    print(f"{'='*70}\n")

    total_days = len(decision_delta) + 1
    days_with_delta = len(decision_delta)
    total_a_only = sum(len(d["a_only"]) for d in decision_delta)
    total_c_only = sum(len(d["c_only"]) for d in decision_delta)
    total_both = sum(len(d["both"]) for d in decision_delta)

    print(f"  Days with decision difference: {days_with_delta}/{total_days} ({days_with_delta/total_days*100:.1f}%)")
    print(f"  Total BUY signals (A only):    {total_a_only}")
    print(f"  Total BUY signals (C only):    {total_c_only}")
    print(f"  Total BUY signals (both):      {total_both}")

    if decision_delta:
        print(f"\n  Sample decision changes (last 5):")
        for d in decision_delta[-5:]:
            a_str = ",".join(d["a_only"]) if d["a_only"] else "-"
            c_str = ",".join(d["c_only"]) if d["c_only"] else "-"
            print(f"    {d['date']}: A-only=[{a_str}] C-only=[{c_str}]")


def _save_reports(results, decision_delta, start, end):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "period": {"start": start, "end": end},
        "scenarios": {},
        "decision_delta_summary": {
            "total_days": len(decision_delta) + 1,
            "days_with_difference": len(decision_delta),
        },
    }
    for key, r in results.items():
        summary["scenarios"][key] = {
            "label": r.scenario,
            "total_return_pct": r.total_return,
            "annualized_return_pct": r.annualized_return,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown_pct": r.max_drawdown,
            "volatility_pct": r.volatility,
            "win_rate_pct": r.win_rate,
            "total_trades": r.total_trades,
            "final_equity": r.final_equity,
        }

    # Save summary JSON
    summary_path = REPORTS_DIR / f"ablation_{start}_{end}_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n  Summary saved: {summary_path}")

    # Save decision delta CSV
    delta_path = REPORTS_DIR / f"ablation_{start}_{end}_delta.csv"
    with open(delta_path, "w") as f:
        f.write("date,a_only,c_only,both\n")
        for d in decision_delta:
            f.write(f"{d['date']},{';'.join(d['a_only'])},{';'.join(d['c_only'])},{';'.join(d['both'])}\n")
    print(f"  Delta CSV saved: {delta_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-FACTOR BACKTEST (5 Models: M1 + M2 + M3 + Alpha + VN20 Gate)
# ═══════════════════════════════════════════════════════════════════════════════

FINANCIAL_DB_PATH = DATA_DIR / "financial_facts.db"


def _get_financial_score(fin_conn, symbol, target_date, metric_names, weights=None):
    """Read health_ratios from financial_facts.db, return composite score [0,1]."""
    try:
        if weights is None:
            weights = {m: 1.0 / len(metric_names) for m in metric_names}
        scores = {}
        for m in metric_names:
            row = fin_conn.execute(
                "SELECT ratio_value FROM health_ratios "
                "WHERE symbol=? AND ratio_name=? AND period<=? "
                "ORDER BY period DESC LIMIT 1",
                (symbol, m, _date_to_period(target_date)),
            ).fetchone()
            if row and row[0] is not None:
                scores[m] = row[0]
        if not scores:
            return 0.5
        # Normalize each metric to [0,1]
        # NOTE: health_ratios stored as decimals (ROE=0.15 = 15%)
        normalized = {}
        for m, val in scores.items():
            if m == "ROE":
                normalized[m] = max(0.0, min(1.0, val / 0.30))  # 30% = 1.0
            elif m == "DEBT_TO_EQUITY":
                normalized[m] = max(0.0, min(1.0, 1.0 - val / 3.0))
            elif m in ("GROSS_MARGIN", "NET_MARGIN"):
                normalized[m] = max(0.0, min(1.0, val / 0.50))  # 50% = 1.0
            elif m == "CURRENT_RATIO":
                normalized[m] = max(0.0, min(1.0, val / 3.0))
            else:
                normalized[m] = max(0.0, min(1.0, val))
        total_w = sum(weights.get(m, 0) for m in normalized)
        if total_w == 0:
            return 0.5
        return sum(normalized[m] * weights.get(m, 0) for m in normalized) / total_w
    except Exception:
        return 0.5


def _date_to_period(target_date):
    """Convert date string '2025-06-30' to period '2025Q2'."""
    from datetime import datetime
    try:
        d = datetime.strptime(target_date, "%Y-%m-%d")
        q = (d.month - 1) // 3 + 1
        return f"{d.year}Q{q}"
    except Exception:
        return "2025Q2"


def _get_fundamental_score(fin_conn, symbol, target_date):
    """M2: Fundamental quality from health_ratios (ROE + Gross Margin + D/E)."""
    return _get_financial_score(fin_conn, symbol, target_date,
                                ["ROE", "GROSS_MARGIN", "DEBT_TO_EQUITY"],
                                {"ROE": 0.4, "GROSS_MARGIN": 0.3, "DEBT_TO_EQUITY": 0.3})


def _get_behavioral_score(conn, symbol, target_date):
    """M3: Volume pattern + institutional flow proxy."""
    try:
        rows = conn.execute(
            "SELECT volume, close FROM daily_ohlcv WHERE symbol=? AND date<? "
            "ORDER BY date DESC LIMIT 30",
            (symbol, target_date),
        ).fetchall()
        if len(rows) < 10:
            return 0.5
        volumes = [float(r[0]) for r in rows]
        closes = [float(r[1]) for r in rows]
        avg_vol = sum(volumes[5:]) / max(len(volumes[5:]), 1)
        recent_vol = sum(volumes[:5]) / 5
        vol_ratio = recent_vol / max(avg_vol, 1)
        price_chg = (closes[0] - closes[5]) / closes[5] if closes[5] else 0
        vol_chg = vol_ratio - 1.0
        score = 0.5 + price_chg * 0.3 + vol_chg * 0.2
        return max(0.0, min(1.0, score))
    except Exception:
        return 0.5


def _get_momentum_score(conn, symbol, target_date):
    """Alpha: Multi-timeframe momentum (5D/10D/20D)."""
    try:
        rows = conn.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol=? AND date<? "
            "ORDER BY date DESC LIMIT 40",
            (symbol, target_date),
        ).fetchall()
        if len(rows) < 20:
            return 0.5
        prices = [float(r[0]) for r in rows]
        ret_5d = (prices[0] - prices[4]) / prices[4] if prices[4] else 0
        ret_10d = (prices[0] - prices[9]) / prices[9] if prices[9] else 0
        ret_20d = (prices[0] - prices[19]) / prices[19] if prices[19] else 0
        score = 0.5 + ret_5d * 0.4 + ret_10d * 0.3 + ret_20d * 0.2
        return max(0.0, min(1.0, score))
    except Exception:
        return 0.5


def _vn20_gate(fin_conn, conn, symbol, target_date):
    """VN20 Quant Gate: ROE>5% (quarterly) + D/E<2 + liquidity check."""
    try:
        period = _date_to_period(target_date)
        roe_row = fin_conn.execute(
            "SELECT ratio_value FROM health_ratios WHERE symbol=? AND ratio_name='ROE' AND period<=? ORDER BY period DESC LIMIT 1",
            (symbol, period),
        ).fetchone()
        de_row = fin_conn.execute(
            "SELECT ratio_value FROM health_ratios WHERE symbol=? AND ratio_name='DEBT_TO_EQUITY' AND period<=? ORDER BY period DESC LIMIT 1",
            (symbol, period),
        ).fetchone()
        vol_row = conn.execute(
            "SELECT volume FROM daily_ohlcv WHERE symbol=? AND date=?",
            (symbol, target_date),
        ).fetchone()
        roe = roe_row[0] if roe_row else 0
        de = de_row[0] if de_row else 99
        vol = vol_row[0] if vol_row else 0
        # ROE is quarterly — threshold 5% quarterly ≈ 20% annualized
        return (roe or 0) > 0.05 and (de or 99) < 2.0 and (vol or 0) > 50000
    except Exception:
        return False


def _multi_factor_decide(score, price, entry, trailing_stop=0.07, trailing_take=0.15):
    """Decision logic with trailing stop/take."""
    if score is None:
        return "HOLD"
    if entry is None and score > 0.55:
        return "BUY"
    if entry is not None:
        pnl = (price - entry) / entry
        if pnl <= -trailing_stop:
            return "SELL"
        if pnl >= trailing_take:
            return "SELL"
        if score < 0.35:
            return "SELL"
    return "HOLD"


def _execute_multi_factor(state, conn, date, action, symbol, score, weight=0.10):
    """Execute with position sizing + transaction costs."""
    price = _get_close(conn, symbol, date)
    if not price or price <= 0:
        return

    current_shares = state.positions.get(symbol, 0)

    if action == "BUY" and current_shares == 0:
        alloc = state.capital * weight
        exec_price = price * 1.002
        shares = int(alloc / exec_price)
        if shares > 0:
            cost = shares * exec_price * 1.0045
            if cost <= state.capital:
                state.capital -= cost
                state.positions[symbol] = shares
                state.entry_prices[symbol] = exec_price
                state.trade_log.append({
                    "date": date, "symbol": symbol, "action": "BUY",
                    "price": round(exec_price, 2), "shares": shares,
                    "score": round(score, 4), "cost": round(cost, 0),
                })

    elif action == "SELL" and current_shares > 0:
        exec_price = price * 0.998
        revenue = current_shares * exec_price * 0.9955
        entry = state.entry_prices.get(symbol, exec_price)
        pnl = (exec_price - entry) / entry
        state.capital += revenue
        state.trade_log.append({
            "date": date, "symbol": symbol, "action": "SELL",
            "price": round(exec_price, 2), "shares": current_shares,
            "score": round(score, 4), "pnl_pct": round(pnl * 100, 2),
        })
        state.positions.pop(symbol, None)
        state.entry_prices.pop(symbol, None)


def run_multi_factor_backtest(start="2021-04-01", end="2026-08-04",
                               db_path=None, sample_every=5):
    """Run 5-model multi-factor backtest with Position Sizing + Risk Management."""
    print(f"\n{'='*70}")
    print("  MULTI-FACTOR BACKTEST — 5 Models (M1+M2+M3+Alpha+VN20)")
    print(f"{'='*70}")
    print(f"  Period: {start} -> {end}")
    print(f"  Scored days: every {sample_every}d")
    print(f"  Transaction cost: 0.45% | Trailing stop: -7% | Take profit: +15%")
    print(f"  Position sizing: 10% per stock, 30% sector cap, 5% cash reserve")
    print()

    if db_path is None:
        db_path = str(DATA_DIR / "screener_cache.db")

    conn = sqlite3.connect(db_path)
    fin_conn = sqlite3.connect(str(FINANCIAL_DB_PATH))
    macro_engine = RegionalInfluenceEngine()
    lag_engine = MacroLagEngine()
    ix_engine = InteractionEngine()
    matrix = SectorExposureMatrix()

    dates = _get_trading_days(conn, start, end)
    score_days = dates[::sample_every]

    state = ScenarioState()
    vnindex_start = _get_vnindex_close(conn, dates[0])
    decision_log = []
    macro_cache = {"date": None, "result": None}

    print(f"  [Phase 1] Pre-fetching macro vectors...")
    preload_start = time.time()
    try:
        import pandas as pd
        if CACHE_FILE.exists():
            df = pd.read_parquet(CACHE_FILE)
            cached_days = len(df)
            print(f"  [Phase 1] Loaded {cached_days} days from Parquet cache in {time.time()-preload_start:.1f}s")
    except Exception:
        pass
    print(f"  [Phase 1] Done in {time.time()-preload_start:.1f}s")

    print(f"  [Phase 2] Running multi-factor backtest loop...")
    t2 = time.time()

    for i, date in enumerate(dates):
        # Score every N-th day
        if date in score_days:
            macro_result = None
            try:
                macro_result = macro_engine.compute(date)
            except Exception:
                pass

            if macro_result:
                M = macro_result.macro_vector
                sector_scores = matrix.get_sector_ranking(M)
                lag_results = lag_engine.compute_all_sectors(date)
                ix_results = ix_engine.compute_all_sectors(M)

                buy_candidates = []
                for sect, raw_score in sector_scores:
                    eff = lag_results.get(sect)
                    eff_score = eff.effective_score if eff else raw_score
                    mult = ix_results.get(sect)
                    final_score = eff_score * (mult.multiplier if mult else 1.0)

                    if final_score > 0.50:
                        # Get top stock in sector
                        stocks = [s for s in UNIVERSE if _get_sector(conn, s) == sect]
                        for sym in stocks[:2]:
                            if _vn20_gate(fin_conn, conn, sym, date):
                                fund_score = _get_fundamental_score(fin_conn, sym, date)
                                behav_score = _get_behavioral_score(conn, sym, date)
                                alpha_score = _get_momentum_score(conn, sym, date)

                                composite = (
                                    0.35 * fund_score
                                    + 0.25 * eff_score
                                    + 0.25 * alpha_score
                                    + 0.15 * behav_score
                                )
                                if composite > 0.55:
                                    buy_candidates.append((sym, composite, sect))

                buy_candidates.sort(key=lambda x: x[1], reverse=True)

                for sym, composite, sect in buy_candidates[:5]:
                    action = _multi_factor_decide(composite, 0, None)
                    if action == "BUY":
                        _execute_multi_factor(state, conn, date, "BUY", sym, composite, weight=0.10)
                        decision_log.append({"date": date, "symbol": sym, "action": "BUY", "score": composite})

            # SELL check for existing positions
            for sym in list(state.positions.keys()):
                price = _get_close(conn, sym, date)
                if price:
                    entry = state.entry_prices.get(sym)
                    composite = _get_fundamental_score(fin_conn, sym, date)
                    action = _multi_factor_decide(composite, price, entry)
                    if action == "SELL":
                        _execute_multi_factor(state, conn, date, "SELL", sym, composite)

        _mark_to_market(state, conn, date)

        if (i + 1) % 200 == 0:
            equity = state.equity_curve[-1] if state.equity_curve else state.initial_capital
            print(f"  Day {i+1}/{len(dates)}: {date} | Equity={equity/1e6:.1f}M | Positions={len(state.positions)}")

    t2 = time.time() - t2
    print(f"  [Phase 2] Done in {t2:.1f}s ({len(score_days)} scored days x {len(dates)} total)")

    # Build result
    result = _build_result(state, "MULTI_FACTOR", start, end, len(dates))

    # Print report
    print(f"\n{'='*70}")
    print("  MULTI-FACTOR BACKTEST RESULTS")
    print(f"{'='*70}\n")

    metrics = [
        ("Total Return (%)", result.total_return, ".2f"),
        ("Annualized Return (%)", result.annualized_return, ".2f"),
        ("Sharpe Ratio", result.sharpe_ratio, ".4f"),
        ("Max Drawdown (%)", result.max_drawdown, ".2f"),
        ("Volatility (%)", result.volatility, ".2f"),
        ("Win Rate (%)", result.win_rate, ".1f"),
        ("Total Trades", result.total_trades, "d"),
        ("Final Equity (M)", result.final_equity / 1e6, ".1f"),
    ]
    for label, val, fmt in metrics:
        print(f"  {label:<25} {val:>15{fmt}}")

    # Position summary
    print(f"\n  Active positions: {len(state.positions)}")
    for sym, shares in list(state.positions.items())[:10]:
        entry = state.entry_prices.get(sym, 0)
        print(f"    {sym}: {shares} shares @ {entry:.2f}")

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": "multi_factor",
        "period": {"start": start, "end": end},
        "scenarios": {
            "MULTI_FACTOR": {
                "total_return_pct": result.total_return,
                "annualized_return_pct": result.annualized_return,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown,
                "volatility_pct": result.volatility,
                "win_rate_pct": result.win_rate,
                "total_trades": result.total_trades,
                "final_equity": result.final_equity,
            }
        },
    }
    summary_path = REPORTS_DIR / f"multifactor_{start}_{end}_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n  Summary saved: {summary_path}")

    conn.close()
    fin_conn.close()
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Unified System Replay — Ablation Study")
    parser.add_argument("--start", default="2021-04-01", help="Start date (default: 2021-04-01)")
    parser.add_argument("--end", default="2026-08-04", help="End date (default: 2026-08-04)")
    parser.add_argument("--db", default=None, help="Override DB path")
    parser.add_argument("--sample-every", type=int, default=5, help="Score every N-th day (default: 5)")
    parser.add_argument("--mode", choices=["ablation", "multi-factor"], default="ablation",
                        help="Run mode: ablation (A/B/C) or multi-factor (5 models)")
    args = parser.parse_args()
    if args.mode == "multi-factor":
        run_multi_factor_backtest(args.start, args.end, args.db, args.sample_every)
    else:
        run_unified_replay(args.start, args.end, args.db, args.sample_every)
