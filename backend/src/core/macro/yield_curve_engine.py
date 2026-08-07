"""
Yield Curve Engine — Đo lường độ dốc đường cong lợi suất.
Phát hiện đảo ngược (inversion) và cảnh báo suy thoái.
Các spread:
  10Y-2Y  = spread chuẩn dự báo suy thoái (inverted khi < 0)
  30Y-10Y = độ dốc dài hạn
  10Y-5Y  = trung hạn
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# Ngưỡng cảnh báo
INVERSION_WARN = 0.0  # spread < 0 = inverted
FLAT_WARN = 0.5  # spread < 0.5 = phẳng
STEEP_THRESHOLD = 2.0  # spread > 2 = dốc


def get_yield_curve_from_db() -> dict:
    """Lấy US2Y, US5Y, US10Y, US30Y từ macro_history, tính spread."""
    try:
        import pandas as pd

        from src.database.db_core import get_connection

        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT variable, value FROM (
                    SELECT variable, value, ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                    FROM macro_history
                    WHERE variable IN ('US2Y', 'US5Y', 'US10Y', 'US30Y')
                ) WHERE rn = 1
            """,
                conn,
            )

        if df.empty:
            return _empty_curve()

        yields = {}
        for _, row in df.iterrows():
            yields[row["variable"]] = row["value"]

        us2y = yields.get("US2Y")
        us5y = yields.get("US5Y")
        us10y = yields.get("US10Y")
        us30y = yields.get("US30Y")

        spreads = {}
        if us10y is not None and us2y is not None:
            spreads["10Y2Y"] = round(us10y - us2y, 3)
        if us30y is not None and us10y is not None:
            spreads["30Y10Y"] = round(us30y - us10y, 3)
        if us10y is not None and us5y is not None:
            spreads["10Y5Y"] = round(us10y - us5y, 3)

        return {
            "yields": {
                "us2y": us2y,
                "us5y": us5y,
                "us10y": us10y,
                "us30y": us30y,
            },
            "spreads": spreads,
            "inversion_status": _classify_inversion(spreads.get("10Y2Y")),
            "steepness_status": _classify_steepness(spreads.get("30Y10Y")),
        }
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.error(f"Yield curve fetch failed: {e}")
        return _empty_curve()


def _empty_curve() -> dict:
    return {
        "yields": {"us2y": None, "us5y": None, "us10y": None, "us30y": None},
        "spreads": {},
        "inversion_status": "NO_DATA",
        "steepness_status": "NO_DATA",
    }


def _classify_inversion(spread_10y2y: float | None) -> str:
    """Phân loại trạng thái inversion dựa trên spread 10Y-2Y."""
    if spread_10y2y is None:
        return "NO_DATA"
    if spread_10y2y < INVERSION_WARN:
        return "INVERTED"
    if spread_10y2y < FLAT_WARN:
        return "FLAT"
    return "NORMAL"


def _classify_steepness(spread_30y10y: float | None) -> str:
    """Phân loại độ dốc dài hạn dựa trên spread 30Y-10Y."""
    if spread_30y10y is None:
        return "NO_DATA"
    if spread_30y10y > STEEP_THRESHOLD:
        return "STEEP"
    if spread_30y10y < 0:
        return "INVERTED"
    return "NORMAL"
