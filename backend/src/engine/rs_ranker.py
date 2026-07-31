import os
import sys
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản (screener.py, .kit)
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json

import pandas as pd

import src.config
from src.database.db_core import get_connection


def calculate_rs_score():
    """
    RS Ranking Engine Alpha V2 (v1.6 - Momentum Flavor)
    Công thức: 40% (3M) + 20% (6M) + 20% (9M) + 20% (12M)
    Thứ tự: Tính RS toàn thị trường → Xếp hạng Percentile (1-99) → Lọc thanh khoản đầu ra
    """
    print("\n" + "="*50)
    print("📡 ĐANG QUÉT RADAR RS (RELATIVE STRENGTH)...")
    print("="*50)

    # ── [TẦNG 0] Lọc chất lượng dữ liệu ──────────────────
    from src.engine.data_quality import loc_bo_bang_tin_cay
    reliable = set(loc_bo_bang_tin_cay())
    print(f"  📊 Đã lọc dữ liệu: {len(reliable)} mã đạt chuẩn (≥200 phiên)")

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

    # LAW-DATA-001: Loại bỏ mã chỉ số rổ khỏi mảng RS
    index_symbols = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPINDEX"}
    before = df['symbol'].nunique()
    df = df[~df['symbol'].isin(index_symbols)].copy()
    removed = before - df['symbol'].nunique()
    if removed:
        print(f"  🧹 Đã loại {removed} mã chỉ số khỏi mảng RS (LAW-DATA-001)")

    # --- SENTINEL SAFE PATTERN ---
    df = df.copy()
    df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed')

    # Lọc chỉ giữ các mã đủ dữ liệu tin cậy
    before_count = df['symbol'].nunique()
    df = df[df['symbol'].isin(reliable)].copy()
    after_count = df['symbol'].nunique()
    if before_count != after_count:
        print(f"  ⚠ Tạm hoãn xếp hạng {before_count - after_count} mã — thiếu dữ liệu lịch sử.")

    # Chuẩn hóa đơn vị giá (VND → nghìn đồng) cho price và close
    m = df['close'] > 500
    df.loc[m, 'close'] = df.loc[m, 'close'] / 1000.0

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
        avg_value_20d = (group['close'].tail(20) * group['volume'].tail(20)).mean() / 1_000_000

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

    # 3. PERCENTILE RANKING (1-99) — trên TOÀN BỘ thị trường
    ranked_df = rs_df.dropna(subset=['rs_raw']).copy()
    ranked_df.loc[:, 'rs_rating'] = ranked_df['rs_raw'].rank(pct=True) * 99
    ranked_df.loc[:, 'rs_rating'] = ranked_df['rs_rating'].fillna(0).round(0).astype(int)

    # 4. LIQUIDITY SHIELD — Màng lọc định chế: giá trị GD bình quân 20 phiên >= 5 tỷ VND
    filtered_df = ranked_df[ranked_df['avg_value_20d'] >= 5].copy()

    if filtered_df.empty:
        print("⚠️ Không có mã nào thỏa mãn bộ lọc thanh khoản (>= 5 tỷ VND/phiên).")
        return None

    # 5. Xuất bản kết quả
    result = filtered_df.sort_values('rs_rating', ascending=False).copy()

    output_path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    result.to_json(output_path, orient='records', force_ascii=False, indent=4)

    print(f"🚀 [Radar] Đã xếp hạng {len(result)} mã đủ thanh khoản.")
    print("🏆 TOP 10 SIÊU CỔ PHIẾU (RS RATING):")
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

def compute_rs_components(df_ohlcv: pd.DataFrame) -> dict:
    """
    Tính toán các thành phần Momentum (1M, 3M, 6M, 1Y) dưới dạng Ma trận (Symbol x Date).
    Phục vụ cho việc gán trọng số động (Adaptive Weighting) trong Backtest v1.0.1.
    """
    df_ohlcv = df_ohlcv.copy()
    if 'adj_close' not in df_ohlcv.columns or df_ohlcv['adj_close'].isna().all():
        df_ohlcv['adj_close'] = df_ohlcv.get('close', pd.Series(dtype=float))

    df_ohlcv = df_ohlcv.dropna(subset=['symbol', 'date', 'adj_close'])
    df_ohlcv['adj_close'] = pd.to_numeric(df_ohlcv['adj_close'], errors='coerce')
    df_ohlcv = df_ohlcv[['symbol', 'date', 'adj_close']]

    pivot_df = df_ohlcv.pivot_table(index='date', columns='symbol', values='adj_close', aggfunc='max')
    pivot_filled = pivot_df.ffill()

    return {
        '1m': pivot_filled.pct_change(21),
        '3m': pivot_filled.pct_change(63),
        '6m': pivot_filled.pct_change(126),
        '1y': pivot_filled.pct_change(252)
    }

def compute_rs_matrix(df_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Hàm Legacy: Phục vụ tương thích ngược với V1.0 (Fixed weights: 40-30-20-10).
    """
    comps = compute_rs_components(df_ohlcv)
    rs_score = (comps['1m'] * 0.4 + comps['3m'] * 0.3 + comps['6m'] * 0.2 + comps['1y'] * 0.1)
    return rs_score.rank(pct=True, axis=1) * 100

if __name__ == "__main__":
    calculate_rs_score()

