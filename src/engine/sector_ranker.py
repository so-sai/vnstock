import sys
import os
from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config
from src.database.db_core import get_connection

def calculate_sector_stats():
    """
    Alpha V4.4 - Sector Surveillance: Phan tich luan chuyen dong tien nganh (V4.4).
    """
    print("\n" + "="*65)
    print("DEBUG: [ICB SECTOR SURVEILLANCE] Analyzing Industry Rotations")
    print("="*65)

    # 1. Tải dữ liệu từ Vault
    db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
    if not os.path.exists(db_path):
        print("❌ Lỗi: CSDL Vault không tồn tại.")
        return

    with get_connection() as conn:
        # Lấy OHLC và Industry Mapping
        query = """
            SELECT o.symbol, o.date, o.close, o.volume, i.icb_name2 as industry
            FROM daily_ohlcv o
            JOIN symbol_industry i ON o.symbol = i.symbol
            WHERE o.symbol NOT IN ('VNINDEX', 'VN30')
            ORDER BY o.symbol, o.date ASC
        """
        df_ohlcv = pd.read_sql(query, conn)
        print(f"DEBUG: Joined Rows Count: {len(df_ohlcv)}")
        if not df_ohlcv.empty:
            print(f"DEBUG: First 5 Symbols Joined: {df_ohlcv['symbol'].unique()[:5]}")

    if df_ohlcv.empty:
        # Diagnostic: Check raw counts separately
        print("DEBUG: Checking raw table counts...")
        with get_connection() as conn:
            ohlcv_count = conn.execute("SELECT count(*) FROM daily_ohlcv").fetchone()[0]
            ind_count = conn.execute("SELECT count(*) FROM symbol_industry").fetchone()[0]
            print(f"DEBUG: raw daily_ohlcv: {ohlcv_count}")
            print(f"DEBUG: raw symbol_industry: {ind_count}")
        print("⚠️ Vault trống hoặc chưa có mapping ngành.")
        return

    # 2. Xử lý Ma trận RS (Vectorization)
    df_ohlcv['date'] = pd.to_datetime(df_ohlcv['date'], format='ISO8601', errors='coerce')
    pivot_price = df_ohlcv.pivot(index='date', columns='symbol', values='close').ffill()
    pivot_vol = df_ohlcv.pivot(index='date', columns='symbol', values='volume').ffill()
    
    # Tinh Liquidity Value (Price * Volume * 1000)
    pivot_value = pivot_price * pivot_vol * 1000
    avg_value_20d = pivot_value.rolling(20).mean().iloc[-1]   # Trung binh 20 phien
    value_latest = pivot_value.iloc[-1]                        # Gia tri phien gan nhat
    
    # Tính Momentum RS (1M=50%, 3M=50% cho Sector focus)
    perf_1m = pivot_price.pct_change(21).iloc[-1]
    perf_3m = pivot_price.pct_change(63).iloc[-1]
    
    # Xếp hạng Percentile Rank
    rs_1m = perf_1m.rank(pct=True).fillna(0) * 100
    rs_3m = perf_3m.rank(pct=True).fillna(0) * 100
    rs_combined = (rs_1m + rs_3m) / 2
    
    # 3. Phân tích theo nhóm ngành
    sector_data = []
    for sector, members in df_ohlcv.groupby('industry'):
        symbols = members['symbol'].unique()
        
        # Chỉ tính trên các mã có dữ liệu RS
        valid_symbols = [s for s in symbols if s in rs_combined.index]
        if not valid_symbols: continue
        
        avg_rs_1m = rs_1m[valid_symbols].mean()
        avg_rs_3m = rs_3m[valid_symbols].mean()
        
        # --- ALPHA V4.5: DIFFUSION INDEX ($DI$) ---
        # Đếm số mã có RS > 60 và giá nằm trên MA20
        sector_prices = pivot_price[valid_symbols]
        sector_ma20 = sector_prices.rolling(20).mean()
        
        latest_prices = sector_prices.iloc[-1]
        latest_ma20 = sector_ma20.iloc[-1]
        latest_rs = rs_combined[valid_symbols]
        
        # --- V4.6 IRON GATE: Diffusion Index chi tinh tren cac ma vuot cua 5B ---
        # Loc ra cac ma vuot ca 2 cua (TB 20 phien >= 5B VA phien hom nay >= 5B)
        iron_gate_mask = (
            (avg_value_20d[valid_symbols] >= 5_000_000_000) &
            (value_latest[valid_symbols] >= 5_000_000_000)
        )
        diamond_symbols = [s for s in valid_symbols if iron_gate_mask.get(s, False)]

        # Diffusion Index chi tinh tren Diamond symbols
        if diamond_symbols:
            diamond_prices = sector_prices[diamond_symbols]
            diamond_ma20  = sector_ma20[diamond_symbols]
            diamond_latest_prices = diamond_prices.iloc[-1]
            diamond_latest_ma20   = diamond_ma20.iloc[-1]
            diamond_rs = rs_combined[diamond_symbols]
            bullish_members = (diamond_rs > 60) & (diamond_latest_prices > diamond_latest_ma20)
            diffusion_index = (bullish_members.sum() / len(diamond_symbols)) * 100
        else:
            bullish_members  = pd.Series(dtype=bool)
            diffusion_index  = 0.0

        # Dem so Diamond (ca 2 cua >= 5B)
        liquidity_depth = len(diamond_symbols)
        
        sector_data.append({
            'Sector': sector,
            'RS 1M': avg_rs_1m,
            'RS 3M': avg_rs_3m,
            'Combined': (avg_rs_1m + avg_rs_3m) / 2,
            'Diffusion (%)': diffusion_index,
            'Diamonds (>5B)': int(liquidity_depth),
            'Total': len(valid_symbols)
        })

    if not sector_data:
        print("⚠️ Không có dữ liệu ngành sau khi chuẩn hóa RS.")
        return

    df_sectors = pd.DataFrame(sector_data).sort_values(by='Combined', ascending=False)

    # 4. In bảng kết quả
    print(f"{'NHOM NGANH (ICB)':<25} | {'RS 1M':>6} | {'RS 3M':>6} | {'SCORE':>6} | {'DIFF.':>6} | {'DIAMONDS (>5B)':>14}")
    print("-" * 80)
    for _, row in df_sectors.iterrows():
        # Đánh dấu "Hội tụ" nếu DI >= 40%
        marker = "🟢" if row['Diffusion (%)'] >= 40 else "🟡"
        # Xóa marker để tránh Encoding crash trên Windows, dùng dấu star
        marker = "(*)" if row['Diffusion (%)'] >= 40 else "   "
        
        print(f"{row['Sector']:<25} | {row['RS 1M']:>6.1f} | {row['RS 3M']:>6.1f} | {row['Combined']:>6.1f} | {row['Diffusion (%)']:>5.0f}% | {row['Diamonds (>5B)']:>3}/{row['Total']:<3} {marker}")
    print("=" * 80)
    print("DEBUG: (*) Hoi tu (Diffusion Index >= 40%). Score: (RS 1M + RS 3M) / 2.")

if __name__ == "__main__":
    calculate_sector_stats()

