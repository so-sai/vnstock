
import os
import sys
import time
import random
import pandas as pd
import json
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
from vnstock import Trading, Quote, Listing
from src.database.db_core import get_connection, save_data_upsert

def run_daily_update(target_date=None):
    """
    Hệ thống Cập nhật Phiên (Session Updater):
    Tự động đồng bộ OHLCV và Foreign Flow từ Price Board cho toàn bộ thị trường.
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    print(f"\n--- KÍCH HOẠT DAILY UPDATER [{target_date}] ---")

    # 1. Lấy danh sách mã cần cập nhật
    with get_connection() as conn:
        symbols_in_db = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_ohlcv").fetchall()]
    
    # Đảm bảo có cả VNINDEX (VNINDEX không nằm trong price_board thông thường, xử lý riêng)
    symbols_to_update = [s for s in symbols_in_db if s != 'VNINDEX']
    
    print(f"📦 Tổng số mã cần cập nhật: {len(symbols_to_update)}")

    # 2. Cập nhật VNINDEX (Dùng Quote.history)
    print(f"📡 Cập nhật chỉ số VNINDEX...", end=' ', flush=True)
    try:
        q_idx = Quote(symbol='VNINDEX', source='kbs')
        df_idx = q_idx.history(start=target_date, end=target_date)
        if df_idx is not None and not df_idx.empty:
            df_idx = df_idx.rename(columns={'time': 'date'})
            df_idx['symbol'] = 'VNINDEX'
            df_idx['source'] = 'kbs'
            if 'adj_close' not in df_idx.columns: df_idx['adj_close'] = df_idx['close']
            
            # --- FIX: Chuyển Timestamp sang string để tránh lỗi SQLite binding ---
            if pd.api.types.is_datetime64_any_dtype(df_idx['date']):
                df_idx['date'] = df_idx['date'].dt.strftime('%Y-%m-%d')
            
            df_idx = df_idx[['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']]
            with get_connection() as conn:
                save_data_upsert('daily_ohlcv', df_idx, conn)
            print("✅")
        else:
            print("⚠️ Trống")
    except Exception as e:
        print(f"❌ Lỗi Chỉ số: {e}")

    # 3. Batch Update toàn bộ thị trường (Dùng Price Board)
    batch_size = 50
    t = Trading(source='kbs')
    
    # Chuẩn hóa target_date string
    target_date_str = str(target_date)

    for i in range(0, len(symbols_to_update), batch_size):
        batch = symbols_to_update[i:i+batch_size]
        print(f"🚀 Batch {i//batch_size + 1}/{len(symbols_to_update)//batch_size + 1} ({len(batch)} mã)...", end=' ', flush=True)
        
        try:
            df_pb = t.price_board(batch)
            if df_pb is not None and not df_pb.empty:
                # --- Xử lý OHLCV ---
                df_save = df_pb.copy()
                df_save['date'] = target_date_str
                
                # Check required columns
                required_cols = ['foreign_buy_volume', 'foreign_sell_volume', 'close_price']
                missing_cols = [c for c in required_cols if c not in df_save.columns]
                if missing_cols:
                    print(f"⚠️ Thiếu cột {missing_cols}", end=' ', flush=True)
                    # Try to fill if possible, or skip
                    for mc in missing_cols: df_save[mc] = 0
                
                df_save = df_save.rename(columns={
                    'open_price': 'open',
                    'high_price': 'high',
                    'low_price': 'low',
                    'close_price': 'close',
                    'total_trades': 'volume',
                    'foreign_buy_volume': 'foreign_vol'
                })
                df_save['adj_close'] = df_save['close']
                df_save['source'] = 'kbs'
                
                # Foreign Flow Calculation
                df_save['net_vol'] = df_save['foreign_vol'] - df_save['foreign_sell_volume']
                df_save['net_value'] = (df_save['net_vol'] * df_save['close']) / 1_000_000_000
                
                cols_ohlcv = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
                cols_foreign = ['symbol', 'date', 'foreign_vol', 'net_vol', 'net_value']
                
                # Persistence
                with get_connection() as conn:
                    save_data_upsert('daily_ohlcv', df_save[cols_ohlcv], conn)
                    save_data_upsert('market_foreign_history', df_save[cols_foreign], conn)
                
                print("✅ Done", flush=True)
            else:
                print("⚠️ No Data", flush=True)
        except Exception as e:
            print(f"❌ Error: {e}", flush=True)
        
        # Elite Armor Throttling
        time.sleep(random.uniform(0.5, 1.2))

    print("\n--- HOÀN TẤT ĐỒNG BỘ PHIÊN ---")

if __name__ == "__main__":
    run_daily_update()
