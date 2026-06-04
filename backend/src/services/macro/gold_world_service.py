"""
Gold World Service — Fetch XAUUSD via yfinance (GC=F)
Bổ sung Global Gold vào Gold Cognition Layer.
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

TICKER = "GC=F"
GOLD_CACHE = {"price": None, "timestamp": 0, "conflicted": False}
CACHE_TTL = 300
# Sanity bounds: historical gold has never gone outside 500-5000 USD/oz
HARD_LOWER = 500.0
HARD_UPPER = 5000.0
# Soft sanity: deviation >50% from 30d median → CONFLICTED
SOFT_DEVIATION = 0.50


def _get_30d_median() -> Optional[float]:
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
    except Exception:
        return None


def fetch_world_gold_live() -> Optional[float]:
    """Fetch XAUUSD (GC=F) latest close via yfinance. Returns price or None."""
    now = int(datetime.now().timestamp())
    if GOLD_CACHE["price"] is not None and now - GOLD_CACHE["timestamp"] < CACHE_TTL:
        return GOLD_CACHE["price"]
    try:
        gold = yf.Ticker(TICKER)
        data = gold.history(period="1d")
        if data.empty:
            return None
        latest = float(data.iloc[-1]["Close"])
        GOLD_CACHE["price"] = latest
        GOLD_CACHE["timestamp"] = now
        GOLD_CACHE["conflicted"] = False

        # Sanity check
        if latest < HARD_LOWER or latest > HARD_UPPER:
            logger.warning(f"XAUUSD {latest} outside hard bounds [{HARD_LOWER}, {HARD_UPPER}]")
            return latest

        median = _get_30d_median()
        if median is not None:
            deviation = abs(latest / median - 1)
            if deviation > SOFT_DEVIATION:
                GOLD_CACHE["conflicted"] = True
                logger.warning(
                    f"XAUUSD {latest} deviates {deviation*100:.0f}% from 30d median {median:.0f} — CONFLICTED"
                )

        return latest
    except Exception as e:
        logger.error(f"World gold fetch failed: {e}")
        return None


def is_gold_conflicted() -> bool:
    """Kiểm tra xem giá vàng hiện tại có bị đánh dấu CONFLICTED không."""
    return GOLD_CACHE.get("conflicted", False)


def fetch_world_gold_history(period: str = "1y") -> pd.DataFrame:
    """Fetch XAUUSD (GC=F) history via yfinance. Returns DataFrame with columns: date, close."""
    try:
        gold = yf.Ticker(TICKER)
        data = gold.history(period=period)
        if data.empty:
            return pd.DataFrame()
        df = data[["Close"]].reset_index()
        df.columns = [c.lower().strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "close"]]
    except Exception as e:
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
            logger.info(f"World gold seeded: {len(df)} rows (canonical v2: {len(v2_records)}, rejects: {rejects})")
        else:
            logger.warning(f"World gold seed: all {rejects} rows rejected by canonical validator")

        return True
    except Exception as e:
        logger.error(f"World gold seed failed: {e}")
        return False
