"""backfill_sector_pit.py — Reconstruct sector rotation state PIT for 2022-2025.

Replicates SectorStateEngine's 4-pillar score (momentum .35, health .25,
flow .25, valuation .15) but POINT-IN-TIME:

  momentum   <- daily_ohlcv: RS = close/SMA20 - 1, last value <= target_date
                (window = last 90 calendar days before target_date)
  flow       <- daily_ohlcv: sector avg volume / market avg volume at the
                latest trading day <= target_date
  health     <- financial_facts.health_ratios WHERE period <= quarter(target_date)
  valuation  <- financial_facts.valuation_scores WHERE period <= quarter(target_date)

Notes:
  - Live SectorStateEngine reads health/valuation with columns (date, score)
    that DO NOT EXIST in the schema -> those pillars always fall back to 0.0.
    This backfill uses the correct PIT period-based query instead, so the
    reconstructed sector state is strictly more informative than live.
  - top_phase = phase of top_sector (matches the load_sector() bugfix).
  - coverage = fraction of pillar inputs observed (momentum/flow always, if
    daily_ohlcv data exists; health/valuation only when period data exists).

Usage:
  python backend/src/backtest/backfill_sector_pit.py --start 2022-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

# ── Path setup ──
_current = Path(__file__).resolve().parent
for _par in [_current] + list(_current.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        ROOT = _par
        break
BACKEND = ROOT / "backend"
SRC = BACKEND / "src"
DATA = BACKEND / "data"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SCREENER_DB = DATA / "screener_cache.db"
FIN_DB = DATA / "financial_facts.db"

PILLAR_WEIGHTS = {"momentum": 0.35, "health": 0.25, "flow": 0.25, "valuation": 0.15}
MIN_SYMBOLS = 3

ROTATION_PHASES = {
    "EARLY": "Dòng tiền bắt đầu vào ngành, RS cải thiện từ đáy",
    "MID": "Dòng tiền mạnh nhất, momentum và flow đồng thuận",
    "LATE": "Dòng tiền chững lại, valuation bắt đầu đắt",
    "WEAKENING": "RS suy yếu, flow giảm, chuẩn bị đảo chiều",
    "NEUTRAL": "Không có tín hiệu rõ ràng",
}


def quarter_at(target_date: str) -> str:
    y, m, _ = target_date.split("-")
    q = (int(m) - 1) // 3 + 1
    return f"{y}Q{q}"


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_pit (
            date TEXT PRIMARY KEY,
            top_sector TEXT,
            top_phase TEXT,
            top_score REAL,
            n_healthy INT,
            n_weak INT,
            rotation_chain_json TEXT,
            sectors_json TEXT,
            coverage REAL,
            provenance_json TEXT,
            computed_at TEXT
        )
    """)
    conn.commit()


def get_trading_days(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def load_icb_mapping(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute("SELECT symbol, icb_name2 FROM symbol_industry WHERE icb_name2 IS NOT NULL").fetchall()
    mapping: dict[str, list[str]] = {}
    for sym, sector in rows:
        mapping.setdefault(sector, []).append(sym)
    return mapping


def load_ohlcv(conn: sqlite3.Connection) -> dict:
    """Load daily_ohlcv into memory: {symbol: {"dates": [...], "close": [...], "volume": [...]}}."""
    rows = conn.execute("SELECT symbol, date, close, volume FROM daily_ohlcv ORDER BY symbol, date").fetchall()
    data: dict[str, dict] = {}
    for sym, d, close, vol in rows:
        if close is None:
            continue
        bucket = data.setdefault(sym, {"dates": [], "close": [], "volume": []})
        bucket["dates"].append(d)
        bucket["close"].append(float(close))
        bucket["volume"].append(float(vol or 0.0))
    return data


def _sma(values: list[float], window: int) -> list[float]:
    out = [np.nan] * len(values)
    running = 0.0
    for i in range(len(values)):
        running += values[i]
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            out[i] = running / window
    return out


def compute_pillar_series(ohlcv: dict) -> dict:
    """Precompute per-symbol per-date RS (momentum) and volume, keyed by symbol -> list aligned to dates.

    Returns {symbol: {"dates": [...], "rs": [...], "vol": [...]}}.
    """
    series: dict[str, dict] = {}
    for sym, b in ohlcv.items():
        closes = b["close"]
        if len(closes) < 20:
            continue
        sma20 = _sma(closes, 20)
        rs = [(c / s - 1.0) if s and not np.isnan(s) else np.nan for c, s in zip(closes, sma20)]
        series[sym] = {
            "dates": b["dates"],
            "rs": rs,
            "vol": b["volume"],
        }
    return series


def _last_index_le(dates: list[str], target: str) -> int | None:
    """Binary search: last index i where dates[i] <= target."""
    lo, hi = 0, len(dates) - 1
    res = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            res = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return res if res >= 0 else None


def load_health_pit(fin: sqlite3.Connection, target_date: str) -> dict[str, float]:
    """health score per symbol, PIT: latest period <= quarter(target_date).

    Uses ratio_value mean across ratio_names for that symbol's latest period.
    """
    q = quarter_at(target_date)
    rows = fin.execute(
        """
        SELECT symbol, AVG(ratio_value) FROM (
            SELECT symbol, period, ratio_value,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY period DESC) AS rn
            FROM health_ratios
            WHERE period <= ?
        ) WHERE rn = 1
        GROUP BY symbol
        """,
        (q,),
    ).fetchall()
    return {sym: float(v) for sym, v in rows if v is not None}


def load_valuation_pit(fin: sqlite3.Connection, target_date: str) -> dict[str, float]:
    """valuation z-score per symbol, PIT: latest period <= quarter(target_date)."""
    q = quarter_at(target_date)
    rows = fin.execute(
        """
        SELECT symbol, AVG(z_score) FROM (
            SELECT symbol, period, z_score,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY period DESC) AS rn
            FROM valuation_scores
            WHERE period <= ?
        ) WHERE rn = 1
        GROUP BY symbol
        """,
        (q,),
    ).fetchall()
    return {sym: float(v) for sym, v in rows if v is not None}


def classify_phase(momentum: float, health: float, flow: float, valuation: float) -> str:
    m = float(np.clip((momentum + 1) / 2, 0, 1))
    f = float(np.clip((flow + 1) / 2, 0, 1))
    v = float(np.clip((valuation + 1) / 2, 0, 1))
    if m > 0.55 and f > 0.50 and v < 0.60:
        return "MID"
    if m > 0.40 and f > 0.40 and m < 0.60:
        return "EARLY"
    if m > 0.50 and v > 0.60:
        return "LATE"
    if m < 0.40 and f < 0.40:
        return "WEAKENING"
    return "NEUTRAL"


def build_rotation_chain(sectors: list[dict]) -> list[str]:
    phase_order = {"EARLY": 0, "MID": 1, "LATE": 2, "WEAKENING": 3, "NEUTRAL": 4}
    sorted_sectors = sorted(sectors, key=lambda s: (phase_order.get(s["phase"], 4), -s["score"]))
    chain: list[str] = []
    seen = set()
    for s in sorted_sectors:
        if s["phase"] not in seen:
            chain.append(f"[{s['phase']}]")
            seen.add(s["phase"])
        chain.append(s["sector"])
    phases_present = [s["phase"] for s in sorted_sectors if s["phase"] != "NEUTRAL"]
    unique_phases = list(dict.fromkeys(phases_present))
    if unique_phases:
        chain.append(f"Flow direction: {' -> '.join(unique_phases)}")
    return chain


def backfill(start: str, end: str, db_path: str | None = None, fin_db_path: str | None = None) -> None:
    db = sqlite3.connect(str(db_path or SCREENER_DB))
    fin = sqlite3.connect(str(fin_db_path or FIN_DB))
    create_table(db)

    days = get_trading_days(db, start, end)
    print(f"Trading days: {len(days)} ({start} - {end})")

    mapping = load_icb_mapping(db)
    sectors_to_eval = {k: v for k, v in mapping.items() if len(v) >= MIN_SYMBOLS}
    print(f"Sectors (>= {MIN_SYMBOLS} symbols): {len(sectors_to_eval)}")

    print("Loading daily_ohlcv into memory...", flush=True)
    ohlcv = load_ohlcv(db)
    series = compute_pillar_series(ohlcv)
    print(f"Loaded {len(series)} symbols with >= 20 days", flush=True)

    # Market avg volume per date (for flow denominator)
    market_vol_by_date: dict[str, float] = {}
    for sym, b in ohlcv.items():
        for d, v in zip(b["dates"], b["volume"]):
            market_vol_by_date[d] = market_vol_by_date.get(d, 0.0) + v
    n_sym_by_date: dict[str, int] = {}
    for sym, b in ohlcv.items():
        for d in b["dates"]:
            n_sym_by_date[d] = n_sym_by_date.get(d, 0) + 1

    t0 = time.time()
    inserted = 0
    phase_dist: dict[str, int] = {}
    coverage_stats: list[float] = []

    for i, d in enumerate(days):
        # ── Health / Valuation PIT (per symbol) ──
        health_map = load_health_pit(fin, d)
        valuation_map = load_valuation_pit(fin, d)

        # ── Momentum / Flow per sector at target date ──
        sector_results = []
        prov_momentum = "FALLBACK"
        prov_flow = "FALLBACK"
        n_sectors_momentum = 0
        n_sectors_flow = 0
        for sector, symbols in sectors_to_eval.items():
            rs_vals: list[float] = []
            vol_vals: list[float] = []
            n_data = 0
            for sym in symbols:
                s = series.get(sym)
                if not s:
                    continue
                idx = _last_index_le(s["dates"], d)
                if idx is None:
                    continue
                n_data += 1
                rs_vals.append(s["rs"][idx])
                vol_vals.append(s["vol"][idx])

            if n_data == 0:
                continue

            # Momentum: mean RS, normalized [-0.5, 0.5] -> [-1, 1]
            rs_clean = [v for v in rs_vals if not np.isnan(v)]
            rs_mean = float(np.nanmean(rs_clean)) if rs_clean else 0.0
            momentum = float(np.clip(rs_mean, -0.5, 0.5) / 0.5)

            # Flow: sector avg vol / market avg vol at latest day
            sector_vol = float(np.mean(vol_vals)) if vol_vals else 0.0
            market_vol = market_vol_by_date.get(d, 0.0)
            n_mkt = n_sym_by_date.get(d, 1)
            market_avg = (market_vol / n_mkt) if n_mkt > 0 else 0.0
            ratio = sector_vol / market_avg if market_avg > 0 else 0.0
            flow = float(np.clip(np.log1p(ratio) / 5.0, -1.0, 1.0))

            # Health / Valuation from PIT financial maps
            health_vals = [health_map.get(s) for s in symbols if s in health_map]
            health = float(np.clip(float(np.mean(health_vals)), -1.0, 1.0)) if health_vals else 0.0
            val_vals = [valuation_map.get(s) for s in symbols if s in valuation_map]
            valuation = float(np.clip(-float(np.mean(val_vals)), -1.0, 1.0)) if val_vals else 0.0

            score = (
                PILLAR_WEIGHTS["momentum"] * momentum
                + PILLAR_WEIGHTS["health"] * health
                + PILLAR_WEIGHTS["flow"] * flow
                + PILLAR_WEIGHTS["valuation"] * valuation
            ) * 100.0
            phase = classify_phase(momentum, health, flow, valuation)

            if rs_clean:
                n_sectors_momentum += 1
                prov_momentum = "OBSERVED"
            if vol_vals:
                n_sectors_flow += 1
                prov_flow = "OBSERVED"

            sector_results.append(
                {
                    "sector": sector,
                    "symbol_count": n_data,
                    "momentum": round(momentum, 4),
                    "health": round(health, 4),
                    "flow": round(flow, 4),
                    "valuation": round(valuation, 4),
                    "score": round(score, 2),
                    "phase": phase,
                    "phase_desc": ROTATION_PHASES.get(phase, ""),
                }
            )

        if not sector_results:
            continue

        sector_results.sort(key=lambda x: x["score"], reverse=True)
        top = sector_results[0]
        n_healthy = sum(1 for s in sector_results if s["score"] > 50)
        n_weak = sum(1 for s in sector_results if s["score"] < 30)
        chain = build_rotation_chain(sector_results)

        # Coverage: fraction of (sector, pillar) pairs with observed input.
        n_sectors = len(sector_results)
        n_sectors_health_data = sum(1 for s in sector_results if any(x in health_map for x in sectors_to_eval[s["sector"]]))
        n_sectors_val_data = sum(1 for s in sector_results if any(x in valuation_map for x in sectors_to_eval[s["sector"]]))
        coverage = (n_sectors_momentum + n_sectors_flow + n_sectors_health_data + n_sectors_val_data) / (4 * n_sectors)

        provenance = {
            "momentum": prov_momentum,
            "flow": prov_flow,
            "health": "OBSERVED" if n_sectors_health_data else "FALLBACK",
            "valuation": "OBSERVED" if n_sectors_val_data else "FALLBACK",
        }

        db.execute(
            """
            INSERT OR REPLACE INTO sector_pit
            (date, top_sector, top_phase, top_score, n_healthy, n_weak,
             rotation_chain_json, sectors_json, coverage, provenance_json, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
            (
                d,
                top["sector"],
                top["phase"],
                top["score"],
                n_healthy,
                n_weak,
                json.dumps(chain, ensure_ascii=False),
                json.dumps(sector_results, ensure_ascii=False),
                round(coverage, 3),
                json.dumps(provenance),
            ),
        )

        phase_dist[top["phase"]] = phase_dist.get(top["phase"], 0) + 1
        coverage_stats.append(coverage)
        inserted += 1

        if (i + 1) % 25 == 0:
            db.commit()
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  {i + 1}/{len(days)} days, {rate:.1f} days/s, ETA {(len(days) - i - 1) / rate:.0f}s", flush=True)

    db.commit()
    db.close()
    fin.close()

    elapsed = time.time() - t0
    avg_cov = sum(coverage_stats) / len(coverage_stats) if coverage_stats else 0
    min_cov = min(coverage_stats) if coverage_stats else 0

    print(f"\n{'=' * 70}")
    print(f"BACKFILL DONE: {inserted} days in {elapsed:.1f}s")
    print(f"Top-phase distribution: {phase_dist}")
    print(f"Coverage: avg={avg_cov:.1%} min={min_cov:.1%}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--db", default=None)
    ap.add_argument("--fin-db", default=None)
    args = ap.parse_args()
    backfill(args.start, args.end, args.db, args.fin_db)
