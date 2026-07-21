"""schema_normalizer.py — Schema Normalizer Adapter cho Multi-Source Fallback.

Mọi dữ liệu từ Tier 0.5 (KBS, VCI, SSI, TCBS, MSN, Web Scrapers)
BẮT BUỘC qua `normalize_to_ptd_schema()` trước khi vào pipeline.

Thiết kế: Declarative Alias Registry + get_safe() — không Bao giờ KeyError.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PTCK_NORMALIZER")

# ── Declarative Alias Registry ──────────────────────────────────────────
# Mỗi trường PTD ánh xạ đến danh sách alias (ưu tiên giảm dần).
# get_safe() thử từng alias, trả về None nếu không có alias nào tồn tại.

OHLCV_ALIASES: Dict[str, List[str]] = {
    "open":       ["open", "open_price", "mo_cua", "OPEN", "Open", "OPN", "o"],
    "high":       ["high", "high_price", "cao_nhat", "HIGH", "High", "HI", "h"],
    "low":        ["low", "low_price", "thap_nhat", "LOW", "Low", "LO", "l"],
    "close":      ["close", "close_price", "dong_cua", "CLOSE", "Close",
                   "CLS", "c", "last"],
    "adj_close":  ["adj_close", "adjClose", "adjclose", "adjusted_close",
                   "dieu_chinh", "ADJ_CLOSE", "adj"],
    "volume":     ["volume", "total_trades", "khoi_luong", "VOLUME", "Volume",
                   "Vol", "vol", "v", "VolumeWeighted"],
}

MACRO_ALIASES: Dict[str, List[str]] = {
    "date":       ["date", "Date", "DATE", "time", "Time", "TIME", "ngay", "t"],
    "symbol":     ["symbol", "Symbol", "SYMBOL", "ticker", "Ticker",
                   "ma_cp", "s", "Ticker"],
    "net_profit":  ["netProfit", "net_profit", "LNST", "loi_nhuan_rong",
                    "profit_after_tax", "NetProfit", "netIncome",
                    "net_income"],
    "revenue":     ["revenue", "doanh_thu", "DT", "Revenue", "total_revenue",
                    "TotalRevenue", "sales"],
    "eps":         ["eps", "EPS", "earnings_per_share", "thu_nhap_moi_co_phieu",
                    "EarningsPerShare"],
    "roe":         ["roe", "ROE", "return_on_equity", "ReturnOnEquity",
                    "ty_suat_loi_nhuan_von"],
    "roa":         ["roa", "ROA", "return_on_assets", "ReturnOnAssets"],
    "pe":          ["pe", "PE", "P/E", "price_to_earnings",
                    "PriceToEarnings", "PER"],
    "pb":          ["pb", "PB", "P/B", "price_to_book", "PriceToBook", "PBR"],
    "dividend_yield": ["dividend_yield", "DividendYield", "ty_le_co_tuc",
                       "dividendYield"],
    "market_cap":  ["market_cap", "MarketCap", "marketCapitalization",
                    "von_hoa", "marketCap", "capitalization"],
    "foreign_buy_vol": ["foreign_buy_volume", "foreign_buy_vol", "ForeignBuyVol",
                        "foreign_vol"],
    "foreign_sell_vol": ["foreign_sell_volume", "foreign_sell_vol", "ForeignSellVol"],
}

# Hợp nhất aliases cho PTD schema hoàn chỉnh
PTD_ALIASES: Dict[str, List[str]] = {**OHLCV_ALIASES, **MACRO_ALIASES}

# Provider-specific identity mapping
PROVIDER_IDENTITY: Dict[str, str] = {
    "kbs": "KBS",
    "vci": "VCI",
    "ssi": "SSI",
    "tcbs": "TCBS",
    "msn": "MSN",
    "yahoo": "YAHOO",
    "cafef": "CAFEF",
    "vietstock": "VIETSTOCK",
}


def get_safe(row: Dict[str, Any], ptd_field: str,
             default: Any = None, coerce: Optional[type] = None) -> Any:
    """Tra cứu an toàn: thử tất cả alias, không bao giờ KeyError.

    Args:
        row: Dict dữ liệu thô từ provider
        ptd_field: Tên trường PTD (ví dụ 'close', 'net_profit')
        default: Giá trị mặc định nếu không tìm thấy alias nào
        coerce: Ép kiểu (float, int, str) — None = giữ nguyên

    Returns:
        Giá trị từ alias đầu tiên tìm thấy, hoặc default.
    """
    aliases = PTD_ALIASES.get(ptd_field, [ptd_field])
    for alias in aliases:
        if alias in row and row[alias] is not None:
            val = row[alias]
            if coerce is float:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default
            if coerce is int:
                try:
                    return int(float(val))
                except (ValueError, TypeError):
                    return default
            return val
    return default


def normalize_to_ptd_schema(
    raw: Dict[str, Any],
    source_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Ép mọi schema lạ về PTD schema chuẩn.

    Args:
        raw: Dict dữ liệu thô từ bất kỳ provider nào
        source_label: Nhãn nguồn (KBS, VCI, SSI, ...) — để log

    Returns:
        Dict với các trường PTD chuẩn (chỉ chứa trường tìm thấy).

    Không bao giờ raise KeyError. Trường thiếu → None.
    """
    normalized = {}
    src_tag = f"[{source_label}] " if source_label else ""

    for ptd_field in list(OHLCV_ALIASES.keys()) + list(MACRO_ALIASES.keys()):
        val = get_safe(raw, ptd_field)
        if val is not None:
            normalized[ptd_field] = val

    # Log cảnh báo nếu thiếu trường OHLCV quan trọng
    for critical in ["open", "high", "low", "close"]:
        if critical not in normalized:
            logger.warning(f"{src_tag}Thiếu trường quan trọng '{critical}' — gán None")

    # Tự động map 'symbol' nếu có
    sym = get_safe(raw, "symbol")
    if sym:
        normalized["symbol"] = str(sym).upper()

    return normalized


def normalize_batch(
    rows: List[Dict[str, Any]],
    source_label: str,
) -> List[Dict[str, Any]]:
    """Batch normalize — áp dụng normalize_to_ptd_schema() cho list dict."""
    return [normalize_to_ptd_schema(r, source_label) for r in rows]


def get_missing_fields(record: Dict[str, Any]) -> List[str]:
    """Trả về danh sách trường PTD bị thiếu trong record."""
    required = ["open", "high", "low", "close", "volume"]
    return [f for f in required if f not in record or record[f] is None]


def validate_ohlcv(row: Dict[str, Any]) -> List[str]:
    """Kiểm tra tính hợp lệ OHLCV cơ bản."""
    issues = []
    close = get_safe(row, "close", coerce=float)
    high = get_safe(row, "high", coerce=float)
    low = get_safe(row, "low", coerce=float)
    open_ = get_safe(row, "open", coerce=float)
    if close and high and close > high:
        issues.append(f"close({close}) > high({high})")
    if close and low and close < low:
        issues.append(f"close({close}) < low({low})")
    if open_ and high and open_ > high:
        issues.append(f"open({open_}) > high({high})")
    if open_ and low and open_ < low:
        issues.append(f"open({open_}) < low({low})")
    return issues
