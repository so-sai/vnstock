"""
Macro Service Layer v1.0
Cầu nối giữa FastAPI Routes và Regime/Breadth Engines.
Xử lý: Data transformation, Exception handling, Fallback.
"""

import logging
import sys
from pathlib import Path


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
    return root_path


PROJECT_ROOT = _hydrate_path()

import pandas as pd
from core.macro.gold_regime_engine import cross_reference_with_market

from src.database.db_core import get_connection
from src.registry import registry

logger = logging.getLogger(__name__)


def _get_latest_macro_values() -> dict:
    """
    Truy vấn macro_history, xử lý duplicate bằng cách lấy giá trị mới nhất
    cho mỗi variable (theo rowid DESC). Kèm cờ is_stale = 1 nếu có bản ghi nào
    trong macro_history bị đánh dấu stale.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT variable, date, value, COALESCE(is_stale, 0) AS is_stale FROM (
                    SELECT variable, date, value, is_stale,
                           ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                    FROM macro_history
                ) WHERE rn = 1
            """,
                conn,
            )

        if df.empty:
            return {}

        result = {}
        macro_stale = False
        for _, row in df.iterrows():
            var = row["variable"]
            val = row["value"]
            if int(row.get("is_stale", 0)):
                macro_stale = True
            if var == "DXY":
                result["dxy_index"] = round(val, 2)
            elif var in ("USD_CNH", "USDCNH", "FX_IDC:USDCNH"):
                result["usd_cnh"] = round(val, 4)
            elif var in ("USD_CNY", "USDCNY", "FX_IDC:USDCNY"):
                result["usd_cny"] = round(val, 4)
            elif var == "COPPER_HG":
                result["copper_price"] = round(val, 2)
            elif var == "US10Y":
                result["us10y_yield"] = round(val, 3)
            elif var == "GOLD_XAU":
                result["gold_price"] = round(val, 2)
            elif var == "XAGUSD":
                result["silver_price"] = round(val, 3)
            elif var == "USD_VND":
                result["usd_vnd"] = round(val, 2)
            elif var == "BTC":
                result["btc_price"] = round(val, 2)
            elif var == "US2Y":
                result["us2y_yield"] = round(val, 3)
            elif var == "US5Y":
                result["us5y_yield"] = round(val, 3)
            elif var == "US30Y":
                result["us30y_yield"] = round(val, 3)
            elif var == "INTERBANK_ON":
                result["interbank_rate"] = round(val, 2)
            elif var == "INTERBANK_1W":
                result["interbank_rate_1w"] = round(val, 2)
            elif var == "INTERBANK_2W":
                result["interbank_rate_2w"] = round(val, 2)
            elif var == "INTERBANK_1M":
                result["interbank_rate_1m"] = round(val, 2)
            elif var == "SBV_ACTION":
                result["sbv_action"] = str(val)
            elif var == "VGB10Y":
                result["vgb10y"] = round(val, 2)
            elif var == "VGB10Y_BPS":
                result["vgb10y_bps"] = int(val)
            elif var == "VGB10Y_STATUS":
                result["vgb10y_status"] = str(val)
            elif var == "TIP_PRICE":
                result["tip_price"] = round(val, 2)
            elif var == "US_REAL_YIELD":
                result["us_real_yield"] = round(val, 3)
            elif var == "BREAKEVEN_INFLATION":
                result["breakeven_inflation"] = round(val, 3)

        result["macro_stale"] = macro_stale
        return result
    except Exception as e:
        logger.error(f"Error fetching macro history: {e}")
        return {"macro_stale": False}


def _get_interbank_rate() -> float | None:
    """
    Lấy lãi suất liên ngân hàng từ macro_history (INTERBANK_ON).
    Trả về None nếu chưa có dữ liệu — không dùng ước lượng giả.
    """
    macro = _get_latest_macro_values()
    return macro.get("interbank_rate")


def _get_interbank_rate_1w() -> float | None:
    macro = _get_latest_macro_values()
    return macro.get("interbank_rate_1w")


def _get_interbank_rate_2w() -> float | None:
    macro = _get_latest_macro_values()
    return macro.get("interbank_rate_2w")


def _get_interbank_rate_1m() -> float | None:
    macro = _get_latest_macro_values()
    return macro.get("interbank_rate_1m")


def _get_sbv_action() -> str:
    """
    Lấy trạng thái SBV từ macro_history (SBV_ACTION).
    Trả về UNKNOWN nếu chưa có dữ liệu — không suy diễn từ DXY.
    """
    macro = _get_latest_macro_values()
    return macro.get("sbv_action", "UNKNOWN")


def _get_vgb10y(us10y_yield: float | None) -> dict:
    """
    Lấy VGB10Y từ macro_history (VGB10Y).
    Nếu chưa có, ước lượng từ US10Y và đánh dấu ESTIMATED.
    """
    macro = _get_latest_macro_values()
    real_vgb = macro.get("vgb10y")
    if real_vgb is not None:
        return {
            "yield": round(real_vgb, 2),
            "data_quality": "REAL",
            "bps_change": macro.get("vgb10y_bps"),
            "status": macro.get("vgb10y_status"),
        }
    if us10y_yield:
        estimated = round(us10y_yield * 0.65, 2)
        return {
            "yield": estimated,
            "data_quality": "ESTIMATED",
            "bps_change": None,
            "status": "NO_DATA",
        }
    return {
        "yield": None,
        "data_quality": "NO_DATA",
        "bps_change": None,
        "status": "NO_DATA",
    }


def get_macro_status(target_date: str | None = None) -> dict:
    """
    Lấy trạng thái Vĩ mô + Regime Score.
    Trả về dict khớp với MacroStatus Pydantic model.

    Args:
        target_date: Ngày mục tiêu (YYYY-MM-DD). Nếu None, lấy ngày mới nhất.
    """
    try:
        regime = registry.regime_engine.detect_regime(target_date=target_date)
    except Exception as e:
        logger.error(f"Regime engine failed: {e}")
        regime = None

    try:
        breadth = registry.breadth_engine.run_breadth_analysis()
    except Exception as e:
        logger.error(f"Breadth engine failed: {e}")
        breadth = None

    macro_values = _get_latest_macro_values()

    if regime is None and breadth is None:
        raise RuntimeError("Both regime and breadth engines failed. Data may not be seeded.")

    regime_score = regime.get("regime_score", 0) if regime else 0
    regime_status = regime.get("status", "UNKNOWN") if regime else "UNKNOWN"
    details = regime.get("details", {}) if regime else {}

    breadth_pct = details.get("breadth_pct", 0)
    if breadth:
        breadth_pct = breadth.get("health_score_ma20", breadth_pct)

    if regime_score > 0.65:
        risk_level = "Emerald"
    elif regime_score >= 0.35:
        risk_level = "Amber"
    else:
        risk_level = "Red"

    gold_cognition = cross_reference_with_market(
        {
            "regime_status": regime_status,
            "risk_level": risk_level,
            "dxy_index": macro_values.get("dxy_index", 104.5),
            "us_real_yield": macro_values.get("us_real_yield"),
            "breakeven_inflation": macro_values.get("breakeven_inflation"),
        }
    )
    gold = gold_cognition.get("gold_cognition", {})

    premium = gold.get("domestic_premium", {})

    us10y_raw = macro_values.get("us10y_yield")
    vgb_data = _get_vgb10y(us10y_raw)

    us2y = macro_values.get("us2y_yield")
    us5y = macro_values.get("us5y_yield")
    us30y = macro_values.get("us30y_yield")

    spread_10y2y = round(us10y_raw - us2y, 3) if (us10y_raw is not None and us2y is not None) else None
    spread_30y10y = round(us30y - us10y_raw, 3) if (us30y is not None and us10y_raw is not None) else None

    yc_inversion = None
    if spread_10y2y is not None:
        if spread_10y2y < 0:
            yc_inversion = "INVERTED"
        elif spread_10y2y < 0.5:
            yc_inversion = "FLAT"
        else:
            yc_inversion = "NORMAL"

    vgb_bps_display = None
    vgb_label = None
    if vgb_data.get("data_quality") == "REAL" and vgb_data.get("bps_change") is not None:
        bps = vgb_data["bps_change"]
        vgb_bps_display = f"{'+' if bps > 0 else ''}{bps} bps"
        vgb_label = "Áp lực rút vốn" if bps > 3 else ("Thanh khoản nới lỏng" if bps < -3 else "Ổn định")
    else:
        vgb_bps_display = None
        vgb_label = "NO_DATA" if vgb_data.get("data_quality") == "NO_DATA" else "ESTIMATED"

    return {
        "usd_cnh": macro_values.get("usd_cnh", 7.24),
        "usd_cny": macro_values.get("usd_cny", 7.24),
        "tip_price": macro_values.get("tip_price"),
        "us_real_yield": macro_values.get("us_real_yield"),
        "breakeven_inflation": macro_values.get("breakeven_inflation"),
        "copper_price": macro_values.get("copper_price", 9500.0),
        "dxy_index": macro_values.get("dxy_index", 104.5),
        "interbank_rate": _get_interbank_rate(),
        "sbv_action": _get_sbv_action(),
        "risk_level": risk_level,
        "regime_score": regime_score,
        "regime_status": regime_status,
        "breadth_pct": round(breadth_pct, 1),
        "breadth_std_10d": details.get("breadth_std_10d", 0),
        "breadth_momentum": details.get("breadth_momentum", 0),
        "ma50_slope": details.get("ma50_slope", 0),
        "adx": details.get("adx", 0),
        "atr_ratio": details.get("atr_ratio", 0),
        "vgb10y": vgb_data["yield"],
        "vgb10y_data_quality": vgb_data.get("data_quality", "NO_DATA"),
        "vgb10y_bps_change": vgb_bps_display,
        "vgb10y_status_label": vgb_label,
        "vgb10y_raw_bps": vgb_data.get("bps_change"),
        "us2y_yield": us2y,
        "us5y_yield": us5y,
        "us30y_yield": us30y,
        "spread_10y2y": spread_10y2y,
        "spread_30y10y": spread_30y10y,
        "yield_curve_inversion": yc_inversion,
        "gold_price": macro_values.get("gold_price", 0),
        "silver_price": macro_values.get("silver_price", 0),
        "btc_price": macro_values.get("btc_price", 0),
        "usd_vnd": macro_values.get("usd_vnd", 0),
        "gold_regime": gold.get("gold_regime", "NEUTRAL"),
        "gold_velocity": gold.get("velocity", 0),
        "gold_spread_pressure": gold.get("spread_pressure", 0),
        "gold_macro_bias": gold.get("macro_bias", "NEUTRAL"),
        "gold_scenarios": gold.get("scenarios", []),
        "gold_silver_ratio": round(
            macro_values.get("gold_price", 0) / macro_values.get("silver_price", 1)
            if macro_values.get("silver_price", 0)
            else 0,
            2,
        ),
        "gold_premium_regime": premium.get("premium_regime", "PREMIUM_NORMAL"),
        "gold_premium_pct": premium.get("premium_pct", 0),
        "gold_premium_vnd": premium.get("premium_vnd", 0),
        "gold_xau_vnd_per_luong": premium.get("xau_vnd_per_luong", 0),
        "gold_xau_usd_per_oz": premium.get("xau_usd_per_oz", 0),
        "macro_stale": macro_values.get("macro_stale", False),
    }


def get_regime_history(limit: int = 90, start_date: str | None = None, end_date: str | None = None) -> list:
    """
    Lấy lịch sử Regime Score để vẽ biểu đồ Timeline.
    Bộ lọc động:
      - Live monitoring (mặc định): start_date >= '2025-01-01' (bỏ seed 2023)
      - Backtest/Stress Test: truyền start_date / end_date tùy ý
    """
    try:
        where_parts = []
        if start_date:
            where_parts.append(f"date >= '{start_date}'")
        else:
            where_parts.append("date >= '2025-01-01'")
        if end_date:
            where_parts.append(f"date <= '{end_date}'")
        where_clause = " AND ".join(where_parts)

        with get_connection() as conn:
            df = pd.read_sql(
                f"""
                SELECT date, regime_score, status, breadth_pct, trend_score, vol_score
                FROM regime_history
                WHERE {where_clause}
                ORDER BY date DESC
                LIMIT {limit}
            """,
                conn,
            )

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
        return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"Error fetching regime history: {e}")
        return []
