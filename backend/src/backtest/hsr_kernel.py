"""
hsr_kernel.py — HSR Execution Kernel (zero-DB replay engine).

Strategy:
    Replace the on-disk SQLite database with an in-memory SQLite instance
    pre-loaded with all OHLCV data needed for the replay window.

    All engines' SQL queries hit RAM instead of disk — zero I/O amplification.
    Zero engine modifications required. Works transparently through
    monkey-patching of db_core.get_connection.

    O(n) per day instead of O(n × engines × IO).
"""

import logging
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger("sentinel.hsr.kernel")


class InMemoryDB:
    """In-memory SQLite database pre-loaded with OHLCV data for HSR replay.

    Usage:
        db = InMemoryDB("2023-01-01", "2026-06-01")
        with db.patch_get_connection():
            # All engine calls here use in-memory DB — zero disk I/O
            for date in trading_days:
                build_historical_snapshot(date)
    """

    def __init__(self, start_date: str, end_date: str):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._load_data(start_date, end_date)
        self._create_indexes()

    def _load_data(self, start_date: str, end_date: str):
        import pandas as pd
        from src.database.db_core import get_connection as _disk_conn

        tables_to_copy = ["daily_ohlcv"]
        for table in tables_to_copy:
            with _disk_conn() as disk:
                df = pd.read_sql(f"SELECT * FROM {table} WHERE date >= ? AND date <= ?", disk, params=(start_date, end_date))
            if not df.empty:
                df.to_sql(table, self._conn, if_exists="replace", index=False)
                logger.info(f"  [Kernel] Loaded {len(df):,} rows from {table} into memory ({start_date} → {end_date})")

        # Also load regime_history for flow_forecast
        try:
            with _disk_conn() as disk:
                df = pd.read_sql(
                    "SELECT * FROM regime_history WHERE date >= ? AND date <= ?", disk, params=(start_date, end_date)
                )
            if not df.empty:
                df.to_sql("regime_history", self._conn, if_exists="replace", index=False)
                logger.info(f"  [Kernel] Loaded {len(df):,} rows from regime_history")
        except Exception:
            logger.info("  [Kernel] regime_history table not available — will be created by engines")

    def _create_indexes(self):
        try:
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON daily_ohlcv(date)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol ON daily_ohlcv(symbol)")
        except Exception:
            pass

    @contextmanager
    def patch_get_connection(self):
        """Context manager: replaces db_core.get_connection with in-memory DB."""
        import src.database.db_core as db_core

        original_get_connection = db_core.get_connection

        @contextmanager
        def _mem_connection():
            yield self._conn

        db_core.get_connection = _mem_connection
        try:
            yield
        finally:
            db_core.get_connection = original_get_connection
            self._conn.rollback()

    @property
    def connection(self):
        return self._conn

    def close(self):
        self._conn.close()
