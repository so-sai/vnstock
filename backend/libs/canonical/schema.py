from __future__ import annotations

import sqlite3
from typing import Optional


SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS macro_history_v2 (
    variable TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    asset_class TEXT NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_value REAL,
    raw_unit TEXT,
    confidence REAL DEFAULT 1.0,
    valid_from TEXT,
    valid_to TEXT,
    freshness_score REAL DEFAULT 1.0,
    latency_ms INTEGER DEFAULT 0,
    ingested_at TEXT DEFAULT (datetime('now')),
    metadata TEXT,
    PRIMARY KEY (variable, date, source)
);
"""

INDEX_V2 = """
CREATE INDEX IF NOT EXISTS idx_macro_v2_var_date ON macro_history_v2(variable, date);
CREATE INDEX IF NOT EXISTS idx_macro_v2_class ON macro_history_v2(asset_class);
"""


def migrate_macro_history(conn: sqlite3.Connection) -> dict:
    """Create macro_history_v2 table and indexes. Returns migration report."""
    result = {"table_created": False, "records_migrated": 0, "errors": []}

    try:
        conn.execute(SCHEMA_V2)
        for stmt in INDEX_V2.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        result["table_created"] = True
    except sqlite3.Error as e:
        result["errors"].append(f"Create table failed: {e}")
        return result

    # Migrate existing data from macro_history v1 (if it exists)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='macro_history'"
    )
    if cursor.fetchone() is None:
        result["note"] = "No macro_history v1 table to migrate"
        return result

    try:
        conn.execute("""
            INSERT OR IGNORE INTO macro_history_v2
                (variable, date, value, asset_class, unit, source, raw_value, raw_unit)
            SELECT
                variable, date, value,
                CASE
                    WHEN variable IN ('VNINDEX','VN30','HNXINDEX','UPCOMINDEX','SH_COMP') THEN 'index'
                    WHEN variable IN ('USD_VND','USD_CNY','USD_CNH') THEN 'fx'
                    WHEN variable IN ('GOLD_XAU','COPPER_HG','BRENT_OIL','WTI_OIL') THEN 'commodity'
                    WHEN variable = 'BTC' THEN 'crypto'
                    WHEN variable = 'US10Y' THEN 'macro_yield'
                    WHEN variable = 'DXY' THEN 'macro_index'
                    ELSE 'unknown'
                END,
                CASE
                    WHEN variable IN ('VNINDEX','VN30','HNXINDEX','UPCOMINDEX','SH_COMP') THEN 'index_level'
                    WHEN variable = 'DXY' THEN 'dxy_level'
                    WHEN variable IN ('USD_VND','USD_CNY','USD_CNH') THEN 'fx_rate'
                    WHEN variable = 'GOLD_XAU' THEN 'usd_per_ounce'
                    WHEN variable = 'COPPER_HG' THEN 'usd_per_lb'
                    WHEN variable IN ('BRENT_OIL','WTI_OIL') THEN 'usd_per_barrel'
                    WHEN variable = 'BTC' THEN 'usd'
                    WHEN variable = 'US10Y' THEN 'percent'
                    ELSE 'unknown'
                END,
                CASE
                    WHEN variable IN ('VNINDEX','VN30','HNXINDEX','UPCOMINDEX') THEN 'kbs'
                    WHEN variable IN ('SH_COMP','DXY','GOLD_XAU','COPPER_HG','BRENT_OIL','WTI_OIL','BTC','US10Y','USD_VND','USD_CNY','USD_CNH') THEN 'yahoo'
                    ELSE 'unknown'
                END,
                NULL, NULL
            FROM macro_history
        """)
        conn.commit()
        result["records_migrated"] = conn.total_changes
    except sqlite3.Error as e:
        result["errors"].append(f"Migration failed: {e}")

    return result


def get_create_v2_sql() -> str:
    """Return raw DDL for external use."""
    return SCHEMA_V2 + "\n" + INDEX_V2
