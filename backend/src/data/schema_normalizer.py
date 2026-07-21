"""schema_normalizer.py — Schema Normalizer Adapter cho Multi-Source Fallback.

Mọi dữ liệu từ Tier 0.5 (KBS, VCI, SSI, TCBS, MSN, Web Scrapers)
BẮT BUỘC qua `normalize_to_ptd_schema()` trước khi vào pipeline.

Thiết kế: Declarative Alias Registry + get_safe() — không bao giờ KeyError.
Tự động phát hiện và quy chuẩn độ lệch đơn vị (VND/nghìn/triệu) qua auto_scale_ohlcv().
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.database.db_core import get_connection

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

# ── Scale Detection Constants ───────────────────────────────────────────
# PTD internal unit: VND (đồng), khối lượng: cổ phiếu
# Các web scraper có thể trả về nghìn đồng, triệu đồng,...
KNOWN_SCALES = [0.000001, 0.001, 1.0, 1000.0, 1000000.0]
SCALE_TOLERANCE = 0.15  # 15% tolerance cho nhiễm thị trường
OHLCV_FLOAT_FIELDS = {"open", "high", "low", "close", "adj_close"}


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


# ── Corporate Actions Adjustment ───────────────────────────────────────
# `adj_close` trong daily_ohlcv chỉ là copy của `close`.
# Để tránh auto_scale_ohlcv() hiểu sai split/stock dividend là scale mismatch,
# cần điều chỉnh reference price bằng sự kiện doanh nghiệp từ paper_corporate_actions.
# Công thức: price_factor = 1/(1+ratio) cho STOCK_DIV/SPLIT.
# ratio > 0 → pha loãng (giá giảm), ratio < 0 → hợp nhất (giá tăng).


def _get_corporate_action_factor(symbol: str, ref_date: str) -> float:
    """Tra cứu hệ số điều chỉnh giá do sự kiện doanh nghiệp.

    Tích lũy tất cả STOCK_DIV/SPLIT có ex_date >= ref_date - 30 ngày
    và <= ref_date + 1 ngày, để bắt đúng ngày giao dịch không hưởng quyền.

    Returns:
        float: Hệ số nhân (1.0 = không có sự kiện).
               VD: split 2:1 (ratio=1.0) → factor=0.5 → ref * 0.5
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT ratio FROM paper_corporate_actions "
                "WHERE symbol=? AND action_type IN ('STOCK_DIV','SPLIT') "
                "AND ex_date >= date(?, '-30 days') "
                "AND ex_date <= date(?, '+1 day') "
                "ORDER BY ex_date ASC",
                (symbol, ref_date, ref_date),
            ).fetchall()
        if not rows:
            return 1.0
        # Tích lũy tất cả sự kiện: price_factor = ∏ 1/(1+r)
        factor = 1.0
        for (r,) in rows:
            price_factor = 1.0 / (1.0 + float(r)) if (1.0 + float(r)) != 0 else 1.0
            factor *= price_factor
        if abs(factor - 1.0) > 0.001:
            logger.info(
                f"[CA_ADJ] {symbol} @ {ref_date}: {len(rows)} sự kiện → "
                f"price_factor={factor:.6f}"
            )
        return factor
    except Exception:
        return 1.0


def _get_reference_close(symbol: str, ref_date: Optional[str] = None) -> Optional[float]:
    """Lấy close giá gần nhất từ DB, điều chỉnh cho corporate actions.

    Args:
        symbol: Mã cổ phiếu
        ref_date: Ngày tham chiếu (YYYY-MM-DD) — để tra corporate actions.
                  Nếu None, dùng ngày hiện tại.

    Returns:
        float: Giá tham chiếu đã điều chỉnh, hoặc None nếu không có dữ liệu.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT close, date FROM daily_ohlcv WHERE symbol=? "
                "AND close IS NOT NULL AND close > 0 "
                "ORDER BY date DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row:
                ref_price = float(row[0])
                ref_date_db = row[1] or ref_date
                # Điều chỉnh cho corporate actions
                ca_factor = _get_corporate_action_factor(symbol, ref_date or ref_date_db)
                return ref_price * ca_factor
    except Exception:
        pass
    return None


def auto_scale_ohlcv(
    row: Dict[str, Any],
    symbol: str,
    source_label: Optional[str] = None,
    ref_date: Optional[str] = None,
) -> Tuple[Dict[str, Any], float, bool]:
    """Tự động phát hiện và quy chuẩn độ lệch đơn vị (×1000, ×0.001, …).

    Tích hợp corporate actions: nếu reference date có sự kiện doanh nghiệp
    (split, stock dividend), reference price được điều chỉnh trước khi so sánh
    để tránh nhầm lẫn giảm giá hợp lệ với lệch đơn vị.

    Args:
        row: Dict đã normalize (chứa ít nhất 'close')
        symbol: Mã cổ phiếu
        source_label: Nhãn nguồn để log
        ref_date: Ngày tham chiếu (YYYY-MM-DD) — tra corporate actions.

    Returns:
        Tuple (row_sau_khi_scale, scale_factor, was_scaled)
    """
    close = row.get("close")
    if close is None or not isinstance(close, (int, float)) or close <= 0:
        return row, 1.0, False

    ref_close = _get_reference_close(symbol, ref_date)
    if ref_close is None or ref_close <= 0:
        return row, 1.0, False

    ratio = ref_close / float(close)
    src_tag = f"[{source_label}] " if source_label else ""

    for scale in KNOWN_SCALES:
        if abs(scale - 1.0) < 0.0001:
            continue  # bỏ qua scale = 1 (không đổi)
        # Kiểm tra nếu ratio gần với scale mong đợi
        if abs(ratio / scale - 1.0) < SCALE_TOLERANCE:
            scaled = dict(row)
            for fld in OHLCV_FLOAT_FIELDS:
                v = scaled.get(fld)
                if v is not None and isinstance(v, (int, float)):
                    scaled[fld] = v * scale
            logger.info(
                f"{src_tag}{symbol}: auto_scale phát hiện lệch đơn vị "
                f"×{scale:.6f} (ref={ref_close}, incoming={close})"
            )
            return scaled, scale, True

    # Log cảnh báo nếu lệch quá xa nhưng không khớp scale nào
    if ratio > 100 or ratio < 0.01:
        logger.warning(
            f"{src_tag}{symbol}: tỷ lệ lệch {ratio:.4f} không khớp KNOWN_SCALE nào — "
            f"ref={ref_close}, incoming={close}"
        )

    return row, 1.0, False


def normalize_to_ptd_schema(
    raw: Dict[str, Any],
    source_label: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Ép mọi schema lạ về PTD schema chuẩn + tự động scale đơn vị.

    Args:
        raw: Dict dữ liệu thô từ bất kỳ provider nào
        source_label: Nhãn nguồn (KBS, VCI, SSI, ...) — để log

    Returns:
        Tuple (normalized_dict, scale_info)
        - normalized_dict: Dict với các trường PTD chuẩn
        - scale_info: None nếu không scale, hoặc dict {scale, was_scaled, ref_close}
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

    # Tự động lấy ngày tham chiếu để tra corporate actions
    ref_date = get_safe(raw, "date")
    if ref_date and isinstance(ref_date, str) and len(ref_date) >= 10:
        ref_date = ref_date[:10]  # chuẩn hóa YYYY-MM-DD

    # Tự động scale đơn vị nếu cần (có tích hợp corporate actions)
    scale_info = None
    if sym and normalized.get("close") is not None:
        scaled_row, scale_factor, was_scaled = auto_scale_ohlcv(
            normalized, str(sym).upper(), source_label, ref_date
        )
        if was_scaled:
            normalized = scaled_row
            scale_info = {"scale": scale_factor, "was_scaled": True}

    return normalized, scale_info


def normalize_batch(
    rows: List[Dict[str, Any]],
    source_label: str,
) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """Batch normalize — áp dụng normalize_to_ptd_schema() cho list dict.

    Returns:
        List of (normalized_dict, scale_info) tuples.
    """
    return [normalize_to_ptd_schema(r, source_label) for r in rows]


def get_missing_fields(record: Dict[str, Any]) -> List[str]:
    """Trả về danh sách trường PTD bị thiếu trong record."""
    required = ["open", "high", "low", "close", "volume"]
    return [f for f in required if f not in record or record[f] is None]


def validate_ohlcv(
    row: Dict[str, Any],
    symbol: Optional[str] = None,
    reference_close: Optional[float] = None,
    ref_date: Optional[str] = None,
) -> List[str]:
    """Kiểm tra tính hợp lệ OHLCV + phát hiện lệch đơn vị.

    Args:
        row: Dict OHLCV đã normalize
        symbol: Mã cổ phiếu (optional) — để tra reference nếu chưa có
        reference_close: Close tham chiếu (optional) — tránh query lại DB

    Returns:
        List[str] danh sách vấn đề (rỗng nếu hợp lệ).
    """
    issues = []
    close = get_safe(row, "close", coerce=float)
    high = get_safe(row, "high", coerce=float)
    low = get_safe(row, "low", coerce=float)
    open_ = get_safe(row, "open", coerce=float)

    # Geometric consistency (H ≥ max(O,C), L ≤ min(O,C))
    if close is not None and high is not None and close > high:
        issues.append(f"close({close}) > high({high}) — vi phạm nến")
    if close is not None and low is not None and close < low:
        issues.append(f"close({close}) < low({low}) — vi phạm nến")
    if open_ is not None and high is not None and open_ > high:
        issues.append(f"open({open_}) > high({high}) — vi phạm nến")
    if open_ is not None and low is not None and open_ < low:
        issues.append(f"open({open_}) < low({low}) — vi phạm nến")

    # Unit scale check: so sánh với reference_close (đã điều chỉnh CA)
    ref = reference_close
    if ref is None and symbol:
        ref = _get_reference_close(symbol, ref_date)
    if (
        ref is not None and ref > 0
        and close is not None and close > 0
    ):
        ratio = ref / close
        # Nếu ratio lệch khỏi tất cả KNOWN_SCALES → scale bất thường
        matched = any(
            abs(ratio / s - 1.0) < SCALE_TOLERANCE * 2
            for s in KNOWN_SCALES
        )
        if not matched and (ratio > 50 or ratio < 0.02):
            issues.append(
                f"scale bất thường: ref={ref}, close={close}, "
                f"ratio={ratio:.2f} — không khớp KNOWN_SCALE nào"
            )
        # Dù đã scale, kiểm tra nếu giá quá thấp (sót scale)
        if close < 1.0 and not any(
            abs(ref / (close * s) - 1.0) < SCALE_TOLERANCE
            for s in KNOWN_SCALES
        ):
            issues.append(
                f"close={close} quá thấp — nghi ngờ sót scale (ref={ref})"
            )

    return issues
