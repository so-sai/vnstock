import sys
from pathlib import Path
import os
import time
import random
import pandas as pd
import json

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
from vnstock import Quote, Listing
from src.database.db_core import get_connection
from src.engine.breadth_engine import run_breadth_analysis

def get_diamond_candidates():
    """Lấy danh sách top 548 mã dựa trên thanh khoản lịch sử để làm cụm ưu tiên (Priority Cluster)."""
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT symbol, AVG(close * volume * 1000) as avg_val 
            FROM daily_ohlcv 
            GROUP BY symbol 
            ORDER BY avg_val DESC 
            LIMIT 548
        """, conn)
    return df['symbol'].tolist()

def fetch_and_save(symbol_list, batch_name="Priority"):
    print(f"🚀 Bắt đầu Seeding {batch_name} Cluster ({len(symbol_list)} mã)...")
    success_count = 0
    
    # ELITE ARMOR: Batching
    batch_size = 50
    for i in range(0, len(symbol_list), batch_size):
        batch = symbol_list[i:i+batch_size]
        print(f"📦 Processing Batch {i//batch_size + 1} ({len(batch)} mã)...")
        
        for symbol in batch:
            try:
                # Tránh các chỉ số
                if symbol in ['VNINDEX', 'VN30']: continue
                
                q = Quote(symbol=symbol, source='kbs')
                # Fetch 6 months (approx 125 sessions)
                df_hist = q.history(length='130', interval='1D')
                if df_hist is not None and not df_hist.empty:
                    # RENAME AND MAP COLUMNS FOR DB
                    df_hist = df_hist.rename(columns={'time': 'date'})
                    df_hist['symbol'] = symbol
                    df_hist['source'] = 'kbs'
                    if 'adj_close' not in df_hist.columns:
                        df_hist['adj_close'] = df_hist['close']
                    
                    # Ensure column order matches DB
                    cols = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
                    df_hist = df_hist[cols]

                    with get_connection() as conn:
                        df_hist.to_sql('daily_ohlcv', conn, if_exists='append', index=False)
                    success_count += 1
                
                # ELITE ARMOR: Throttling
                time.sleep(random.uniform(0.5, 1.5))
            except Exception as e:
                print(f"⚠️ Lỗi fetch {symbol}: {e}")
                # ELITE ARMOR: Negative Caching/Backoff
                if "500" in str(e) or "429" in str(e):
                    print("🛑 API Warning! Sleeping for 60s...")
                    time.sleep(60)
        
        print(f"✅ Đã xong batch. Cooldown 10s...")
        time.sleep(10)

    print(f"🏁 Đã nạp xong {success_count} mã.")

def run_v1_screening():
    print("\n" + "="*50)
    print("🎯 SCREENER V1.0.4 GOLD: EXECUTING VERDICT")
    print("="*50)
    
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT * FROM daily_ohlcv 
            WHERE date >= '2025-01-01'
        """, conn)
    
    if df.empty: return
    
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.sort_values(['symbol', 'date'])
    
    # Logic V1 Filters
    g = df.groupby('symbol')
    df['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df['ma50'] = g['close'].transform(lambda x: x.rolling(50).mean())
    df['high_20'] = g['high'].transform(lambda x: x.shift(1).rolling(20).max())
    df['vol_ma20'] = g['volume'].transform(lambda x: x.rolling(20).mean())
    
    # Absolute Momentum 6M (approx 125 sessions)
    df['return_6m'] = g['close'].transform(lambda x: (x / x.shift(125)) - 1)
    
    latest_date = df['date'].max()
    latest = df[df['date'] == latest_date].copy()
    
    # 1. Momentum Gate: Return 6M > 0
    # 2. Trend Gate: Price > MA20 AND Price > MA50
    # 3. Screener V1: Breakout & Vol Spike
    latest['signal'] = (
        (latest['return_6m'] > 0) &
        (latest['close'] > latest['ma20']) &
        (latest['close'] > latest['ma50']) &
        (latest['close'] > latest['high_20']) &
        (latest['volume'] > 1.5 * latest['vol_ma20'])
    )
    
    winners = latest[latest['signal'] == True].copy()
    # Output Liquidity Filter: Today Value > 2bn VND
    winners['value_today'] = winners['close'] * winners['volume'] * 1000
    winners = winners[winners['value_today'] > 2000000000].copy()
    
    winners = winners.sort_values('return_6m', ascending=False)
    
    print(f"\n🏆 TOP SIÊU BINH SĨ (16/04/2026)")
    print("-" * 50)
    if winners.empty:
        print("Không tìm thấy mã nào thỏa mãn V1.0 hôm nay.")
    else:
        for _, row in winners.head(5).iterrows():
            print(f"💎 {row['symbol']} | Giá: {row['close']:,.0f} | Momentum 6M: {row['return_6m']*100:.2f}% | Vol Spike: {row['volume']/row['vol_ma20']:.1f}x")
    print("-" * 50 + "\n")

if __name__ == "__main__":
    symbols = get_diamond_candidates()
    # Phase 1: Diamonds
    fetch_and_save(symbols[:50], "Phase 1 - Top 50 Diamonds") # Just 50 for immediate result
    run_v1_screening()
