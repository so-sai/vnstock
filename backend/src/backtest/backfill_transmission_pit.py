"""backfill_transmission_pit.py — Reconstruct transmission_phase PIT for 2022-2025.

Reads macro_history (date <= target_date) for 7 variables:
  INTERBANK_ON, INTERBANK_1W, INTERBANK_3M, DXY, VIX, USD_VND, GOLD_XAU

Classifies into 6 phases using EconomicTransmissionEngine._classify_phase logic.
Tracks provenance: OBSERVED (in DB), FALLBACK (default used).
Computes coverage = fraction of 7 variables observed.

Usage:
  python backend/src/backtest/backfill_transmission_pit.py --start 2022-01-01 --end 2025-12-31
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

# ── Constants ──
REQUIRED_VARS = ["INTERBANK_ON", "INTERBANK_1W", "INTERBANK_3M", "DXY", "VIX", "USD_VND", "GOLD_XAU"]
DEFAULTS = {
    "INTERBANK_ON": 4.5,
    "INTERBANK_1W": None,  # will use ib_on
    "INTERBANK_3M": None,  # will use ib_on + 1.0
    "DXY": 104.0,
    "VIX": 18.0,
    "USD_VND": 25400.0,
    "GOLD_XAU": 2000.0,
}


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transmission_pit (
            date TEXT PRIMARY KEY,
            transmission_phase TEXT NOT NULL,
            liquidity REAL,
            credit REAL,
            confidence REAL,
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


def fetch_macro_pit(conn: sqlite3.Connection, target_date: str) -> dict[str, tuple[float, str]]:
    """Fetch macro variables PIT (date <= target_date). Returns {var: (value, provenance)}."""
    q = """
        SELECT variable, value FROM (
            SELECT variable, date, value,
                   ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
            FROM macro_history
            WHERE variable IN (?,?,?,?,?,?,?)
              AND date <= ?
        ) WHERE rn = 1
    """
    rows = conn.execute(q, (*REQUIRED_VARS, target_date)).fetchall()
    raw = {r[0]: r[1] for r in rows}

    result = {}
    for var in REQUIRED_VARS:
        if var in raw and raw[var] is not None:
            result[var] = (float(raw[var]), "OBSERVED")
        else:
            default = DEFAULTS[var]
            if default is None:
                # Derive from other variables
                if var == "INTERBANK_1W":
                    ib_on = result.get("INTERBANK_ON", (4.5, "FALLBACK"))[0]
                    default = ib_on
                elif var == "INTERBANK_3M":
                    ib_on = result.get("INTERBANK_ON", (4.5, "FALLBACK"))[0]
                    default = ib_on + 1.0
            result[var] = (float(default), "FALLBACK")
    return result


def classify_transmission(macro: dict[str, tuple[float, str]]) -> tuple[str, float, float, float, float, dict]:
    """Classify transmission phase from PIT macro data. Returns (phase, liq, cred, conf, coverage, provenance)."""
    ib_on, _ = macro["INTERBANK_ON"]
    ib_1w, _ = macro["INTERBANK_1W"]
    ib_3m, _ = macro["INTERBANK_3M"]
    dxy, _ = macro["DXY"]
    vix, _ = macro["VIX"]
    usd_vnd, _ = macro["USD_VND"]
    gold, _ = macro["GOLD_XAU"]

    # Liquidity
    liquidity = float(np.clip(100 - (ib_on / 10.0) * 100, 0, 100))

    # Credit
    spread_3m_on = max(ib_3m - ib_on, 0)
    spread_1w_on = max(ib_1w - ib_on, 0)
    avg_spread = (spread_3m_on + spread_1w_on) / 2.0
    credit = float(np.clip(100 - (avg_spread / 5.0) * 100, 0, 100))

    # Confidence
    dxy_score = float(np.clip(100 - (max(dxy - 95, 0) / 25.0) * 100, 0, 100))
    vix_score = float(np.clip(100 - (vix / 40.0) * 100, 0, 100))
    vnd_score = float(np.clip(100 - ((usd_vnd - 24000) / 4000.0) * 100, 0, 100))
    gold_score = float(np.clip(100 - (max(gold - 2000, 0) / 3000.0) * 100, 0, 100))
    confidence = float(
        np.clip(
            dxy_score * 0.30 + vix_score * 0.30 + vnd_score * 0.25 + gold_score * 0.15,
            0,
            100,
        )
    )

    # Phase classification (same as EconomicTransmissionEngine._classify_phase)
    liq, cred, conf = liquidity, credit, confidence
    if liq > 60 and cred < 40:
        phase = "LIQUIDITY_TRAP"
    elif liq < 30 and cred < 30 and conf < 40:
        phase = "CREDIT_CRUNCH"
    elif liq > 60 and cred > 60 and conf > 60:
        phase = "HEALTHY_TRANSMISSION"
    elif cred > 80 and conf > 80:
        phase = "OVERHEATING"
    elif liq < 40 and conf < 40:
        phase = "RISK_OFF_FLIGHT"
    else:
        phase = "FRAGILE_STABILITY"

    # Coverage
    n_observed = sum(1 for v, (_, src) in macro.items() if src == "OBSERVED")
    coverage = n_observed / len(REQUIRED_VARS)

    # Provenance
    provenance = {var: src for var, (_, src) in macro.items()}

    return phase, liquidity, credit, confidence, coverage, provenance


def backfill(start: str, end: str, db_path: str | None = None) -> None:
    db = sqlite3.connect(str(db_path or SCREENER_DB))
    create_table(db)

    days = get_trading_days(db, start, end)
    print(f"Trading days: {len(days)} ({start} - {end})")

    t0 = time.time()
    inserted = 0
    phase_dist = {}
    coverage_stats = []

    for i, d in enumerate(days):
        macro = fetch_macro_pit(db, d)
        phase, liq, cred, conf, coverage, prov = classify_transmission(macro)

        db.execute(
            """
            INSERT OR REPLACE INTO transmission_pit
            (date, transmission_phase, liquidity, credit, confidence, coverage, provenance_json, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
            (d, phase, round(liq, 1), round(cred, 1), round(conf, 1), round(coverage, 3), json.dumps(prov)),
        )

        phase_dist[phase] = phase_dist.get(phase, 0) + 1
        coverage_stats.append(coverage)
        inserted += 1

        if (i + 1) % 50 == 0:
            db.commit()
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  {i + 1}/{len(days)} days, {rate:.1f} days/s, ETA {(len(days) - i - 1) / rate:.0f}s", flush=True)

    db.commit()
    db.close()

    elapsed = time.time() - t0
    avg_cov = sum(coverage_stats) / len(coverage_stats) if coverage_stats else 0
    min_cov = min(coverage_stats) if coverage_stats else 0

    print(f"\n{'=' * 70}")
    print(f"BACKFILL DONE: {inserted} days in {elapsed:.1f}s")
    print(f"Phase distribution: {phase_dist}")
    print(f"Coverage: avg={avg_cov:.1%} min={min_cov:.1%}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    backfill(args.start, args.end, args.db)
