"""sqlite_provider.py — SqliteCacheProvider.

Final tier of the fallback chain. Reads normalized facts from
`financial_facts.db` — the same store the Governor/Decision layer
consumes. This is the "cache/fixture" tier: it never hits the network,
so it always works (even fully offline), and it guarantees the
Governor can run off a persisted snapshot.

NOTE: this provider exposes *raw normalized facts* (long format) rather
than wide DataFrames, because that is exactly what financial_facts.db
holds. Consumers that need wide statements run their own pivot (as the
existing fetch pipeline does).
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.providers.base import FinancialProvider


def _resolve_db_path() -> Optional[Path]:
    root = Path(__file__).resolve().parents[3]  # PTCK_VNSTOCK/
    db = root / "backend" / "data" / "financial_facts.db"
    return db if db.exists() else None


class SqliteCacheProvider(FinancialProvider):
    """Read-only adapter over financial_facts.db (normalized fact store)."""

    name = "sqlite"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _resolve_db_path()

    def is_available(self) -> bool:
        return self._db_path is not None and self._db_path.exists()

    def _connect(self) -> Optional[sqlite3.Connection]:
        if not self.is_available():
            return None
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _facts_df(
        self, symbol: str, statement_type: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        conn = self._connect()
        if conn is None:
            return None
        try:
            query = """
                SELECT symbol, period, fiscal_year, fiscal_quarter,
                       entity_type, statement_type, metric, value, unit
                FROM financial_facts
                WHERE symbol = ?
            """
            params: List[Any] = [symbol.upper()]
            if statement_type:
                query += " AND statement_type = ?"
                params.append(statement_type.upper())
            query += " ORDER BY period DESC, metric"
            df = pd.read_sql_query(query, conn, params=params)
            return df if not df.empty else None
        except Exception:
            return None
        finally:
            conn.close()

    # ── Financial statements (raw long-format facts) ───────────────────
    def income_statement(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        return self._facts_df(symbol, statement_type="IS")

    def balance_sheet(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        return self._facts_df(symbol, statement_type="BS")

    def cashflow(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        return self._facts_df(symbol, statement_type="CF")

    # ── Market data (not stored normalized here) ───────────────────────
    def history(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[pd.DataFrame]:
        # daily_ohlcv lives in screener_cache.db; delegate via db_core
        try:
            from src.database.db_core import get_connection
            with get_connection() as conn:
                query = """
                    SELECT date, open, high, low, close, adj_close, volume
                    FROM daily_ohlcv
                    WHERE symbol = ?
                """
                params: List[Any] = [symbol.upper()]
                if start:
                    query += " AND date >= ?"
                    params.append(start)
                if end:
                    query += " AND date <= ?"
                    params.append(end)
                query += " ORDER BY date"
                df = pd.read_sql_query(query, conn, params=params)
                return df if not df.empty else None
        except Exception:
            return None

    # ── Audit ──────────────────────────────────────────────────────────
    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "db_path": str(self._db_path) if self._db_path else None,
            "available": self.is_available(),
        }
