"""
Gold Regime Engine v1.0
Phân tích vàng như macro entropy sensor:
  - velocity: tốc độ thay đổi giá
  - spread_pressure: áp lực chênh lệch mua/bán
  - macro_bias: DEFENSIVE / NEUTRAL / RISK_ON
"""

import logging
import sqlite3
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent
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

import pandas as pd

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)


def analyze_gold_regime(lookback_days: int = 20) -> dict:
    """
    Phân tích regime vàng dựa trên GOLD_XAU từ macro_history.

    Returns:
        gold_regime: RISK_OFF / DEFENSIVE / NEUTRAL / RISK_ON
        velocity: tốc độ thay đổi (0-1)
        spread_pressure: áp lực chênh lệch (0-1)
        macro_bias: DEFENSIVE / NEUTRAL / RISK_ON
        signals: các tín hiệu chi tiết
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT date, value FROM macro_history
                WHERE variable = 'GOLD_XAU'
                ORDER BY date DESC LIMIT ?
            """,
                conn,
                params=(lookback_days + 5,),
            )
        if df.empty:
            return _default_gold_regime()
        df["date"] = pd.to_datetime(df["date"], format="mixed")
        df = df.sort_values("date").reset_index(drop=True)
        prices = df["value"].values
        if len(prices) < 5:
            return _default_gold_regime()
        latest = prices[-1]
        ma10 = prices[-10:].mean() if len(prices) >= 10 else prices.mean()
        ma20 = prices.mean()
        # Velocity: ROC 5-day chuẩn hóa
        roc_5d = (prices[-1] / prices[-5] - 1) if len(prices) >= 5 else 0
        velocity = min(abs(roc_5d) * 10, 1.0)
        # Spread pressure dựa trên độ lệch so với MA
        deviation = abs(latest / ma20 - 1)
        spread_pressure = min(deviation * 5, 1.0)
        # Macro bias & gold regime
        if latest > ma20 * 1.05 and velocity > 0.3:
            gold_regime = "RISK_OFF"
            macro_bias = "DEFENSIVE"
        elif latest > ma20 * 1.03:
            gold_regime = "DEFENSIVE"
            macro_bias = "DEFENSIVE"
        elif latest < ma20 * 0.97 and velocity < 0.2:
            gold_regime = "RISK_ON"
            macro_bias = "RISK_ON"
        else:
            gold_regime = "NEUTRAL"
            macro_bias = "NEUTRAL"
        return {
            "gold_regime": gold_regime,
            "velocity": round(velocity, 4),
            "spread_pressure": round(spread_pressure, 4),
            "macro_bias": macro_bias,
            "latest_price": float(round(latest, 2)),
            "ma20": float(round(ma20, 2)),
            "ma10": float(round(ma10, 2)),
            "roc_5d": float(round(roc_5d * 100, 2)),
            "deviation_pct": float(round(deviation * 100, 2)),
            "signals": {
                "above_ma20_5pct": bool(latest > ma20 * 1.05),
                "above_ma20_3pct": bool(latest > ma20 * 1.03),
                "below_ma20_3pct": bool(latest < ma20 * 0.97),
                "velocity_high": velocity > 0.3,
                "spread_pressure_high": spread_pressure > 0.4,
            },
        }
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.error(f"Gold regime analysis failed: {e}")
        return _default_gold_regime()


def cross_reference_with_market(macro_data: dict) -> dict:
    """
    Cross-reference gold regime + premium với market signals.
    Dùng trong macro_service để tạo cognitive insight.
    """
    gold = analyze_gold_regime()
    regime = gold.get("gold_regime", "NEUTRAL")
    market_regime = macro_data.get("regime_status", "UNKNOWN")
    risk_level = macro_data.get("risk_level", "Amber")

    premium = {}
    try:
        from core.macro.gold_spread_engine import analyze_domestic_premium

        premium = analyze_domestic_premium()
    except ImportError, AttributeError, TypeError, KeyError:
        logger.debug("cross_reference_with_market: không đọc được premium — bỏ qua")

    # Real yield context
    real_yield = macro_data.get("us_real_yield")
    breakeven = macro_data.get("breakeven_inflation")

    scenarios = []
    # Real yield signals (opportunity cost for gold)
    if real_yield is not None:
        if real_yield > 2.5:
            scenarios.append(f"Real yield {real_yield}% cao → Chi phí cơ hội nắm giữ vàng lớn")
        elif real_yield < 0.5:
            scenarios.append(f"Real yield {real_yield}% thấp → Vàng hưởng lợi từ chi phí cơ hội thấp")
    if breakeven is not None and breakeven > 3.5:
        scenarios.append(f"Breakeven inflation {breakeven}% cao → Cầu phòng vệ lạm phát hỗ trợ vàng")

    # Vàng ↑ + BANK ↓
    if regime == "RISK_OFF" and risk_level == "Red":
        scenarios.append("Vàng tăng + Thị trường suy yếu → Risk-off xác nhận")
        scenarios.append("Dòng tiền phòng thủ đang chiếm ưu thế")
    # Vàng ↑ + USD ↑
    if regime in ("RISK_OFF", "DEFENSIVE") and macro_data.get("dxy_index", 100) > 105:
        scenarios.append("Vàng tăng + DXY cao → Currency stress")
    # Vàng ↓ + SEC ↑ (proxy: risk-on market)
    if regime == "RISK_ON" and market_regime == "TRENDING":
        scenarios.append("Vàng giảm + Thị trường tăng → Risk-on rotation")
    # Spread mở rộng
    if gold.get("spread_pressure", 0) > 0.4:
        scenarios.append("Áp lực chênh lệch vàng tăng → Liquidity distortion")
    # Vàng ↑ nhưng VNINDEX vẫn ↑
    if regime in ("DEFENSIVE", "RISK_OFF") and market_regime == "TRENDING":
        scenarios.append("Vàng tăng nhưng thị trường vẫn tăng → Late-cycle divergence")
    # Premium surge
    if premium.get("premium_regime") == "PREMIUM_SURGE":
        scenarios.append(f"Premium nội địa {premium.get('premium_pct', 0)}% — Cầu trú ẩn nội địa cực mạnh, méo mó thanh khoản")
    elif premium.get("premium_regime") == "PREMIUM_ELEVATED":
        scenarios.append(f"Premium nội địa {premium.get('premium_pct', 0)}% — Tâm lý phòng thủ đang chiếm ưu thế")
    elif premium.get("premium_regime") == "PREMIUM_DISCOUNT":
        scenarios.append("Vàng trong nước rẻ hơn thế giới — Tâm lý ổn định")

    return {
        "gold_cognition": {
            **gold,
            "scenarios": scenarios,
            "scenario_count": len(scenarios),
            "domestic_premium": {
                "premium_regime": premium.get("premium_regime", "PREMIUM_NORMAL"),
                "premium_pct": premium.get("premium_pct", 0),
                "premium_vnd": premium.get("premium_vnd", 0),
                "sjc_sell": premium.get("sjc_sell", 0),
                "xau_usd_per_oz": premium.get("xau_usd_per_oz", 0),
                "xau_vnd_per_luong": premium.get("xau_vnd_per_luong", 0),
                "usd_vnd": premium.get("usd_vnd", 0),
                "signal": premium.get("signal", ""),
            },
        }
    }


def _default_gold_regime() -> dict:
    return {
        "gold_regime": "NEUTRAL",
        "velocity": 0.0,
        "spread_pressure": 0.0,
        "macro_bias": "NEUTRAL",
        "latest_price": 0.0,
        "ma20": 0.0,
        "ma10": 0.0,
        "roc_5d": 0.0,
        "deviation_pct": 0.0,
        "signals": {
            "above_ma20_5pct": False,
            "above_ma20_3pct": False,
            "below_ma20_3pct": False,
            "velocity_high": False,
            "spread_pressure_high": False,
        },
    }
