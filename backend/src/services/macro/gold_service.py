"""
Gold Service — chuẩn hóa SJC + BTMC thành GoldPriceSnapshot
Dùng provenance-aware để Sentinel hiểu bối cảnh giá vàng.

[SENTINEL GUARD] In-memory cache layer — chống vnstock Rate Limit
- LIVE cache (target_date=None): TTL 12 tiếng (đủ 1 phiên giao dịch)
- HISTORICAL cache (target_date=YYYY-MM-DD): TTL 1 tiếng (chỉ để chống re-query)
- Cache key theo target_date — backfill nhiều ngày không đụng nhau
- Chỉ cache khi DataFrame có dữ liệu thật (tránh cache rỗng khi API lỗi)
- KHÔNG thay đổi signature/public API của hàm
"""
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Optional

# ── Sentinel Macro Cache ─────────────────────────────────────
LIVE_CACHE_TTL = 43200      # 12 tiếng cho dữ liệu live (None)
HISTORICAL_CACHE_TTL = 3600  # 1 tiếng cho dữ liệu lịch sử (backfill safety)
_SJC_CACHE: dict = {}        # key=target_date_str|None -> (data, expiry_ts)
_BTMC_CACHE: dict = {}
logger_macro = logging.getLogger(__name__)

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
    vnstock_path = str(root_path / "backend" / "libs" / "vnstock")
    if vnstock_path not in sys.path:
        sys.path.insert(0, vnstock_path)
    libs_path = str(root_path / "backend" / "libs")
    if libs_path not in sys.path:
        sys.path.insert(0, libs_path)
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
from pydantic import BaseModel, Field
from vnstock.explorer.misc.gold_price import sjc_gold_price, btmc_goldprice
from src.database.db_core import get_connection
from src.utils.defense import CircuitBreaker, APIBlockedError, guarded_call

logger = logging.getLogger(__name__)
_VNSTOCK_SOURCE = "vnstock"


class GoldPriceSnapshot(BaseModel):
    source: str
    timestamp: int
    brand: str
    branch: Optional[str] = None
    buy_price: float
    sell_price: float
    spread: float
    normalized_price: float


def _cache_get(cache: dict, key: str) -> Optional[list]:
    """Lấy dữ liệu từ cache nếu còn hạn. Trả về None nếu hết hạn hoặc không có."""
    entry = cache.get(key)
    if entry is None:
        return None
    data, expiry = entry
    if time.time() > expiry:
        cache.pop(key, None)
        return None
    logger_macro.debug(f"[MACRO CACHE] HIT key={key} age={int(time.time() - (expiry - (LIVE_CACHE_TTL if key == 'live' else HISTORICAL_CACHE_TTL)))}s")
    return data


def _cache_set(cache: dict, key: str, data: list) -> None:
    """Chỉ cache khi data thật sự có nội dung (không cache mảng rỗng)."""
    if not data:
        return
    ttl = LIVE_CACHE_TTL if key == "live" else HISTORICAL_CACHE_TTL
    cache[key] = (data, time.time() + ttl)
    logger_macro.info(f"[MACRO CACHE] SET key={key} ttl={ttl}s items={len(data)}")


def fetch_sjc_snapshot(target_date: Optional[str] = None) -> list[GoldPriceSnapshot]:
    """Fetch SJC gold prices and return standardized snapshots.
    
    [SENTINEL GUARD] Có in-memory cache 12h (live) / 1h (historical)
    để chống vnstock Rate Limit. Click tab nhiều lần KHÔNG tốn thêm API quota.
    """
    cache_key = "live" if target_date is None else f"hist:{target_date}"
    
    # 1. Trả cache nếu còn hạn (không gọi API)
    cached = _cache_get(_SJC_CACHE, cache_key)
    if cached is not None:
        return cached
    
    # 2. Cache miss → kiểm tra Circuit Breaker TRƯỚC khi gọi API
    if not CircuitBreaker.is_available(_VNSTOCK_SOURCE):
        remaining = CircuitBreaker.time_remaining(_VNSTOCK_SOURCE)
        logger_macro.warning(
            f"[MACRO CACHE] vnstock breaker OPEN — còn {remaining}s. "
            f"Trả cache rỗng cho SJC (key={cache_key})."
        )
        return []
    try:
        df = sjc_gold_price(date=target_date)
        if df is None or df.empty:
            logger_macro.warning(f"[MACRO CACHE] SJC empty for key={cache_key} — NOT caching")
            return []
        snapshots = []
        for _, row in df.iterrows():
            buy = float(row['buy_price'])
            sell = float(row['sell_price'])
            snapshots.append(GoldPriceSnapshot(
                source="SJC",
                timestamp=int(datetime.now().timestamp()),
                brand=str(row['name']),
                branch=str(row['branch']) if pd.notna(row.get('branch')) else None,
                buy_price=buy,
                sell_price=sell,
                spread=round(sell - buy, 2),
                normalized_price=round((buy + sell) / 2, 2),
            ))
        # 3. Chỉ cache khi có dữ liệu thật
        _cache_set(_SJC_CACHE, cache_key, snapshots)
        return snapshots
    except Exception as e:
        logger.error(f"SJC fetch failed: {e}")
        # Kích hoạt Circuit Breaker nếu lỗi thuộc pattern rate-limit / 5xx
        if CircuitBreaker.should_trip_on_error(str(e)):
            CircuitBreaker.report_failure(_VNSTOCK_SOURCE, reason=f"SJC: {str(e)[:200]}")
        return []


def fetch_btmc_snapshot() -> list[GoldPriceSnapshot]:
    """Fetch BTMC gold prices and return standardized snapshots.
    
    [SENTINEL GUARD] Có in-memory cache 12h (BTMC chỉ support live).
    """
    cache_key = "live"
    
    cached = _cache_get(_BTMC_CACHE, cache_key)
    if cached is not None:
        return cached

    # Circuit Breaker guard — chặn gọi API nếu vnstock đang trong cooldown 12h
    if not CircuitBreaker.is_available(_VNSTOCK_SOURCE):
        remaining = CircuitBreaker.time_remaining(_VNSTOCK_SOURCE)
        logger_macro.warning(
            f"[MACRO CACHE] vnstock breaker OPEN — còn {remaining}s. "
            f"Trả cache rỗng cho BTMC."
        )
        return []

    try:
        df = btmc_goldprice()
        if df is None or df.empty:
            logger_macro.warning(f"[MACRO CACHE] BTMC empty — NOT caching")
            return []
        snapshots = []
        for _, row in df.iterrows():
            buy = float(row['buy_price']) if row['buy_price'] else 0.0
            sell = float(row['sell_price']) if row['sell_price'] else 0.0
            if buy == 0 and sell == 0:
                continue
            snapshots.append(GoldPriceSnapshot(
                source="BTMC",
                timestamp=int(datetime.now().timestamp()),
                brand=str(row['name']),
                branch=None,
                buy_price=buy,
                sell_price=sell,
                spread=round(sell - buy, 2),
                normalized_price=round((buy + sell) / 2, 2),
            ))
        _cache_set(_BTMC_CACHE, cache_key, snapshots)
        return snapshots
    except Exception as e:
        logger.error(f"BTMC fetch failed: {e}")
        if CircuitBreaker.should_trip_on_error(str(e)):
            CircuitBreaker.report_failure(_VNSTOCK_SOURCE, reason=f"BTMC: {str(e)[:200]}")
        return []


def get_gold_dashboard() -> dict:
    """Lấy snapshot SJC chính + BTMC + spread để hiển thị dashboard."""
    sjc = fetch_sjc_snapshot()
    btmc = fetch_btmc_snapshot()
    main_sjc = next((s for s in sjc if "SJC" in s.brand.upper()), None)
    main_btmc = next((s for s in btmc if "SJC" in s.brand.upper()), btmc[0] if btmc else None)
    result = {
        "sjc_buy": main_sjc.buy_price if main_sjc else 0,
        "sjc_sell": main_sjc.sell_price if main_sjc else 0,
        "sjc_spread": main_sjc.spread if main_sjc else 0,
        "btmc_buy": main_btmc.buy_price if main_btmc else 0,
        "btmc_sell": main_btmc.sell_price if main_btmc else 0,
        "btmc_spread": main_btmc.spread if main_btmc else 0,
        "sjc_brand": main_sjc.brand if main_sjc else "",
        "btmc_brand": main_btmc.brand if main_btmc else "",
        "all_sjc": [s.model_dump() for s in sjc],
        "all_btmc": [s.model_dump() for s in btmc],
    }
    return result


def get_gold_cognition_layer() -> dict:
    """
    Gold Cognition Layer — hợp nhất VN Gold + Global Gold + Premium.
    Trả về cấu trúc đầy đủ cho Sentinel macro insight.
    """
    from src.services.macro.gold_world_service import fetch_world_gold_live
    from core.macro.gold_spread_engine import analyze_domestic_premium

    dashboard = get_gold_dashboard()
    xau = fetch_world_gold_live()
    premium = analyze_domestic_premium()

    return {
        "vn_gold": {
            "sjc": {
                "buy": dashboard.get("sjc_buy", 0),
                "sell": dashboard.get("sjc_sell", 0),
                "spread": dashboard.get("sjc_spread", 0),
                "brand": dashboard.get("sjc_brand", ""),
            },
            "btmc": {
                "buy": dashboard.get("btmc_buy", 0),
                "sell": dashboard.get("btmc_sell", 0),
                "spread": dashboard.get("btmc_spread", 0),
                "brand": dashboard.get("btmc_brand", ""),
            },
        },
        "global_gold": {
            "xau_usd_per_oz": xau if xau else premium.get("xau_usd_per_oz", 0),
            "xau_vnd_per_luong": premium.get("xau_vnd_per_luong", 0),
        },
        "domestic_premium": {
            "premium_pct": premium.get("premium_pct", 0),
            "premium_vnd": premium.get("premium_vnd", 0),
            "premium_regime": premium.get("premium_regime", "PREMIUM_NORMAL"),
            "signal": premium.get("signal", ""),
        },
    }