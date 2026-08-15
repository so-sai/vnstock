"""seed_commodity_baseline.py — Seed verified commodity baseline into macro_history.

Data provenance (Zero-Hallucination — only verified points are seeded):
  IRON_ORE_62      Investing.com SGX TSI Iron Ore 62% Fe (TIOc1) historical/quote
  COKING_COAL_HCC  TradingEconomics Australia HCC premium (13/08/2026)
  HRC_CFR          HPG giao T8/2026 (GMK/IndexBox, công bố 02/07/2026)

Corrections vs first-pass draft (2026-08-14):
  - 95.28 is the 06-Aug-2026 close, NOT 30-Jun
  - 93.91 is the 05-Aug-2026 close, NOT 31-Jul (31-Jul actual = 98.00)
  - 13-Aug iron ore quote = 95.05 (delayed), not 94.00
  - coking coal 225.00 (31-Jul) had no source -> omitted; only 222.00 (13-Aug) seeded
  - HRC 546 carried forward to 13-Aug as the current announced HPG price (source hpg_gmk)

Re-run any time with the latest verified prices; INSERT OR REPLACE keeps it idempotent.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# (variable, date, value, source)
COMMODITY_BASELINE = [
    # Quặng sắt 62% Fe (USD/t) — Investing.com TIOc1
    ("IRON_ORE_62", "2026-07-06", 98.30, "investing"),
    ("IRON_ORE_62", "2026-07-14", 98.92, "investing"),
    ("IRON_ORE_62", "2026-07-24", 98.42, "investing"),
    ("IRON_ORE_62", "2026-07-31", 98.00, "investing"),
    ("IRON_ORE_62", "2026-08-03", 93.66, "investing"),
    ("IRON_ORE_62", "2026-08-05", 93.91, "investing"),
    ("IRON_ORE_62", "2026-08-06", 95.28, "investing"),
    ("IRON_ORE_62", "2026-08-13", 95.05, "investing"),
    # Than mỡ luyện kim HCC (USD/t) — TradingEconomics
    ("COKING_COAL_HCC", "2026-08-13", 222.00, "tradingeconomics"),
    # Thép cuộn cán nóng HRC CFR (USD/t) — HPG giao T8/2026 (GMK/IndexBox 02/07)
    ("HRC_CFR", "2026-07-02", 546.00, "hpg_gmk"),
    ("HRC_CFR", "2026-08-13", 546.00, "hpg_gmk"),
]


def _ensure_source_column(conn: sqlite3.Connection) -> None:
    """Add `source` column to macro_history if missing (same pattern as is_stale)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(macro_history)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE macro_history ADD COLUMN source TEXT")
        conn.commit()
        print("[MIGRATION] Added macro_history.source column")


def seed(conn: sqlite3.Connection) -> int:
    _ensure_source_column(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO macro_history (variable, date, value, is_stale, source) VALUES (?, ?, ?, 0, ?)",
        COMMODITY_BASELINE,
    )
    conn.commit()
    return len(COMMODITY_BASELINE)


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}")
        return 1
    with sqlite3.connect(DB_PATH) as conn:
        n = seed(conn)
    print(f"[OK] Seeded {n} commodity baseline rows into {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
