
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

# Sentinel v2.1 (Anchor Fix)
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
import src.config
from src.database.db_core import get_connection

def _calc_adx(df, period=14):
    """Calculates ADX for a given OHLCV DataFrame."""
    df = df.copy()
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.rolling(period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx

def detect_regime(target_date=None):
    """
    Institutional Regime Engine v2.0 (Score-Based):
    Calculates Regime Score (RS) = 0.5*B + 0.3*T + 0.2*V
    Includes [LOCK 2] ATR Shock Filter.
    Supports Point-in-time accuracy via target_date.
    """
    print("\n" + "="*50)
    print(f"REGIME ENGINE v2.0: {'HISTORICAL REPLAY' if target_date else 'LIVE ANALYSIS'}")
    print("="*50)

    # 1. B-Score (Breadth): 50% Weight
    with get_connection() as conn:
        if target_date:
            # Point-in-time window: 60 days before target_date to ensure MA20 calculation
            df_all = pd.read_sql(f"SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date <= '{target_date}' AND date >= date('{target_date}', '-60 days') AND symbol != 'VNINDEX'", conn)
        else:
            df_all = pd.read_sql("SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date >= '2025-10-01' AND symbol != 'VNINDEX'", conn)
    
    df_all = df_all.copy()
    df_all.loc[:, 'date'] = pd.to_datetime(df_all['date'], format='mixed')
    df_all = df_all.sort_values(['symbol', 'date'])
    current_date = pd.to_datetime(target_date) if target_date else df_all['date'].max()
    
    g = df_all.groupby('symbol')
    df_all['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df_all['avg_vol_20d'] = g['volume'].transform(lambda x: x.rolling(20).mean())
    
    latest_df = df_all[df_all['date'] == current_date].copy()
    liquid_df = latest_df[latest_df['avg_vol_20d'] >= 50000]
    
    breadth_pct = (len(liquid_df[liquid_df['close'] > liquid_df['ma20']]) / len(liquid_df) * 100) if not liquid_df.empty else 0
    
    # [INTERNAL HOOK] Calculate Breadth Stability (STD 10D) & Momentum
    with get_connection() as conn:
        if target_date:
            df_hist_b = pd.read_sql(f"SELECT breadth_pct FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 10", conn)
        else:
            df_hist_b = pd.read_sql("SELECT breadth_pct FROM regime_history ORDER BY date DESC LIMIT 10", conn)
    
    # Calculate rolling STD including current data
    all_breadth = df_hist_b['breadth_pct'].tolist() + [breadth_pct]
    breadth_std_10d = np.std(all_breadth) if len(all_breadth) >= 2 else 0.0
    
    # Calculate Breadth Momentum (vs 5 days ago)
    breadth_5d_ago = df_hist_b['breadth_pct'].iloc[4] if len(df_hist_b) >= 5 else (df_hist_b['breadth_pct'].iloc[-1] if not df_hist_b.empty else breadth_pct)
    breadth_momentum = breadth_pct - breadth_5d_ago

    # Breadth Logic: >55(1), 35-55(0.6), 15-35(0.3), <15(0)
    b_score = 1.0 if breadth_pct > 55 else 0.6 if breadth_pct > 35 else 0.3 if breadth_pct > 15 else 0.0

    # 2. T-Score (Trend): 30% Weight
    with get_connection() as conn:
        if target_date:
            df_idx = pd.read_sql(f"SELECT symbol, date, high, low, close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date <= '{target_date}' ORDER BY date", conn)
        else:
            df_idx = pd.read_sql("SELECT symbol, date, high, low, close FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date", conn)
    
    df_idx = df_idx.copy()
    if df_idx.empty:
        latest_date = (pd.to_datetime(target_date) if target_date else datetime.now()).strftime("%Y-%m-%d")
        return {
            "date": latest_date,
            "regime_score": 0.5,
            "status": "RANGING",
            "details": {
                "b_score": 0.5,
                "breadth_pct": 50.0,
                "breadth_std_10d": 0.0,
                "breadth_momentum": 0.0,
                "t_score": 0.5,
                "vnindex_vs_ma200": "NO_DATA",
                "vnindex_vs_ma50": "NO_DATA",
                "ma50_slope": 0.0,
                "adx": 0.0,
                "v_score": 0.5,
                "atr_ratio": 1.0,
                "error": "NO_VNINDEX_DATA",
            }
        }
    df_idx.loc[:, 'date'] = pd.to_datetime(df_idx['date'], format='mixed')
    df_idx.loc[:, 'ma200'] = df_idx['close'].rolling(200).mean()
    df_idx.loc[:, 'ma50'] = df_idx['close'].rolling(50).mean()
    df_idx.loc[:, 'adx'] = _calc_adx(df_idx)
    latest_idx = df_idx.iloc[-1]
    
    # MA50 Slope (current vs 5 days ago)
    ma50_today = latest_idx['ma50']
    ma50_5d_ago = df_idx['ma50'].iloc[-6] if len(df_idx) >= 6 else ma50_today
    ma50_slope = ma50_today - ma50_5d_ago
    
    # Trend Logic: >MA200&ADX>20(1), >MA200&ADX<20(0.6), <MA200(0)
    if latest_idx['close'] > latest_idx['ma200']:
        t_score = 1.0 if latest_idx['adx'] > 20 else 0.6
    else:
        t_score = 0.0

    # 3. V-Score (Volatility): 20% Weight
    tr = pd.concat([
        df_idx['high'] - df_idx['low'],
        (df_idx['high'] - df_idx['close'].shift(1)).abs(),
        (df_idx['low'] - df_idx['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    
    atr_20 = tr.rolling(20).mean()
    atr_today = tr.iloc[-1]
    atr_avg = atr_20.iloc[-1]
    
    # Vol Score Logic: Compressing(1), Stable(0.6), Expanding(0.2)
    # [LOCK 2] ATR Spike Multiplier: if ATR > 1.5 * Avg -> reduction loop
    expanding = atr_today > atr_avg
    v_score = 0.2 if expanding else 0.6 if abs(atr_today - atr_avg) < 0.2 else 1.0
    
    # 4. Final Aggregation
    regime_score = (0.5 * b_score) + (0.3 * t_score) + (0.2 * v_score)
    
    # Apply LOCK 2: ATR Shock absorption
    if atr_today > 1.5 * atr_avg:
        print(f"⚠️ [LOCK 2] ATR SHOCK DETECTED (Today: {atr_today:.2f} vs Avg: {atr_avg:.2f}). Reducing Score.")
        regime_score *= 0.7

    status = "TRENDING" if regime_score > 0.65 else "RANGING" if regime_score >= 0.35 else "CRISIS"

    verdict = {
        "date": current_date.strftime("%Y-%m-%d"),
        "regime_score": round(regime_score, 2),
        "status": status,
        "details": {
            "b_score": b_score,
            "breadth_pct": round(breadth_pct, 1),
            "breadth_std_10d": round(breadth_std_10d, 2),
            "breadth_momentum": round(breadth_momentum, 1),
            "t_score": t_score,
            "vnindex_vs_ma200": "ABOVE" if latest_idx['close'] > latest_idx['ma200'] else "BELOW",
            "vnindex_vs_ma50": "ABOVE" if latest_idx['close'] > latest_idx['ma50'] else "BELOW",
            "ma50_slope": round(ma50_slope, 2),
            "adx": round(latest_idx['adx'], 1),
            "v_score": v_score,
            "atr_ratio": round(atr_today / atr_avg, 2)
        }
    }

    print(f"B-Score: {b_score} ({breadth_pct:.1f}%)")
    print(f"T-Score: {t_score} (VNINDEX {verdict['details']['vnindex_vs_ma200']}, ADX: {verdict['details']['adx']})")
    print(f"V-Score: {v_score} (ATR Ratio: {verdict['details']['atr_ratio']})")
    print("-" * 30)
    flag = ">>" if sys.platform == "win32" else "\U0001f6a9"
    print(f"{flag} FINAL REGIME SCORE: {regime_score:.2f} -> {status}")
    print("="*50)

    return verdict

if __name__ == "__main__":
    detect_regime()
