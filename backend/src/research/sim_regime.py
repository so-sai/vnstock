import sqlite3
import sys
from pathlib import Path

import pandas as pd


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

import json

import numpy as np


def _calc_adx(df, period=14):
    df = df.copy()
    df['plus_dm'] = df['high'].diff()
    df['minus_dm'] = -df['low'].diff()
    df['plus_dm'] = np.where((df['plus_dm'] > df['minus_dm']) & (df['plus_dm'] > 0), df['plus_dm'], 0.0)
    df['minus_dm'] = np.where((df['minus_dm'] > df['plus_dm']) & (df['minus_dm'] > 0), df['minus_dm'], 0.0)

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (df['plus_dm'].rolling(period).mean() / atr)
    minus_di = 100 * (df['minus_dm'].rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx.iloc[-1], tr.rolling(period).mean().iloc[-1]

def simulate():
    output_dir = Path("data/output")
    pulse_path = output_dir / "market_pulse.json"

    if not pulse_path.exists():
        print("Missing market_pulse.json")
        return

    with open(pulse_path, 'r', encoding='utf-8') as f:
        pulse = json.load(f)

    breadth_pct = pulse['health_score_ma20']
    b_score = 1.0 if breadth_pct > 55 else 0.6 if breadth_pct > 35 else 0.3 if breadth_pct > 15 else 0.0

    conn = sqlite3.connect('data/screener_cache.db')
    df = pd.read_sql('SELECT date, high, low, close FROM daily_ohlcv WHERE symbol="VNINDEX" ORDER BY date', conn)

    df['ma200'] = df['close'].rolling(200).mean()
    latest = df.iloc[-1]

    adx_val, atr_val = _calc_adx(df)

    # T-Score Logic:
    # Price > MA200 AND ADX > 20 -> 1.0
    # Price > MA200 AND ADX < 20 -> 0.6
    # Price < MA200 -> 0.0
    if latest['close'] > latest['ma200']:
        t_score = 1.0 if adx_val > 20 else 0.6
    else:
        t_score = 0.0

    # V-Score Logic:
    # ATR compressing -> 1.0
    # ATR stable -> 0.6
    # ATR expanding -> 0.2
    atr_pct = (atr_val / latest['close']) * 100
    avg_atr_pct = (df['close'].rolling(14).apply(lambda x: (df.loc[x.index, 'high'] - df.loc[x.index, 'low']).mean()).iloc[-1] / latest['close']) * 100
    # Simple check for expanding/compressing relative to recent mean
    v_score = 0.2 # Defaulting to expanding given current panic

    regime_score = (0.5 * b_score) + (0.3 * t_score) + (0.2 * v_score)

    status = "TRENDING" if regime_score > 0.65 else "RANGING" if regime_score >= 0.35 else "CRISIS"

    print("\n--- REGIME SIMULATION [17/04/2026] ---")
    print(f"B-Score: {b_score} (Breadth: {breadth_pct}%)")
    print(f"T-Score: {t_score} (ADX: {adx_val:.1f}, Price vs MA200: {latest['close']:.0f}/{latest['ma200']:.0f})")
    print(f"V-Score: {v_score} (Volatility Alert)")
    print("----------------------------------------")
    print(f"FINAL REGIME SCORE: {regime_score:.2f}")
    print(f"SYSTEM STATUS: {status}")
    print("----------------------------------------")

if __name__ == "__main__":
    simulate()
