
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
    df_all['date'] = pd.to_datetime(df_all['date'], format='mixed')
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

    # Breadth Score: continuous linear mapping [0, 100] -> [0.0, 1.0]
    # Replaces discrete step function to eliminate whipsaw at hard thresholds
    b_score = max(0.0, min(1.0, breadth_pct / 100.0))

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
    df_idx['date'] = pd.to_datetime(df_idx['date'], format='mixed')
    df_idx.loc[:, 'ma200'] = df_idx['close'].rolling(200).mean()
    df_idx.loc[:, 'ma50'] = df_idx['close'].rolling(50).mean()
    df_idx.loc[:, 'adx'] = _calc_adx(df_idx)
    latest_idx = df_idx.iloc[-1]
    
    # MA50 Slope (current vs 5 days ago)
    ma50_today = latest_idx['ma50']
    ma50_5d_ago = df_idx['ma50'].iloc[-6] if len(df_idx) >= 6 else ma50_today
    ma50_slope = ma50_today - ma50_5d_ago
    
    # Trend Score: still discrete (3-state) — MA200 crossing is a structural binary gate
    # ADX sub-level uses 0.6 to preserve the partial-trend signal when above MA200 but low momentum
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
    
    # Volatility Score: continuous inverse of ATR ratio excess
    # v_score = 1.0 - clamp(atr_ratio - 1.0, 0.0, 0.8)  ->  range [0.2, 1.0]
    # Replaces discrete 3-step to eliminate cliff-edge jumps at 1.5x ATR boundary
    atr_ratio = (atr_today / atr_avg) if atr_avg and atr_avg > 0 else 1.0
    v_score = max(0.2, min(1.0, 1.0 - max(0.0, min(0.8, atr_ratio - 1.0))))
    
    # 4. Final Aggregation — Raw Score
    regime_score_raw = (0.5 * b_score) + (0.3 * t_score) + (0.2 * v_score)

    # [LOCK 2] ATR Shock: applied to raw score before smoothing so the EMA sees the shock signal
    if atr_today > 1.5 * atr_avg:
        flag = ">>" if sys.platform == "win32" else "\u26a0\ufe0f"
        print(f"{flag} [LOCK 2] ATR SHOCK DETECTED (Today: {atr_today:.2f} vs Avg: {atr_avg:.2f}). Reducing Raw Score.")
        regime_score_raw *= 0.7

    # 5. Adaptive EMA Smoothing
    # alpha_t = clamp(0.2 * atr_ratio, 0.1, 1.0)
    #   -> low volatility  : alpha near 0.1 (heavy smoothing, filters daily noise)
    #   -> high volatility  : alpha near 1.0 (pass-through, zero-lag on structural breaks)
    ema_alpha = max(0.1, min(1.0, 0.2 * atr_ratio))

    # Seed: fetch the most recent smoothed regime_score from SQLite regime_history
    with get_connection() as conn:
        if target_date:
            df_prev = pd.read_sql(
                f"SELECT regime_score FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 1",
                conn
            )
        else:
            df_prev = pd.read_sql(
                "SELECT regime_score FROM regime_history ORDER BY date DESC LIMIT 1",
                conn
            )
    prev_smoothed = df_prev['regime_score'].iloc[0] if not df_prev.empty else regime_score_raw

    # EMA formula: RS_smoothed = alpha * RS_raw + (1 - alpha) * RS_prev
    regime_score = (ema_alpha * regime_score_raw) + ((1.0 - ema_alpha) * prev_smoothed)

    # Status classification applied to the SMOOTHED score
    status = "TRENDING" if regime_score > 0.65 else "RANGING" if regime_score >= 0.35 else "CRISIS"

    # [RAD] Regime Acceleration Detector — override layer
    # Detects phase transition BEFORE EMA catches up
    rad = {"activated": False, "override_status": None, "signals": {}}
    try:
        idx_len = len(df_idx)
        if idx_len >= 10:
            # ΔADX = ADX_today - ADX_{t-3} (find ~3 trading days back)
            lookback = min(4, idx_len - 2)
            adx_today = float(latest_idx['adx'])
            adx_t3 = float(df_idx['adx'].iloc[-1 - lookback])
            delta_adx = adx_today - adx_t3

            # v_breadth = (breadth_today - breadth_prev) / 3 (fixed 3-day lookback per spec)
            with get_connection() as conn:
                if target_date:
                    df_b = pd.read_sql(
                        f"SELECT date, breadth_pct FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 1",
                        conn
                    )
                else:
                    df_b = pd.read_sql(
                        "SELECT date, breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1",
                        conn
                    )
            breadth_prev = float(df_b['breadth_pct'].iloc[0]) if not df_b.empty else breadth_pct
            v_breadth = (breadth_pct - breadth_prev) / 3

            # Gold premium (stress signal)
            gold_premium = None
            try:
                sys.path.insert(0, str(PROJECT_ROOT))
                from core.macro.gold_spread_engine import analyze_domestic_premium
                gp = analyze_domestic_premium()
                gold_premium = gp.get('premium_pct', 0)
            except Exception:
                pass

            rad["signals"] = {
                "delta_adx": round(delta_adx, 2),
                "v_breadth": round(v_breadth, 2),
                "gold_premium": gold_premium,
            }

            # TRANSITION_DOWN_SHOCK: ΔADX > 5 AND v_breadth < -3 AND gold_premium > 3
            if (delta_adx > 5.0 and v_breadth < -3.0
                    and gold_premium is not None and gold_premium > 3.0):
                status = "CRISIS_WARNING"
                rad["activated"] = True
                rad["override_status"] = "CRISIS_WARNING"
                rad["reason"] = "TRANSITION_DOWN_SHOCK"
    except Exception:
        pass

    verdict = {
        "date": current_date.strftime("%Y-%m-%d"),
        "regime_score": round(regime_score, 2),
        "status": status,
        "regime_score_raw": round(regime_score_raw, 4),
        "ema_alpha": round(ema_alpha, 4),
        "rad": rad,
        "details": {
            "b_score": round(b_score, 4),
            "breadth_pct": round(breadth_pct, 1),
            "breadth_std_10d": round(breadth_std_10d, 2),
            "breadth_momentum": round(breadth_momentum, 1),
            "t_score": round(t_score, 4),
            "vnindex_vs_ma200": "ABOVE" if latest_idx['close'] > latest_idx['ma200'] else "BELOW",
            "vnindex_vs_ma50": "ABOVE" if latest_idx['close'] > latest_idx['ma50'] else "BELOW",
            "ma50_slope": round(ma50_slope, 2),
            "adx": round(latest_idx['adx'], 1),
            "v_score": round(v_score, 4),
            "atr_ratio": round(atr_ratio, 4),
        }
    }

    print(f"B-Score (continuous): {b_score:.4f} ({breadth_pct:.1f}%)")
    print(f"T-Score:              {t_score:.4f} (VNINDEX {verdict['details']['vnindex_vs_ma200']}, ADX: {verdict['details']['adx']})")
    print(f"V-Score (continuous): {v_score:.4f} (ATR Ratio: {atr_ratio:.4f})")
    print(f"Raw Score:            {regime_score_raw:.4f}")
    print(f"EMA Alpha:            {ema_alpha:.4f}  |  Prev Smoothed: {prev_smoothed:.4f}")
    print("-" * 30)
    flag = ">>" if sys.platform == "win32" else "\U0001f6a9"
    print(f"{flag} SMOOTHED REGIME SCORE: {regime_score:.4f} -> {status}")
    print("="*50)

    return verdict

if __name__ == "__main__":
    detect_regime()
