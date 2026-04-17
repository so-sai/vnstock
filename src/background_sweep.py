import os
import sys
import time
import random
import pandas as pd
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
from vnstock import Listing, Quote
from src.database.db_core import get_connection

def run_background_sweep():
    print("\n" + ">>> " * 10)
    print("      KICH HOAT BACKGROUND SWEEP: GIAI DOAN 2      ")
    print(">>> " * 10)

    # 1. Xác định tập mã còn thiếu
    l = Listing()
    all_symbols_df = l.all_symbols()
    all_symbols = set(all_symbols_df['symbol'].tolist())

    with get_connection() as conn:
        ohlcv_symbols = set([r[0] for r in conn.execute('SELECT DISTINCT symbol FROM daily_ohlcv').fetchall()])

    missing_symbols = sorted(list(all_symbols - ohlcv_symbols))
    print(f"📊 Tổng số mã thị trường: {len(all_symbols)}")
    print(f"📊 Đã có dữ liệu (Diamond): {len(ohlcv_symbols)}")
    print(f"🔍 Cần quét bổ sung: {len(missing_symbols)} mã")

    if not missing_symbols:
        print("✅ Toàn bộ thị trường đã được quét. Không cần chạy thêm.")
        return

    # 2. Triển khai ELITE ARMOR: Throttling & Batching
    batch_size = 50
    success_count = 0
    start_time = time.time()

    for i in range(0, len(missing_symbols), batch_size):
        batch = missing_symbols[i:i+batch_size]
        print(f"\n📦 Đang xử lý Batch {i//batch_size + 1}/{len(missing_symbols)//batch_size + 1} ({len(batch)} mã)...")

        for symbol in batch:
            try:
                # Bỏ qua các chỉ số
                if symbol in ['VNINDEX', 'VN30', 'HNXINDEX', 'UPINDEX']: continue
                
                print(f"📡 Fetching {symbol}...", end=' ', flush=True)
                q = Quote(symbol=symbol, source='kbs')
                df_hist = q.history(length='135', interval='1D') # Get 135 to ensure 125 (6M) after drops
                
                if df_hist is not None and not df_hist.empty:
                    df_hist = df_hist.rename(columns={'time': 'date'})
                    df_hist['symbol'] = symbol
                    df_hist['source'] = 'kbs'
                    if 'adj_close' not in df_hist.columns:
                        df_hist['adj_close'] = df_hist['close']
                    
                    # Mapping columns for DB
                    cols = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
                    df_hist = df_hist[cols]

                    # Persistence
                    with get_connection() as conn:
                        df_hist.to_sql('daily_ohlcv', conn, if_exists='append', index=False)
                    
                    success_count += 1
                    print("✅", flush=True)
                else:
                    print("⚠️ Trống", flush=True)

                # Throttling để tránh Ban IP (Elite Armor)
                time.sleep(random.uniform(0.3, 0.8))

            except Exception as e:
                print(f"❌ Lỗi: {e}")
                if "429" in str(e) or "500" in str(e):
                    print("🛑 API Limit/Error detected! Backing off 60s...")
                    time.sleep(60)

        # Batch Cooldown
        print(f"⏱️ Đã xong batch. Nghỉ 5s để giải phóng tài nguyên...")
        time.sleep(5)

    total_runtime = time.time() - start_time
    print("\n" + "="*50)
    print(f"🏁 HOÀN TẤT BACKGROUND SWEEP")
    print(f"📊 Số mã đã nạp thành công: {success_count}")
    print(f"⏱️ Tổng thời gian: {total_runtime/60:.1f} phút")
    print("="*50)

if __name__ == "__main__":
    run_background_sweep()
