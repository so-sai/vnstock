import os
import sys
from pathlib import Path


def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
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
import pandas as pd

from src.database.db_core import get_connection


def run_sector_heatmap():
    """
    Phân tích Sector Heatmap (Bản đồ nhiệt dòng tiền):
    - Tính % biến động trung bình của từng nhóm ngành (ICB Level 3).
    - Tính tổng dòng tiền (Money Flow) theo Tỷ đồng.
    - Chống lỗi dữ liệu < 2 phiên.
    """
    print("\n" + "="*50)
    print("🎨 ĐANG VẼ BẢN ĐỒ NHIỆT DÒNG TIỀN (SECTOR HEATMAP)...")
    print("="*50)

    with get_connection() as conn:
        query = """
            SELECT 
                p.symbol, p.date, p.close, p.volume,
                i.icb_name3 as sector
            FROM daily_ohlcv p
            JOIN symbol_industry i ON p.symbol = i.symbol
            WHERE p.date >= '2025-01-01'
        """
        df = pd.read_sql(query, conn)

    if df.empty:
        print("⚠️ Dữ liệu ngành hoặc giá trống. Hãy chạy seeding industry/stock trước.")
        return None

    # --- SENTINEL GUARD: Kiểm tra số phiên tối thiểu ---
    unique_dates = df['date'].nunique()
    if unique_dates < 2:
        print(f"⚠️ [Heatmap] Chỉ có {unique_dates} phiên dữ liệu. Cần tối thiểu 2 phiên để tính biến động.")
        return None

    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.sort_values(['symbol', 'date'])

    # 1. Tính toán Money Flow & Biến động cho từng mã (VECTORIZED)
    df = df.copy()
    df.loc[:, 'money_flow'] = (df['close'] * df['volume']) / 1_000_000_000
    df.loc[:, 'change_pct'] = df.groupby('symbol')['close'].transform(lambda x: x.pct_change() * 100)
    df.loc[:, 'avg_vol_20d'] = df.groupby('symbol')['volume'].transform(lambda x: x.rolling(20).mean())

    # 2. Lấy Snapshot phiên mới nhất & Áp dụng Liquidity Filter
    latest_date = df['date'].max()
    active_df = df[(df['date'] == latest_date) & (df['avg_vol_20d'] >= 50000)].copy()

    if active_df.empty:
        print("⚠️ Không có mã nào đủ thanh khoản để vẽ Heatmap.")
        return None

    # 3. Aggregation theo Sector (ICB Level 3)
    sector_stats = active_df.groupby('sector').agg(
        avg_change=('change_pct', 'mean'),
        total_money_flow=('money_flow', 'sum'),
        advancers=('change_pct', lambda x: (x > 0).sum()),
        decliners=('change_pct', lambda x: (x < 0).sum()),
        symbol_count=('symbol', 'count')
    ).reset_index()

    # 4. Định dạng và Xuất kết quả
    sector_stats = sector_stats.sort_values('avg_change', ascending=False)

    top_5_up = sector_stats.head(5)
    top_5_money = sector_stats.sort_values('total_money_flow', ascending=False).head(5)

    print("🔥 TOP 5 NGÀNH DẪN DẮT (BIẾN ĐỘNG %)")
    print("-" * 45)
    for _, row in top_5_up.iterrows():
        print(f"🚀 {row['sector'][:20]:<20} | {row['avg_change']:>6.2f}% | {row['total_money_flow']:>8.1f} Tỷ")

    print("\n💰 TOP 5 NGÀNH HÚT TIỀN (MONEY FLOW)")
    print("-" * 45)
    for _, row in top_5_money.iterrows():
        print(f"🔥 {row['sector'][:20]:<20} | {row['total_money_flow']:>8.1f} Tỷ | {row['avg_change']:>6.2f}%")

    print("-" * 45)

    output_dir = "data/output"
    os.makedirs(output_dir, exist_ok=True)
    sector_stats.to_json(os.path.join(output_dir, "sector_heatmap.json"),
                         orient='records', force_ascii=False, indent=4)

    return sector_stats

if __name__ == "__main__":
    run_sector_heatmap()

