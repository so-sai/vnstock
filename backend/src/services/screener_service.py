"""
Screener Service Layer v1.0
Cầu nối giữa FastAPI Routes và Screener/RS Engines.
Xử lý: DataFrame → Dict transformation, RS merge, Fallback.
"""
import logging
import os
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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

import json

import pandas as pd

import src.config
from src.database.db_core import get_connection
from src.registry import registry

logger = logging.getLogger(__name__)


def _load_rs_data() -> dict:
    """Nạp RS Rating từ JSON file."""
    rs_path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    if not os.path.exists(rs_path):
        return {}
    try:
        with open(rs_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {item['symbol']: item for item in data}
    except Exception as e:
        logger.error(f"Error loading RS data: {e}")
        return {}


def _get_sector_map() -> dict:
    """Lấy mapping symbol → sector từ DB."""
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT symbol, icb_name3 as sector FROM symbol_industry", conn)
        return dict(zip(df['symbol'], df['sector']))
    except Exception:
        return {}


def get_screener_results(top_n: int = 50) -> list:
    """
    Lấy kết quả Screener + merge RS Rating + Sector.
    Trả về list of dict khớp với DiamondCandidate model.

    Args:
        top_n: Số lượng kết quả tối đa trả về.
    """
    try:
        result_df = registry.screener_logic.run_screener()
    except Exception as e:
        logger.error(f"Screener logic failed: {e}")
        result_df = pd.DataFrame()

    if result_df is None or getattr(result_df, 'empty', True):
        logger.info("Screener returned no results (market may be in CRISIS regime)")
        return _get_fallback_candidates(top_n)

    rs_data = _load_rs_data()
    sector_map = _get_sector_map()

    candidates = []
    for _, row in result_df.iterrows():
        symbol = row.get('symbol', '')
        rs_info = rs_data.get(symbol, {})

        candidates.append({
            "symbol": symbol,
            "price": float(row.get('close', 0)),
            "changePercent": round(float(row.get('close', 0) - row.get('open', 0)) / max(row.get('open', 1), 0.01) * 100, 2),
            "return6m": round(rs_info.get('change_1y', 0) * 0.5, 2),
            "signalV1": "Breakout",
            "volumeRatio": round(float(row.get('volume', 0)) / max(row.get('vol_ma20', 1), 1), 2),
            "rsRating": int(rs_info.get('rs_rating', 0)),
            "sector": sector_map.get(symbol, "Unknown"),
        })

        if len(candidates) >= top_n:
            break

    return candidates


def _get_fallback_candidates(top_n: int = 50) -> list:
    """
    Fallback: Nếu screener không có kết quả (CRISIS regime),
    trả về top RS Rating candidates thay thế.
    """
    rs_data = _load_rs_data()
    if not rs_data:
        return []

    sector_map = _get_sector_map()

    sorted_symbols = sorted(rs_data.items(), key=lambda x: x[1].get('rs_rating', 0), reverse=True)

    candidates = []
    for symbol, data in sorted_symbols[:top_n]:
        candidates.append({
            "symbol": symbol,
            "price": float(data.get('price', 0)),
            "changePercent": 0.0,
            "return6m": round(data.get('change_1y', 0) * 0.5, 2),
            "signalV1": "RS-High",
            "volumeRatio": float(data.get('rvol', 0)),
            "rsRating": int(data.get('rs_rating', 0)),
            "sector": sector_map.get(symbol, "Unknown"),
        })

    return candidates


def get_rs_rankings(top_n: int = 100) -> list:
    """
    Lấy danh sách xếp hạng RS Rating.
    """
    rs_data = _load_rs_data()
    if not rs_data:
        return []

    sector_map = _get_sector_map()

    sorted_items = sorted(rs_data.items(), key=lambda x: x[1].get('rs_rating', 0), reverse=True)

    results = []
    for symbol, data in sorted_items[:top_n]:
        try:
            price_val = data.get('price', 0)
            rvol_val = data.get('rvol', 0)
            avg_vol_val = data.get('avg_vol_20d', 0)
            change_1y_val = data.get('change_1y', 0)
            rs_raw_val = data.get('rs_raw', 0)
            rs_rating_val = data.get('rs_rating', 0)

            # Bộ lọc thanh khoản thép: Giá trị GD TB 20N >= 2 tỷ VND
            # price (nghìn đồng) * avgVol20d (số CP) >= 2,000,000 (nghìn đồng)
            trading_value_20d = float(price_val) * float(avg_vol_val)
            if trading_value_20d < 2000000:
                continue

            results.append({
                "symbol": symbol,
                "rsRating": int(rs_rating_val) if rs_rating_val is not None else 0,
                "rsRaw": round(float(rs_raw_val), 4) if rs_raw_val is not None else 0.0,
                "price": float(price_val) if price_val is not None else 0.0,
                "rvol": float(rvol_val) if rvol_val is not None else 0.0,
                "avgVol20d": float(avg_vol_val) if avg_vol_val is not None else 0.0,
                "change1y": round(float(change_1y_val), 2) if change_1y_val is not None else 0.0,
                "sector": sector_map.get(symbol, "Unknown"),
            })
        except (TypeError, ValueError) as e:
            logger.warning(f"Skipping {symbol} due to data error: {e}")
            continue

    return results
