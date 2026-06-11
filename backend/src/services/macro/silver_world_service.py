"""
Silver World Service — Fetch XAGUSD via yfinance (SI=F)
Bổ sung Global Silver vào Precious Metals Framework.
Sanity guard: so sánh với rolling 30d median từ DB, cảnh báo nếu lệch >50%.
"""
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
from src.database.db_core import get_connection, save_data_upsert
from canonical import CanonicalAssetRegistry, Normalizer
from canonical.validator import ValidationError as CanonicalValidationError

_CANON = CanonicalAssetRegistry()
_NORM = Normalizer()

logger = logging.getLogger(__name__)

TICKER = "SI=F"
SILVER_CACHE = {"price": None, "timestamp": 0, "conflicted": False}
CACHE_TTL = 300
HARD_LOWER = 5.0
HARD_UPPER = 100.0
SOFT_DEVIATION = 0.50


def _get_30d_median() -> Optional[float]:
    """Lấy median XAGUSD 30 phiên gần nhất từ DB."""
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT value FROM macro_history WHERE variable = 'XAGUSD' ORDER BY date DESC LIMIT 30",
                conn,
            )
        if df.empty:
            return None
        return float(df["value"].median())
    except Exception:
        return None


def fetch_world_silver_live() -> Optional[float]:
    """Fetch XAGUSD (SI=F) latest close via yfinance. Returns price or None."""
    now = int(datetime.now().timestamp())
    if SILVER_CACHE["price"] is not None and now - SILVER_CACHE["timestamp"] < CACHE_TTL:
        return SILVER_CACHE["price"]
    try:
        silver = yf.Ticker(TICKER)
        data = silver.history(period="1d")
        if data.empty:
            return None
        latest = float(data.iloc[-1]["Close"])
        SILVER_CACHE["price"] = latest
        SILVER_CACHE["timestamp"] = now
        SILVER_CACHE["conflicted"] = False

        if latest < HARD_LOWER or latest > HARD_UPPER:
            logger.warning(f"XAGUSD {latest} outside hard bounds [{HARD_LOWER}, {HARD_UPPER}]")
            return latest

        median = _get_30d_median()
        if median is not None:
            deviation = abs(latest / median - 1)
            if deviation > SOFT_DEVIATION:
                SILVER_CACHE["conflicted"] = True
                logger.warning(
                    f"XAGUSD {latest} deviates {deviation*100:.0f}% from 30d median {median:.2f} — CONFLICTED"
                )

        return latest
    except Exception as e:
        logger.error(f"World silver fetch failed: {e}")
        return None


def is_silver_conflicted() -> bool:
    return SILVER_CACHE.get("conflicted", False)


def fetch_world_silver_history(period: str = "1y") -> pd.DataFrame:
    """Fetch XAGUSD (SI=F) history via yfinance. Returns DataFrame with columns: date, close."""
    try:
        silver = yf.Ticker(TICKER)
        data = silver.history(period=period)
        if data.empty:
            return pd.DataFrame()
        df = data[["Close"]].reset_index()
        df.columns = [c.lower().strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "close"]]
    except Exception as e:
        logger.error(f"World silver history fetch failed: {e}")
        return pd.DataFrame()


def seed_world_silver_to_db(period: str = "1y") -> bool:
    """Fetch SI=F from yfinance and save to macro_history as XAGUSD (canonical v2)."""
    try:
        df = fetch_world_silver_history(period=period)
        if df.empty:
            return False
        df = df.rename(columns={"close": "value"}).copy()
        df["variable"] = "XAGUSD"

        with get_connection() as conn:
            save_data_upsert("macro_history", df[["variable", "date", "value"]], conn)

        v2_records = []
        rejects = 0
        for _, row in df.iterrows():
            try:
                rec = _NORM.normalize("XAGUSD", row["date"], row["value"], "yahoo")
                v2_records.append({
                    "variable": rec.variable, "date": rec.date,
                    "value": rec.value, "asset_class": rec.asset_class.value,
                    "unit": rec.unit.value, "source": rec.source.value,
                    "raw_value": rec.raw_value, "raw_unit": rec.raw_unit,
                    "confidence": rec.confidence,
                })
            except (ValueError, CanonicalValidationError):
                rejects += 1

        if v2_records:
            df_v2 = pd.DataFrame(v2_records)
            with get_connection() as conn:
                save_data_upsert("macro_history_v2", df_v2, conn)
            logger.info(f"World silver seeded: {len(df)} rows (canonical v2: {len(v2_records)}, rejects: {rejects})")
        else:
            logger.warning(f"World silver seed: all {rejects} rows rejected by canonical validator")

        return True
    except Exception as e:
        logger.error(f"World silver seed failed: {e}")
        return False
