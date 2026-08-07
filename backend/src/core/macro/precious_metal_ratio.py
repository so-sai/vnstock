"""
Precious Metal Ratio — Gold/Silver Ratio engine.
Gold/Silver Ratio = XAUUSD / XAGUSD.

Lịch sử:
  Ratio > 85  → Fear / Risk-Off / USD shortage
  Ratio 70-85 → Elevated
  Ratio 55-70 → Neutral
  Ratio < 55  → Risk-On / Euphoria / Industrial boom
"""

import logging

logger = logging.getLogger(__name__)

# Ngưỡng lịch sử cho Gold/Silver Ratio
GS_EXTREME_FEAR = 85.0
GS_ELEVATED = 70.0
GS_NEUTRAL = 55.0


def calculate_gold_silver_ratio(xau_price: float | None, xag_price: float | None) -> float | None:
    """Tính Gold/Silver Ratio = XAUUSD / XAGUSD."""
    if xau_price is None or xag_price is None or xag_price == 0:
        return None
    return round(xau_price / xag_price, 2)


def assess_gs_ratio_regime(ratio: float | None) -> dict:
    """Đánh giá trạng thái Gold/Silver Ratio.

    Returns dict với:
      - ratio: giá trị ratio
      - regime: EXTREME_FEAR / ELEVATED / NEUTRAL / RISK_ON
      - signal: mô tả tín hiệu
      - severity: 0-1
    """
    if ratio is None:
        return {
            "ratio": None,
            "regime": "UNKNOWN",
            "signal": "Không đủ dữ liệu",
            "severity": 0.0,
        }

    if ratio >= GS_EXTREME_FEAR:
        regime = "EXTREME_FEAR"
        signal = "Sợ hãi cực độ — USD shortage, risk-off mạnh"
        severity = 0.95
    elif ratio >= GS_ELEVATED:
        regime = "ELEVATED"
        signal = "Bạc yếu hơn vàng — tâm lý phòng thủ"
        severity = 0.65
    elif ratio >= GS_NEUTRAL:
        regime = "NEUTRAL"
        signal = "Cân bằng giữa trú ẩn và công nghiệp"
        severity = 0.35
    else:
        regime = "RISK_ON"
        signal = "Bạc dẫn dắt — kỳ vọng tăng trưởng, industrial demand mạnh"
        severity = 0.20

    return {
        "ratio": ratio,
        "regime": regime,
        "signal": signal,
        "severity": severity,
    }


def get_gs_ratio_from_db() -> dict:
    """Lấy XAUUSD và XAGUSD từ macro_history, tính Gold/Silver Ratio."""
    try:
        from src.database.db_core import get_connection

        with get_connection() as conn:
            import pandas as pd

            df = pd.read_sql(
                """
                SELECT variable, value FROM (
                    SELECT variable, value, ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                    FROM macro_history
                    WHERE variable IN ('GOLD_XAU', 'XAGUSD')
                ) WHERE rn = 1
            """,
                conn,
            )

        if df.empty:
            return assess_gs_ratio_regime(None)

        values = {}
        for _, row in df.iterrows():
            values[row["variable"]] = row["value"]

        xau = values.get("GOLD_XAU")
        xag = values.get("XAGUSD")
        ratio = calculate_gold_silver_ratio(xau, xag)
        result = assess_gs_ratio_regime(ratio)
        result["xau_usd"] = xau
        result["xag_usd"] = xag
        return result

    except Exception as e:
        logger.error(f"GS ratio DB fetch failed: {e}")
        return assess_gs_ratio_regime(None)
