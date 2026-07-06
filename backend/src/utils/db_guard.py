"""Tầng 2: DB Guard — Connection Wrapper chống cross-contamination
Giữa Operational Brain (backend/data/brain.db) và Memory Brain (.kit/local_brain.db)."""
import logging
import re
import sqlite3

logger = logging.getLogger("ptck.db_guard")

OPERATIONAL_TABLES = {
    'daily_ohlcv', 'macro_history', 'regime_history',
    'capital_displacement_history', 'flow_forecast_history',
    'market_foreign_history', 'portfolio_recommendations',
    'risk_governance_history', 'rsi_regime_history'
}
MEMORY_DB_MARKER = r'\.kit[/\\]local_brain\.db'


def pathed_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    is_memory_brain = re.search(MEMORY_DB_MARKER, str(db_path))

    if is_memory_brain:
        def authorizer(action, arg1, arg2, arg3, arg4):
            if action == sqlite3.SQLITE_CREATE_TABLE and arg1 in OPERATIONAL_TABLES:
                logger.critical(
                    f"DB_GUARD: Chan hanh vi tao bang Operational '{arg1}' trong Memory Brain!"
                )
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and arg1 in OPERATIONAL_TABLES:
                logger.critical(
                    f"DB_GUARD: Chan hanh vi ghi du lieu vao bang '{arg1}' trong Memory Brain!"
                )
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorizer)

    return conn
