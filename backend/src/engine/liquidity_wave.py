"""
Liquidity Wave Engine (Phase 11 — Asia Adaptation Layer).
Measures retail chase velocity, volume acceleration, leader propagation, turnover shock.
VN/KR/TW markets are liquidity-driven, not efficiently priced.
"""
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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import logging

import pandas as pd

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)


def get_volume_profile(symbol: str, lookback: int = 60) -> dict:
    with get_connection() as conn:
        df = pd.read_sql(
            f"SELECT date, close, volume FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT {lookback}",
            conn, params=(symbol,)
        )
    if df.empty or len(df) < 30:
        return {"status": "INSUFFICIENT_DATA", "symbol": symbol}

    df = df.sort_values("date").reset_index(drop=True)
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_accel"] = df["vol_ma5"] / df["vol_ma20"] - 1.0
    df["value_20d"] = df["close"] * df["volume"] * 1000 / 1e9
    df["avg_value_20d"] = df["value_20d"].rolling(20).mean()

    latest = df.iloc[-1]
    vol_ma20 = latest["vol_ma20"]
    vol_ratio = latest["volume"] / vol_ma20 if vol_ma20 > 0 else 0

    vol_ma20_prev = df["vol_ma20"].iloc[-2] if len(df) >= 2 else vol_ma20
    vol_accel = (vol_ma20 / vol_ma20_prev - 1) if vol_ma20_prev > 0 else 0

    avg_value = latest["avg_value_20d"]
    turnover_shock = (latest["value_20d"] / avg_value) if avg_value and avg_value > 0 else 0

    df["vol_rank"] = df["volume"].rank(pct=True)
    latest_rank = df["vol_rank"].iloc[-1]

    vol_std = df["volume"].std()
    vol_mean = df["volume"].mean()
    vol_cv = vol_std / vol_mean if vol_mean > 0 else 0

    consecutive_up = 0
    for i in range(min(10, len(df) - 1)):
        if df["volume"].iloc[-(i + 1)] > df["vol_ma20"].iloc[-(i + 1)]:
            consecutive_up += 1
        else:
            break

    wave_strength = "NORMAL"
    if vol_ratio > 2.0 and vol_accel > 0.3 and turnover_shock > 1.5:
        wave_strength = "SURGE"
    elif vol_ratio > 1.5 and vol_accel > 0.15:
        wave_strength = "STRONG"
    elif vol_ratio < 0.5 and vol_accel < -0.1:
        wave_strength = "DROUGHT"

    return {
        "symbol": symbol,
        "vol_ratio": round(vol_ratio, 2),
        "vol_accel_20d": round(vol_accel, 3),
        "vol_rank_pct": round(latest_rank, 3),
        "vol_cv": round(vol_cv, 2),
        "avg_value_20d_bn": round(avg_value, 1) if avg_value else 0,
        "turnover_shock": round(turnover_shock, 2),
        "consecutive_high_vol_days": consecutive_up,
        "wave_strength": wave_strength,
        "retail_chase_score": round(min(1.0, vol_ratio * vol_accel * 3), 3),
    }


def scan_liquidity_waves(top_n: int = 30) -> list:
    with get_connection() as conn:
        universe = pd.read_sql(
            "SELECT DISTINCT symbol FROM daily_ohlcv WHERE date >= date('now', '-60 days') AND volume > 0",
            conn
        )
    results = []
    for sym in universe["symbol"].head(200):
        profile = get_volume_profile(sym)
        if profile.get("status") == "INSUFFICIENT_DATA":
            continue
        results.append(profile)

    results.sort(key=lambda x: x["retail_chase_score"], reverse=True)
    return results[:top_n]


def get_market_liquidity_health() -> dict:
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT date, SUM(volume) as total_vol, AVG(close * volume * 1000 / 1e9) as avg_value_bn "
            "FROM daily_ohlcv WHERE date >= date('now', '-30 days') GROUP BY date ORDER BY date",
            conn
        )
    if df.empty or len(df) < 5:
        return {"status": "INSUFFICIENT_DATA"}

    df["vol_ma5"] = df["total_vol"].rolling(5).mean()
    latest = df.iloc[-1]
    vol_trend = (latest["total_vol"] / df["vol_ma5"].iloc[-2] - 1) if len(df) >= 2 and df["vol_ma5"].iloc[-2] > 0 else 0

    df["value_ma5"] = df["avg_value_bn"].rolling(5).mean()
    value_trend = (latest["avg_value_bn"] / df["value_ma5"].iloc[-2] - 1) if len(df) >= 2 and df["value_ma5"].iloc[-2] > 0 else 0

    liquid_assets = pd.read_sql(
        "SELECT symbol, date, close, volume FROM daily_ohlcv "
        "WHERE date = (SELECT MAX(date) FROM daily_ohlcv) AND volume > 0",
        conn
    )
    total_value_bn = (liquid_assets["close"] * liquid_assets["volume"] * 1000 / 1e9).sum()

    top_10_pct = int(len(liquid_assets) * 0.1) or 1
    sorted_by_vol = liquid_assets.sort_values("volume", ascending=False)
    concentration = sorted_by_vol.head(top_10_pct)["volume"].sum() / liquid_assets["volume"].sum() if liquid_assets["volume"].sum() > 0 else 0

    return {
        "total_market_value_bn": round(total_value_bn, 0),
        "volume_trend_5d": round(vol_trend, 3),
        "value_trend_5d": round(value_trend, 3),
        "top10_concentration_pct": round(concentration * 100, 1),
        "liquidity_phase": "EXPANDING" if vol_trend > 0.05 and value_trend > 0.05
        else "CONTRACTING" if vol_trend < -0.05 and value_trend < -0.05
        else "NEUTRAL",
    }


def detect_retail_chase(threshold_vol_ratio: float = 1.8) -> list:
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT symbol, date, close, volume FROM daily_ohlcv "
            "WHERE date >= date('now', '-5 days') ORDER BY date",
            conn
        )
    if df.empty:
        return []

    df["vol_ma20"] = df.groupby("symbol")["volume"].transform(lambda x: x.rolling(20).mean())
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    df["value_bn"] = df["close"] * df["volume"] * 1000 / 1e9

    latest = df[df["date"] == df.groupby("symbol")["date"].transform("max")]
    chasers = latest[latest["vol_ratio"] >= threshold_vol_ratio].copy()
    chasers = chasers.sort_values("vol_ratio", ascending=False)
    chasers["retail_chase"] = chasers["vol_ratio"].apply(
        lambda r: "EXTREME" if r > 3.0 else "HIGH" if r > 2.5 else "MODERATE"
    )
    return chasers[["symbol", "vol_ratio", "value_bn", "retail_chase"]].head(20).to_dict("records")


if __name__ == "__main__":
    import json
    health = get_market_liquidity_health()
    print(f"Market Health: {json.dumps(health, ensure_ascii=False, indent=2)}")
    waves = scan_liquidity_waves(5)
    print(f"Top Waves: {json.dumps(waves, ensure_ascii=False, indent=2)}")
    chase = detect_retail_chase()
    print(f"Retail Chase: {json.dumps(chase, ensure_ascii=False, indent=2)}")
