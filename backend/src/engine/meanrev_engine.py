import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.db_core import get_connection

def run_meanrev_scan(regime_data=None, target_date=None):
    """
    Model B: Mean Reversion Engine (T+10 to T+30).
    Săn tìm Pullback trong Uptrend mạnh hoặc Sideway ổn định.
    Includes [PHASE 7.5.1] Dual Context Lock (Hardened).
    """
    print("\n" + "="*50)
    print(f"MODEL B: {'HISTORICAL REPLAY' if target_date else 'LIVE ANALYSIS'}")
    print("="*50)

    # 1. Thu thập dữ liệu Regime & Breadth
    if not regime_data:
        from src.engine.regime_engine import detect_regime
        regime_data = detect_regime(target_date=target_date)
    
    details = regime_data['details']
    breadth_pct = details['breadth_pct']
    breadth_std = details['breadth_std_10d']
    breadth_mom = details['breadth_momentum']
    
    vnindex_above_ma200 = details['vnindex_vs_ma200'] == "ABOVE"
    vnindex_above_ma50 = details.get('vnindex_vs_ma50', 'BELOW') == "ABOVE"
    ma50_slope = details.get('ma50_slope', 0.0)
    atr_ratio = details['atr_ratio']

    # Configuration
    cfg = src.config.MODEL_B_CONFIG
    sec_cfg = cfg['secondary_context']
    
    # 2. [PHASE 7.5.1] DUAL CONTEXT LOCK (Hardened)
    context_source = "BLOCKED"
    
    primary_ok = vnindex_above_ma200
    secondary_ok = (
        vnindex_above_ma50 and 
        ma50_slope > sec_cfg['min_ma50_slope'] and 
        breadth_pct > sec_cfg['min_breadth'] and 
        breadth_std < sec_cfg['max_breadth_std']
    )
    
    if primary_ok:
        context_source = "PRIMARY_MA200"
    elif secondary_ok:
        context_source = "SECONDARY_STABLE"
    
    if context_source == "BLOCKED":
        print(f"Context Lock: No valid Trend Path. Blocked by {context_source}.")
        return []

    print(f">>> CONTEXT ENABLED via {context_source}")

    # 3. Adaptive Threshold Calculation
    if atr_ratio < 0.9:
        rsi_threshold = cfg['adaptive_rsi']['low_vol']
    elif atr_ratio < 1.3:
        rsi_threshold = cfg['adaptive_rsi']['mid_vol']
    else:
        rsi_threshold = cfg['adaptive_rsi']['standard']
    
    z_threshold = cfg['z_score_threshold']

    # [LOCK 1] Breadth Kill Switch: < 15% disable all MR
    if breadth_pct < 15:
        print("[LOCK 1] BREADTH KILL SWITCH TRIGGERED (<15%). Model B Disabled.")
        return []

    # 4. Fetch Data & All-Symbols Calculation
    with get_connection() as conn:
        if target_date:
            # 300 days to ensure MA200 calculation
            df = pd.read_sql(f"SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date <= '{target_date}' AND date >= date('{target_date}', '-300 days')", conn)
        else:
            df = pd.read_sql("SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date >= '2025-06-01'", conn)
    
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.sort_values(['symbol', 'date'])
    current_date = pd.to_datetime(target_date) if target_date else df['date'].max()
    
    # Vectorized calculation for universe
    g = df.groupby('symbol')
    df['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df['std20'] = g['close'].transform(lambda x: x.rolling(20).std())
    df['ma200'] = g['close'].transform(lambda x: x.rolling(200).mean())
    df['ma100'] = g['close'].transform(lambda x: x.rolling(100).mean()) # Used for secondary strength
    df['avg_vol_20d'] = g['volume'].transform(lambda x: x.rolling(20).mean())
    
    # RSI(14) calculation
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    df['rsi14'] = g['close'].transform(calc_rsi)
    
    # 5. Filter Universe
    latest_df = df[df['date'] == current_date].copy()
    
    # Filter: Follow the leading trend indicator
    # Note: Even in secondary context, we want stocks that are somewhat supported (MA100)
    trend_filter = latest_df['ma200'] if primary_ok else latest_df['ma100']
    
    candidates = latest_df[
        (latest_df['close'] > trend_filter) &
        (latest_df['avg_vol_20d'] >= 50000)
    ].copy()

    if candidates.empty:
        return []

    # 6. Entry Signals
    candidates['z_score'] = (candidates['close'] - candidates['ma20']) / candidates['std20']
    candidates['lower_bb'] = candidates['ma20'] - (2 * candidates['std20'])
    
    final_picks = candidates[
        (candidates['rsi14'] < rsi_threshold) &
        (candidates['z_score'] < z_threshold) &
        (candidates['close'] < candidates['lower_bb'])
    ].copy()

    print(f"Adaptive Limits: RSI < {rsi_threshold} | Z < {z_threshold} (ATR Ratio: {atr_ratio})")

    if final_picks.empty:
        print("No Mean Reversion candidates found satisfying exhaust criteria.")
        return []

    # 7. [LOCK 3] Liquidity-aware Ranking
    final_picks['vol_rank'] = final_picks['avg_vol_20d'].rank(pct=True)
    final_picks['rank_score'] = (0.6 * final_picks['z_score'].abs()) + (0.4 * final_picks['vol_rank'])
    
    final_picks = final_picks.sort_values('rank_score', ascending=False)
    
    results = []
    for _, row in final_picks.head(10).iterrows():
        print(f"Pick: {row['symbol']} | Z: {row['z_score']:.2f} | RSI: {row['rsi14']:.1f} | Context: {context_source}")
        results.append({
            "symbol": row['symbol'],
            "z_score": round(row['z_score'], 2),
            "rsi": round(row['rsi14'], 1),
            "rank_score": round(row['rank_score'], 2),
            "context_source": context_source
        })

    return results
