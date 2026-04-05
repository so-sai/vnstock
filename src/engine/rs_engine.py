import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản (seed_data.py, .kit)
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "seed_data.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
import numpy as np
import json
import src.config
from src.database.db_core import get_connection

def calculate_rs_score():
    """
    RS Ranking Engine Alpha V2 (v1.6 - Momentum Flavor)
    Công thức: 40% (3M) + 20% (6M) + 20% (9M) + 20% (12M)
    Bộ lọc: Volume 20D > 100k & Value > 2 Tỷ
    """
    print("\n" + "="*50)
    print("📡 ĐANG QUÉT RADAR RS (RELATIVE STRENGTH)...")
    print("="*50)

    # 1. Truy vấn dữ liệu lịch sử từ Vault
    with get_connection() as conn:
        query = """
            SELECT symbol, date, adj_close as close, volume
            FROM daily_ohlcv
            ORDER BY symbol, date ASC
        """
        df = pd.read_sql(query, conn)

    if df.empty:
        print("⚠️ Vault trống rỗng. Hãy hoàn tất Seeding trước.")
        return None

    # --- SENTINEL SAFE PATTERN ---
    df = df.copy()
    df.loc[:, 'date'] = pd.to_datetime(df['date'])
    
    # 2. VECTORIZED MOMENTUM CALCULATION
    stats = []
    # Groupby loop is necessary for complex momentum logic, but we make entries safe
    for symbol, group in df.groupby('symbol'):
        if len(group) < 20: continue 
        
        group = group.copy() # Cắt đứt liên kết slice
        latest_price = group['close'].iloc[-1]
        avg_vol_20d = group['volume'].tail(20).mean()
        latest_vol = group['volume'].iloc[-1]
        
        rvol = latest_vol / avg_vol_20d if avg_vol_20d > 0 else 0
        avg_value_20d = (group['close'].tail(20) * group['volume'].tail(20)).mean() / 1_000_000_000
        
        def get_return(days):
            if len(group) >= days:
                old_price = group['close'].iloc[-days]
                return (latest_price - old_price) / old_price if old_price > 0 else 0
            return 0

        r3m = get_return(63)
        r6m = get_return(126)
        r9m = get_return(189)
        r1y = get_return(252)

        raw_score = (r3m * 0.4) + (r6m * 0.2) + (r9m * 0.2) + (r1y * 0.2)
        
        stats.append({
            'symbol': symbol,
            'rs_raw': raw_score,
            'avg_vol_20d': avg_vol_20d,
            'rvol': round(rvol, 2),
            'avg_value_20d': avg_value_20d,
            'price': latest_price,
            'change_1y': r1y * 100
        })

    rs_df = pd.DataFrame(stats)
    if rs_df.empty: return None

    # --- SENTINEL SAFE PATTERN ---
    rs_df = rs_df.copy()

    # 3. LIQUIDITY SHIELD (Bộ lọc kỷ cương)
    filtered_df = rs_df[(rs_df['avg_vol_20d'] >= 100000) | (rs_df['avg_value_20d'] >= 2)].copy()

    if filtered_df.empty:
        print("⚠️ Không có mã nào thỏa mãn bộ lọc thanh khoản (100k Vol / 2 Tỷ Value).")
        return None

    # 4. PERCENTILE RANKING (1-99)
    # Dùng loc để gán cột mới an toàn
    filtered_df.loc[:, 'rs_rating'] = filtered_df['rs_raw'].rank(pct=True) * 99
    filtered_df.loc[:, 'rs_rating'] = filtered_df['rs_rating'].round(0).astype(int)

    # 5. Xuất bản kết quả
    result = filtered_df.sort_values('rs_rating', ascending=False).copy()
    
    output_path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    result.to_json(output_path, orient='records', force_ascii=False, indent=4)
    
    print(f"🚀 [Radar] Đã xếp hạng {len(result)} mã đủ thanh khoản.")
    print(f"🏆 TOP 10 SIÊU CỔ PHIẾU (RS RATING):")
    print("-" * 50)
    top_10 = result.head(10)
    for _, row in top_10.iterrows():
        print(f"⭐ {row['symbol']:<6} | RS: {row['rs_rating']:>2} | Giá: {row['price']:>8,.0f} | Vol 20D: {row['avg_vol_20d']/1000:>6.1f}K")
    
    return result

def load_rs_data():
    """
    Nạp dữ liệu RS từ Vault JSON. Trả về Dictionary {symbol: data}
    """
    output_path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    if not os.path.exists(output_path):
        return {}
    
    with open(output_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {item['symbol']: item for item in data}

def compute_rs_matrix(df_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Tính toán Ma trận RS (Symbol x Date) sử dụng Vectorization (Pandas).
    Phục vụ Backtest v2.2 (Elite VN-Tuned: 40-30-20-10).
    """
    # 0. BẮT BUỘC: Ép kiểu số & Giữ lại các cột tối thiểu
    df_ohlcv = df_ohlcv.copy()
    # V4.6 SENTINEL: Nếu adj_close không có, fallback về close
    if 'adj_close' not in df_ohlcv.columns or df_ohlcv['adj_close'].isna().all():
        df_ohlcv['adj_close'] = df_ohlcv.get('close', pd.Series(dtype=float))
    # Loại bỏ các hàng trùng lặp hoặc rác
    df_ohlcv = df_ohlcv.dropna(subset=['symbol', 'date', 'adj_close'])
    df_ohlcv['adj_close'] = pd.to_numeric(df_ohlcv['adj_close'], errors='coerce')
    # Giữ lại các cột chuẩn để tránh lỗi pivot các cột rác
    df_ohlcv = df_ohlcv[['symbol', 'date', 'adj_close']]
    
    # 1. Pivot dữ liệu sang Ma trận (Index: Date, Columns: Symbol)
    pivot_df = df_ohlcv.pivot_table(index='date', columns='symbol', values='adj_close', aggfunc='max')
    
    # 2. Tính toán hiệu suất các khung thời gian (Momentum) - ffill trước để tránh FutureWarning
    pivot_filled = pivot_df.ffill()
    perf_1y = pivot_filled.pct_change(252)
    perf_6m = pivot_filled.pct_change(126)
    perf_3m = pivot_filled.pct_change(63)
    perf_1m = pivot_filled.pct_change(21)
    
    # 3. Tính điểm Weighted RS (v2.2: 1M=40%, 3M=30%, 6M=20%, 1Y=10%)
    rs_score = (perf_1m * 0.4 + perf_3m * 0.3 + perf_6m * 0.2 + perf_1y * 0.1)
    
    # 4. Xếp hạng Percentile Rank theo hàng (từng ngày)
    rs_matrix = rs_score.rank(pct=True, axis=1) * 100
    
    return rs_matrix

if __name__ == "__main__":
    calculate_rs_score()
