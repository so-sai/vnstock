import sys
import os
import logging
from pathlib import Path
from typing import Optional

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

import pandas as pd
import json
from src.database.db_core import get_connection
from src.engine.regime_engine import detect_regime
import src.config

logger = logging.getLogger(__name__)

# VN30 constituents for price anomaly guard
VN30_SYMBOLS = {
    'VCB', 'CTG', 'BID', 'HPG', 'FPT', 'MSN', 'VNM', 'VIC', 'VRE', 'VHM',
    'SSI', 'MWG', 'ACB', 'VPB', 'MBB', 'TCB', 'TPB', 'HDB', 'STB', 'EIB',
    'SHB', 'GAS', 'POW', 'PLX', 'SAB', 'BVH', 'VJC', 'PNJ', 'KDH', 'NVL',
}

_mfe_instance = None

def _get_mfe():
    global _mfe_instance
    if _mfe_instance is None:
        from src.engine.money_flow_engine import MoneyFlowEngine
        _mfe_instance = MoneyFlowEngine()
    return _mfe_instance


def _load_rs_data() -> dict:
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


def _resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Nén dữ liệu nến Daily sang Weekly (W) hoặc Monthly (M)."""
    rule = {'W': 'W', 'M': 'ME'}[timeframe]
    df = df.set_index('date')
    resampled = df.resample(rule).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['open', 'close']).reset_index()
    return resampled


def _check_price_anomaly(symbol: str, df: pd.DataFrame) -> Optional[str]:
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    prev_close = prev.get('adj_close') or prev.get('close')
    curr_close = curr.get('adj_close') or curr.get('close')
    if prev_close and curr_close and prev_close > 0:
        pct_change = abs((curr_close - prev_close) / prev_close) * 100
        if pct_change > 15 and symbol in VN30_SYMBOLS:
            msg = f"DATA_ANOMALY: {symbol} gap {pct_change:.1f}% (prev={prev_close}, curr={curr_close})"
            logger.warning(msg)
            return "⚠️ DATA_ANOMALY"
    return None


def _get_latest_ohlcv(symbol: str, timeframe: str = 'D') -> Optional[dict]:
    try:
        limit_map = {'D': 50, 'W': 200, 'M': 800}
        limit = limit_map.get(timeframe, 50)
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, adj_close, volume FROM daily_ohlcv "
                "WHERE symbol = ? ORDER BY date DESC LIMIT ?",
                conn, params=(symbol, limit)
            )
        if df.empty:
            return None

        df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed')
        df = df.sort_values('date')

        if timeframe in ('W', 'M'):
            df = _resample_ohlcv(df, timeframe)

        latest = df.iloc[-1]
        close_series = df['close'].values
        adj_close_series = df['adj_close'].values

        has_adj_close = not (df['adj_close'].isna().all() or (df['adj_close'] == 0).all())
        price_series = adj_close_series if has_adj_close else close_series
        display_price = float(latest['adj_close']) if has_adj_close else float(latest['close'])

        volume_series = df['volume'].values

        candle_count = len(df)
        vol_window = min(20, candle_count)

        vol_ma = pd.Series(volume_series).rolling(vol_window).mean().iloc[-1] if candle_count >= vol_window else None
        volume_ratio = round(latest['volume'] / vol_ma, 2) if vol_ma and vol_ma > 0 else None

        ma50 = pd.Series(price_series).rolling(50).mean().iloc[-1] if candle_count >= 50 else None
        ma200 = pd.Series(price_series).rolling(200).mean().iloc[-1] if candle_count >= 200 else None

        delta = pd.Series(price_series).diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs_series = gain / loss
        rsi14 = round(100 - (100 / (1 + rs_series)).iloc[-1], 1) if not rs_series.isna().iloc[-1] else None

        ma_z = pd.Series(price_series).rolling(20).mean().iloc[-1] if candle_count >= 20 else None
        std_z = pd.Series(price_series).rolling(20).std().iloc[-1] if candle_count >= 20 else None
        z_score = round((display_price - ma_z) / std_z, 2) if ma_z and std_z and std_z > 0 else None

        change_pct = round((latest['close'] - latest['open']) / latest['open'] * 100, 2) if latest['open'] > 0 else 0.0

        data_quality = _check_price_anomaly(symbol, df)

        ohlcv_history = []
        recent = df.tail(30)
        for _, row in recent.iterrows():
            ohlcv_history.append({
                "date": str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date']),
                "open": round(float(row['open']), 2),
                "high": round(float(row['high']), 2),
                "low": round(float(row['low']), 2),
                "close": round(float(row['close']), 2),
                "volume": int(row['volume']),
            })

        return {
            "price": display_price,
            "open": float(latest['open']),
            "high": float(latest['high']),
            "low": float(latest['low']),
            "volume": int(latest['volume']),
            "changePercent": change_pct,
            "volumeRatio": volume_ratio,
            "rsi14": rsi14,
            "zScore": z_score,
            "ma50": float(ma50) if ma50 else None,
            "ma200": float(ma200) if ma200 else None,
            "aboveMa50": bool(display_price > ma50) if ma50 else None,
            "aboveMa200": bool(display_price > ma200) if ma200 else None,
            "dataQuality": data_quality,
            "ohlcvHistory": ohlcv_history,
        }
    except Exception as e:
        logger.error(f"Error fetching OHLCV for {symbol}: {e}")
        return None


def _get_sector(symbol: str) -> str:
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT icb_name3 as sector FROM symbol_industry WHERE symbol = ?",
                conn, params=(symbol,)
            )
        return df.iloc[0]['sector'] if not df.empty else "Unknown"
    except Exception:
        return "Unknown"


def get_xray_data(symbol: str, timeframe: str = 'D') -> dict:
    symbol = symbol.upper()

    rs_data = _load_rs_data()
    rs_info = rs_data.get(symbol, {})

    ohlcv = _get_latest_ohlcv(symbol, timeframe)
    sector = _get_sector(symbol)

    foreign_10d = 0.0
    try:
        foreign_10d = round(_get_mfe().get_accumulation(symbol, 10), 1)
    except Exception:
        pass

    regime_data = {"status": "UNKNOWN", "score": 0.0}
    try:
        rd = detect_regime()
        regime_data = {
            "status": rd.get("status", "UNKNOWN"),
            "score": round(rd.get("regime_score", 0), 3),
        }
    except Exception:
        pass

    price = ohlcv.get('price') if ohlcv else float(rs_info.get('price', 0))
    change_pct = ohlcv.get('changePercent', 0.0) if ohlcv else 0.0
    volume_ratio = ohlcv.get('volumeRatio') if ohlcv else float(rs_info.get('rvol', 0))
    rsi14 = ohlcv.get('rsi14') if ohlcv else None
    z_score = ohlcv.get('zScore') if ohlcv else None

    rs_rating = int(rs_info.get('rs_rating', 0))
    rs_raw = float(rs_info.get('rs_raw', 0))
    rvol = float(rs_info.get('rvol', 0))
    change_1y = float(rs_info.get('change_1y', 0))

    data_quality = ohlcv.get('dataQuality') if ohlcv else None

    return {
        "symbol": symbol,
        "price": price,
        "changePercent": change_pct,
        "rsRating": rs_rating,
        "rsRaw": round(rs_raw, 4),
        "rvol": round(rvol, 2),
        "volumeRatio": volume_ratio,
        "change1y": round(change_1y, 2),
        "sector": sector,
        "rsi14": rsi14,
        "zScore": z_score,
        "foreign10dAcc": foreign_10d,
        "aboveMa50": ohlcv.get('aboveMa50') if ohlcv else None,
        "aboveMa200": ohlcv.get('aboveMa200') if ohlcv else None,
        "volumeSpike": bool(volume_ratio is not None and volume_ratio > 1.5),
        "regime": regime_data,
        "dataQuality": data_quality,
        "ohlcvHistory": ohlcv.get('ohlcvHistory', []) if ohlcv else [],
    }
