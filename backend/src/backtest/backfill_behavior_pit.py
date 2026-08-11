"""backfill_behavior_pit.py — Reconstruct volume_profile + active_demand PIT for 2022-2025.

Phase 2: giải quyết behavior_states = {} (100%) trong replay 2022-2025.

Root cause:
  - volume_profile/active_demand chỉ có 0 rows trước 2026-07-29 → score_behavior trả
    {"position": "UNKNOWN"} → "UNKNOWN" bị _is_active() lọc → clusters.behavior.states = {}.

Fix:
  - Tính volume_profile + active_demand CHO TỪNG NGÀY GIAO DỊCH của 58 mã có BCTC,
    dùng compute_volume_profile(..., as_of_date=date) → chỉ nhìn data <= date (PIT-safe).
  - Ghi INSERT OR REPLACE vào financial_facts.db.volume_profile / active_demand.

PIT-safe: get_ohlcv filter date <= target_date (đã refactor market_behavior_engine).
Phạm vi: 58 mã có BCTC (cùng pool valuation), các phiên trong 2022-2025.
QUYỀN GHI CÓ CHỦ ĐÍCH: chỉ sửa financial_facts.db (gitignored). Backup tự động.

Usage:
  python backend/src/backtest/backfill_behavior_pit.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# ── Path setup ──
_current = Path(__file__).resolve().parent
for _par in [_current] + list(_current.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        ROOT = _par
        break
BACKEND = ROOT / "backend"
SRC = BACKEND / "src"
DATA = BACKEND / "data"
FIN_DB = DATA / "financial_facts.db"
SCREENER_DB = DATA / "screener_cache.db"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SRC))

from src.financial.market_behavior_engine import MarketBehaviorEngine

YEARS = ("2022", "2023", "2024", "2025")


def backup_tables() -> None:
    conn = sqlite3.connect(str(FIN_DB))
    for t in ("volume_profile", "active_demand"):
        conn.execute(f"DROP TABLE IF EXISTS {t}_backup")
        conn.execute(f"CREATE TABLE {t}_backup AS SELECT * FROM {t}")
        n = conn.execute(f"SELECT COUNT(*) FROM {t}_backup").fetchone()[0]
        print(f"[BACKUP] {t} -> {t}_backup ({n} rows)")
    conn.commit()
    conn.close()


def get_symbols() -> list[str]:
    conn = sqlite3.connect(str(FIN_DB))
    syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM financial_facts")]
    conn.close()
    return syms


def trading_days_for(symbol: str) -> list[str]:
    conn = sqlite3.connect(str(SCREENER_DB))
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol=? AND substr(date,1,4) IN (?,?,?,?) ORDER BY date",
        (symbol.upper(), *YEARS),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def main() -> None:
    backup_tables()
    eng = MarketBehaviorEngine()
    eng.init_schema()

    syms = get_symbols()
    total_days = 0
    t0 = time.time()
    for sym in syms:
        days = trading_days_for(sym)
        total_days += len(days)
        for date in days:
            vp = eng.compute_volume_profile(sym, as_of_date=date)
            if not vp:
                continue
            conn = eng.fin_conn()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO volume_profile
                        (symbol, date, price_current, poc, vah, val,
                         poc_volume, total_volume, value_area_volume,
                         bin_size, hvns, price_ma20, price_ma50, price_ma200,
                         volume_ma20, volume_ratio, range_pct)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        sym.upper(),
                        vp["date"],
                        vp["price_current"],
                        vp["poc"],
                        vp["vah"],
                        vp["val"],
                        vp["poc_volume"],
                        vp["total_volume"],
                        vp["value_area_volume"],
                        vp["bin_size"],
                        json.dumps(vp["hvns"]),
                        vp["price_ma20"],
                        vp["price_ma50"],
                        vp["price_ma200"],
                        vp["volume_ma20"],
                        vp["volume_ratio"],
                        vp["range_pct"],
                    ),
                )
                for sig in eng.scan_active_demand(sym, as_of_date=date):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO active_demand
                            (symbol, date, price, signal_type, support_level,
                             volume_ratio, close_position, price_change, strength, metadata)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                        (
                            sig["symbol"],
                            sig["date"],
                            sig["price"],
                            sig["signal_type"],
                            sig["support_level"],
                            sig["volume_ratio"],
                            sig["close_position"],
                            sig["price_change"],
                            sig["strength"],
                            "{}",
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        print(f"  {sym}: {len(days)} days", flush=True)
    elapsed = time.time() - t0

    conn = sqlite3.connect(str(FIN_DB))
    n_vp = conn.execute("SELECT COUNT(*) FROM volume_profile").fetchone()[0]
    n_ad = conn.execute("SELECT COUNT(*) FROM active_demand").fetchone()[0]
    n_pre2026_vp = conn.execute("SELECT COUNT(*) FROM volume_profile WHERE date < '2026-01-01'").fetchone()[0]
    conn.close()
    print(f"[DONE] symbols={len(syms)} days={total_days} elapsed={elapsed:.1f}s")
    print(f"  volume_profile: {n_vp} rows ({n_pre2026_vp} trước 2026)")
    print(f"  active_demand:  {n_ad} rows")


if __name__ == "__main__":
    main()
