import sqlite3
import sys
from pathlib import Path

import pandas as pd


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))

    backend_dir = root_path / "backend"
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()
from src.database.db_core import get_connection, save_data_upsert
from src.engine.ipo_engine import IpoEvent


def log_regime_state(verdict):
    """
    Persists the daily regime decision to the regime_history table.
    """
    date = verdict.get("date")
    if not date:
        return

    data = {
        "date": [date],
        "regime_score": [verdict.get("regime_score")],
        "status": [verdict.get("market_status")],
        "breadth_pct": [verdict["details"].get("breadth_pct")],
        "breadth_velocity": [verdict.get("breadth_velocity", 0.0)],
        "trend_score": [verdict["details"].get("t_score")],
        "vol_score": [verdict["details"].get("v_score")],
        "atr_ratio": [verdict["details"].get("atr_ratio")],
        "active_model": [verdict.get("active_model", "NONE")],
        "recovery_flag": [1 if verdict.get("recovery", {}).get("is_recovery") else 0],
    }

    df = pd.DataFrame(data)
    with get_connection() as conn:
        save_data_upsert("regime_history", df, conn)
    print(f"📈 [TIMELINE] Logged regime state for {date}.")


def get_regime_history(limit=30):
    """
    Retrieves the recent regime history for visibility analysis.
    """
    with get_connection() as conn:
        df = pd.read_sql(f"SELECT * FROM regime_history ORDER BY date DESC LIMIT {limit}", conn)
    return df.sort_values("date")


def _ensure_ipo_calendar_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ipo_calendar (
            symbol TEXT PRIMARY KEY,
            listing_date TEXT NOT NULL,
            listing_price REAL NOT NULL DEFAULT 0,
            listing_volume INTEGER NOT NULL DEFAULT 0,
            market_cap_listing REAL NOT NULL DEFAULT 0,
            sector TEXT NOT NULL DEFAULT 'UNKNOWN',
            exchange TEXT NOT NULL DEFAULT 'HOSE',
            aftermarket_return_pct REAL DEFAULT 0,
            days_listed INTEGER DEFAULT 0,
            source TEXT DEFAULT 'STATIC_SEED',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def seed_static_ipo_calendar() -> int:
    """Insert a small static IPO seed set into the existing SQLite cache DB."""
    seed_rows = [
        {
            "symbol": "DMX",
            "listing_date": "2026-05-25",
            "listing_price": 95000.0,
            "listing_volume": 151140000,
            "market_cap_listing": 14360.0,
            "sector": "Retail/Electronics",
            "exchange": "HOSE",
            "aftermarket_return_pct": 25.0,
            "days_listed": 15,
            "source": "STATIC_SEED",
        },
        {
            "symbol": "GAS",
            "listing_date": "2026-06-01",
            "listing_price": 120000.0,
            "listing_volume": 80000000,
            "market_cap_listing": 9600.0,
            "sector": "Energy",
            "exchange": "HOSE",
            "aftermarket_return_pct": 11.5,
            "days_listed": 8,
            "source": "STATIC_SEED",
        },
        {
            "symbol": "MEC",
            "listing_date": "2026-06-05",
            "listing_price": 42000.0,
            "listing_volume": 120000000,
            "market_cap_listing": 5040.0,
            "sector": "Industrial",
            "exchange": "HNX",
            "aftermarket_return_pct": 7.8,
            "days_listed": 4,
            "source": "STATIC_SEED",
        },
    ]

    with get_connection() as conn:
        _ensure_ipo_calendar_table(conn)
        cursor = conn.cursor()
        inserted = 0
        for row in seed_rows:
            cursor.execute(
                """
                INSERT OR REPLACE INTO ipo_calendar (
                    symbol, listing_date, listing_price, listing_volume,
                    market_cap_listing, sector, exchange,
                    aftermarket_return_pct, days_listed, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["symbol"],
                    row["listing_date"],
                    row["listing_price"],
                    row["listing_volume"],
                    row["market_cap_listing"],
                    row["sector"],
                    row["exchange"],
                    row["aftermarket_return_pct"],
                    row["days_listed"],
                    row["source"],
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def get_active_ipos(days_back: int = 90):
    """Load active IPO records from the SQLite cache DB."""
    try:
        with get_connection() as conn:
            _ensure_ipo_calendar_table(conn)
            rows = conn.execute(
                """
                SELECT symbol, listing_date, listing_price, listing_volume,
                       market_cap_listing, sector, exchange
                FROM ipo_calendar
                WHERE date(listing_date) >= date('now', ?)
                ORDER BY listing_date DESC
                """,
                (f"-{days_back} days",),
            ).fetchall()

        if not rows:
            seed_static_ipo_calendar()
            with get_connection() as conn:
                _ensure_ipo_calendar_table(conn)
                rows = conn.execute(
                    """
                    SELECT symbol, listing_date, listing_price, listing_volume,
                           market_cap_listing, sector, exchange
                    FROM ipo_calendar
                    ORDER BY listing_date DESC
                    """
                ).fetchall()

        return [
            IpoEvent(
                symbol=row[0],
                listing_date=pd.to_datetime(row[1]),
                listing_price=float(row[2] or 0),
                listing_volume=int(row[3] or 0),
                market_cap_listing=float(row[4] or 0),
                sector=row[5] or "UNKNOWN",
                exchange=row[6] or "HOSE",
            )
            for row in rows
        ]
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return []


def get_aftermarket_returns(days_back: int = 90):
    """Load aftermarket returns from the static IPO seed table."""
    try:
        with get_connection() as conn:
            _ensure_ipo_calendar_table(conn)
            rows = conn.execute(
                """
                SELECT symbol, aftermarket_return_pct
                FROM ipo_calendar
                WHERE aftermarket_return_pct IS NOT NULL
                ORDER BY listing_date DESC
                LIMIT ?
                """,
                (days_back,),
            ).fetchall()

        if not rows:
            seed_static_ipo_calendar()
            with get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, aftermarket_return_pct
                    FROM ipo_calendar
                    WHERE aftermarket_return_pct IS NOT NULL
                    ORDER BY listing_date DESC
                    """
                ).fetchall()

        return {symbol: float(ret or 0.0) for symbol, ret in rows}
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return {}


def calculate_breadth_velocity(current_breadth, days=5, target_date=None):
    """
    Calculates the velocity of breadth recovery from historical logs.
    Supports Point-in-time accuracy via target_date.
    """
    try:
        with get_connection() as conn:
            if target_date:
                df = pd.read_sql(
                    f"SELECT breadth_pct, date FROM regime_history "
                    f"WHERE date < '{target_date}' ORDER BY date DESC LIMIT {days}",
                    conn,
                )
            else:
                df = pd.read_sql(f"SELECT breadth_pct, date FROM regime_history ORDER BY date DESC LIMIT {days}", conn)

        if df.empty:
            return 0.0

        past_entry = df.iloc[-1]
        past_breadth = past_entry["breadth_pct"]
        velocity = current_breadth - past_breadth

        if target_date and velocity != 0:
            print(
                f"   [VELOCITY] Today: {current_breadth}% | Past: {past_breadth}% "
                f"(from {past_entry['date']}) | Result: {velocity:+.1f}%"
            )

        return velocity
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        # Table might not exist yet on very first run
        return 0.0
