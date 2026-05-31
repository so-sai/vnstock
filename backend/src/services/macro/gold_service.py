"""
Gold Service — chuẩn hóa SJC + BTMC thành GoldPriceSnapshot
Dùng provenance-aware để Sentinel hiểu bối cảnh giá vàng.
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, date
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

logger = logging.getLogger(__name__)


class GoldPriceSnapshot(BaseModel):
    source: str
    timestamp: int
    brand: str
    branch: Optional[str] = None
    buy_price: float
    sell_price: float
    spread: float
    normalized_price: float


def fetch_sjc_snapshot(target_date: Optional[str] = None) -> list[GoldPriceSnapshot]:
    """Fetch SJC gold prices and return standardized snapshots."""
    try:
        df = sjc_gold_price(date=target_date)
        if df is None or df.empty:
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
        return snapshots
    except Exception as e:
        logger.error(f"SJC fetch failed: {e}")
        return []


def fetch_btmc_snapshot() -> list[GoldPriceSnapshot]:
    """Fetch BTMC gold prices and return standardized snapshots."""
    try:
        df = btmc_goldprice()
        if df is None or df.empty:
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
        return snapshots
    except Exception as e:
        logger.error(f"BTMC fetch failed: {e}")
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