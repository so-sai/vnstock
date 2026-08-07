"""
Gold World Service — Fetch XAUUSD via yfinance (GC=F)
Bổ sung Global Gold vào Gold Cognition Layer.
Sanity guard: so sánh với rolling 30d median từ DB, cảnh báo nếu lệch >50%.
"""

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent.parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()
_LIBS = str(Path(PROJECT_ROOT) / "backend" / "libs")
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)

import pandas as pd
import yfinance as yf
from canonical import CanonicalAssetRegistry, Normalizer
from canonical.validator import ValidationError as CanonicalValidationError

from src.database.db_core import get_connection, save_data_upsert

_CANON = CanonicalAssetRegistry()
_NORM = Normalizer()

logger = logging.getLogger(__name__)


@contextmanager
def _stderr_null():
    """Temporarily redirect stderr to os.devnull to suppress yfinance internal prints."""
    null_fd = os.open(os.devnull, os.O_WRONLY)
    old_fd = os.dup(2)
    os.dup2(null_fd, 2)
    try:
        yield
    finally:
        os.dup2(old_fd, 2)
        os.close(null_fd)


TICKER = "GC=F"
GOLD_CACHE = {"price": None, "timestamp": 0, "conflicted": False, "flat_line": False}
CACHE_TTL = 300
FLAT_LINE_WINDOW = 5  # number of consecutive identical values to signal flat line
# Sanity bounds: historical gold has never gone outside 500-5000 USD/oz
HARD_LOWER = 500.0
HARD_UPPER = 5000.0
# Soft sanity: deviation >50% from 30d median → CONFLICTED
SOFT_DEVIATION = 0.50


def _get_30d_median() -> float | None:
    """Lấy median GOLD_XAU 30 phiên gần nhất từ DB."""
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT value FROM macro_history WHERE variable = 'GOLD_XAU' ORDER BY date DESC LIMIT 30",
                conn,
            )
        if df.empty:
            return None
        return float(df["value"].median())
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return None


def _check_flat_line() -> bool:
    """Detect flat time series: last N consecutive GOLD_XAU values are identical.

    When DB Fallback repeats the same value across weekends/holidays,
    the time series appears flat. This flag alerts downstream algorithms
    (EWMA, Z-Score) that volatility is artificially suppressed.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                f"SELECT value FROM macro_history WHERE variable = 'GOLD_XAU' ORDER BY date DESC LIMIT {FLAT_LINE_WINDOW}",
                conn,
            )
        if len(df) < FLAT_LINE_WINDOW:
            return False
        vals = df["value"].tolist()
        return len(set(vals)) == 1  # all identical
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return False


def _gold_fallback_from_db() -> float | None:
    """Fallback: last known GOLD_XAU from DB when yfinance fails.

    NOTE: This value is NOT inserted into macro_history. The Dual-Z EWMA
    pipeline reads macro_history directly and never sees repeated fallback
    values, so flat-line noise does NOT reach the Z-score computation.
    The flat_line flag in GOLD_CACHE is for downstream consumers that
    might compute rolling statistics on the runtime value.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT value, date FROM macro_history WHERE variable = 'GOLD_XAU' ORDER BY date DESC LIMIT 1",
                conn,
            )
        if not df.empty:
            val = float(df["value"].iloc[0])
            dt = df["date"].iloc[0]
            logger.info(f"Gold fallback from DB: {val} (date={dt})")
            GOLD_CACHE["flat_line"] = _check_flat_line()
            if GOLD_CACHE["flat_line"]:
                logger.warning(f"Gold time series FLAT: last {FLAT_LINE_WINDOW} values identical ({val})")
            return val
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.warning(f"Gold DB fallback failed: {e}")
    return None


def fetch_world_gold_live() -> float | None:
    """Fetch XAUUSD (GC=F) latest close via yfinance. Falls back to DB on failure."""
    now = int(datetime.now().timestamp())
    if GOLD_CACHE["price"] is not None and now - GOLD_CACHE["timestamp"] < CACHE_TTL:
        return GOLD_CACHE["price"]
    try:
        with _stderr_null():
            gold = yf.Ticker(TICKER)
            data = gold.history(period="1d")
        if data.empty:
            logger.warning("Gold yfinance empty, falling back to DB")
            return _gold_fallback_from_db()
        latest = float(data.iloc[-1]["Close"])
        GOLD_CACHE["price"] = latest
        GOLD_CACHE["timestamp"] = now
        GOLD_CACHE["conflicted"] = False
        GOLD_CACHE["flat_line"] = False  # live data resets the flag

        # Sanity check
        if latest < HARD_LOWER or latest > HARD_UPPER:
            logger.warning(f"XAUUSD {latest} outside hard bounds [{HARD_LOWER}, {HARD_UPPER}]")
            return latest

        median = _get_30d_median()
        if median is not None:
            deviation = abs(latest / median - 1)
            if deviation > SOFT_DEVIATION:
                GOLD_CACHE["conflicted"] = True
                logger.warning(f"XAUUSD {latest} deviates {deviation * 100:.0f}% from 30d median {median:.0f} — CONFLICTED")

        return latest
    except Exception as e:  # noqa: BLE001 - fallback ladder: yfinance fail → fallback DB
        logger.error(f"World gold fetch failed: {e}")
        return _gold_fallback_from_db()


def is_gold_conflicted() -> bool:
    """Kiểm tra xem giá vàng hiện tại có bị đánh dấu CONFLICTED không."""
    return GOLD_CACHE.get("conflicted", False)


def is_gold_flat_line() -> bool:
    """True if gold time series has flat-lined (repeated identical values)."""
    return GOLD_CACHE.get("flat_line", False)


def fetch_world_gold_history(period: str = "1y") -> pd.DataFrame:
    """Fetch XAUUSD (GC=F) history via yfinance. Returns DataFrame with columns: date, close."""
    try:
        gold = yf.Ticker(TICKER)
        data = gold.history(period=period)
        if data.empty:
            return pd.DataFrame()
        df = data[["Close"]].reset_index()
        df.columns = [c.lower().strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
        return df[["date", "close"]]
    except Exception as e:  # noqa: BLE001 - resilience: nguồn ngoài yfinance lỗi → trả DataFrame rỗng
        logger.error(f"World gold history fetch failed: {e}")
        return pd.DataFrame()


def seed_world_gold_to_db(period: str = "1y") -> bool:
    """Fetch GC=F from yfinance and save to macro_history as GOLD_XAU (canonical v2)."""
    try:
        df = fetch_world_gold_history(period=period)
        if df.empty:
            return False
        df = df.rename(columns={"close": "value"}).copy()
        df["variable"] = "GOLD_XAU"

        # Write v1 legacy (backward compat)
        with get_connection() as conn:
            save_data_upsert("macro_history", df[["variable", "date", "value"]], conn)

        # Canonical v2 write — normalize + validate
        v2_records = []
        rejects = 0
        for _, row in df.iterrows():
            try:
                rec = _NORM.normalize("GOLD_XAU", row["date"], row["value"], "yahoo")
                v2_records.append(
                    {
                        "variable": rec.variable,
                        "date": rec.date,
                        "value": rec.value,
                        "asset_class": rec.asset_class.value,
                        "unit": rec.unit.value,
                        "source": rec.source.value,
                        "raw_value": rec.raw_value,
                        "raw_unit": rec.raw_unit,
                        "confidence": rec.confidence,
                    }
                )
            except ValueError, CanonicalValidationError:
                rejects += 1

        if v2_records:
            df_v2 = pd.DataFrame(v2_records)
            with get_connection() as conn:
                save_data_upsert("macro_history_v2", df_v2, conn)
            logger.info(f"World gold seeded: {len(df)} rows (canonical v2: {len(v2_records)}, rejects: {rejects})")
        else:
            logger.warning(f"World gold seed: all {rejects} rows rejected by canonical validator")

        return True
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.error(f"World gold seed failed: {e}")
        return False
