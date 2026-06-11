"""
Real Yield Engine — đo lường lợi suất thực (TIPS-implied real yield).
Sử dụng TIP ETF (iShares TIPS Bond) để trích xuất:
  - US_REAL_YIELD = trailing annual dividend yield của TIP ETF
    (đây là cash yield thực tế từ danh mục TIPS, proxy cho real yield)
  - BREAKEVEN_INFLATION = US10Y - US_REAL_YIELD
    (tỷ lệ lạm phát kỳ vọng mà thị trường định giá)
"""
import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REAL_YIELD_HIGH = 2.5
REAL_YIELD_LOW = 0.5
BREAKEVEN_HIGH = 3.5
BREAKEVEN_LOW = 1.5


def get_real_yield_from_db() -> dict:
    """Lấy TIP_PRICE + US10Y từ macro_history, fetch TIP yield từ yfinance."""
    try:
        from src.database.db_core import get_connection
        import pandas as pd

        with get_connection() as conn:
            df = pd.read_sql("""
                SELECT variable, value FROM (
                    SELECT variable, value,
                           ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                    FROM macro_history
                    WHERE variable IN ('TIP_PRICE', 'US10Y')
                ) WHERE rn = 1
            """, conn)

        if df.empty:
            return _empty_real_yield()

        vals = {}
        for _, row in df.iterrows():
            vals[row['variable']] = row['value']

        tip_price = vals.get('TIP_PRICE')
        us10y = vals.get('US10Y')

        tip_yield = _fetch_tip_dividend_yield()

        us_real_yield = tip_yield
        breakeven = round(us10y - us_real_yield, 3) if (us10y is not None and us_real_yield is not None) else None

        return {
            "tip_price": tip_price,
            "us10y": us10y,
            "us_real_yield": us_real_yield,
            "breakeven_inflation": breakeven,
            "real_yield_regime": _classify_real_yield(us_real_yield),
            "breakeven_regime": _classify_breakeven(breakeven),
            "data_quality": "REAL" if tip_yield is not None else "NO_DATA",
        }
    except Exception as e:
        logger.error(f"Real yield fetch failed: {e}")
        return _empty_real_yield()


def _fetch_tip_dividend_yield() -> Optional[float]:
    """Fetch TIP ETF trailing annual dividend yield từ yfinance Ticker.info."""
    try:
        import yfinance as yf
        tip = yf.Ticker("TIP")
        info = tip.info
        dy = info.get("trailingAnnualDividendYield")
        if dy is not None:
            return round(float(dy) * 100, 3)
        if info.get("yield") is not None:
            return round(float(info["yield"]) * 100, 3)
        return None
    except Exception as e:
        logger.warning(f"Cannot fetch TIP dividend yield: {e}")
        return None


def _empty_real_yield() -> dict:
    return {
        "tip_price": None,
        "us10y": None,
        "us_real_yield": None,
        "breakeven_inflation": None,
        "real_yield_regime": "NO_DATA",
        "breakeven_regime": "NO_DATA",
        "data_quality": "NO_DATA",
    }


def _classify_real_yield(real_yield: Optional[float]) -> str:
    if real_yield is None:
        return "NO_DATA"
    if real_yield > REAL_YIELD_HIGH:
        return "RESTRICTIVE"
    if real_yield < REAL_YIELD_LOW:
        return "ACCOMODATIVE"
    return "NEUTRAL"


def _classify_breakeven(breakeven: Optional[float]) -> str:
    if breakeven is None:
        return "NO_DATA"
    if breakeven > BREAKEVEN_HIGH:
        return "HIGH_INFLATION_FEAR"
    if breakeven < BREAKEVEN_LOW:
        return "LOW_INFLATION_CONFIDENCE"
    return "STABLE_INFLATION_EXPECTATIONS"
