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
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import pandas as pd
import json
import os
import src.config
from src.database.db_core import get_connection

def run_breadth_analysis():
    """
    Phân tích Market Breadth (Độ rộng thị trường):
    """
    print("\n" + "="*50)
    print("📊 ĐANG PHÂN TÍCH MARKET PULSE (ĐỘ RỘNG THỊ TRƯỜNG)...")
    print("="*50)
    
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT symbol, date, close, volume, high 
            FROM daily_ohlcv 
            WHERE date >= '2025-01-01'
        """, conn)

    if df.empty:
        print("⚠️ Database trống hoặc chưa đủ dữ liệu (Cần ít nhất 2 phiên cho 1700 mã).")
        return None

    # --- SENTINEL SAFE PATTERN ---
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.sort_values(['symbol', 'date'])
    
    # 2. Tính toán MA20 & Biểu hiện (Vectorized)
    g = df.groupby('symbol')
    df.loc[:, 'ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df.loc[:, 'daily_change_pct'] = g['close'].transform(lambda x: x.pct_change(fill_method=None))
    df.loc[:, 'avg_vol_20d'] = g['volume'].transform(lambda x: x.rolling(20).mean())
    
    # NEW METER: NH10 (New High 10D)
    # Price > Max(High in last 10 sessions excluding today)
    df.loc[:, 'high_10'] = g['high'].transform(lambda x: x.shift(1).rolling(10).max())
    df.loc[:, 'is_nh10'] = (df['close'] > df['high_10']) & (df['high_10'].notna())

    # 3. Lấy dữ liệu phiên mới nhất
    latest_date = df['date'].max()
    prev_dates = sorted(df['date'].unique())[-3:] # Last 3 sessions for consistency check
    
    latest_df = df[df['date'] == latest_date].copy()

    # 3. Áp dụng Liquidity Filter (Volume > 50,000)
    liquidity_threshold = 50000
    clean_df = latest_df[latest_df['avg_vol_20d'] >= liquidity_threshold].copy()
    
    total_active = len(clean_df)
    if total_active == 0:
        print(f"⚠️ Không có mã nào thỏa mãn bộ lọc thanh khoản (> {liquidity_threshold}).")
        return None

    advancers = len(clean_df[clean_df['daily_change_pct'] > 0])
    decliners = len(clean_df[clean_df['daily_change_pct'] < 0])
    unchanged = total_active - advancers - decliners
    
    # Market Health: % mã nằm trên MA20
    above_ma20 = len(clean_df[clean_df['close'] > clean_df['ma20']])
    health_pct = (above_ma20 / total_active) * 100

    # NH10 Count
    nh10_count = int(clean_df['is_nh10'].sum())
    
    # 3-Day Consistency Check (NH10 > 50)
    consistency_count = 0
    for d in prev_dates:
        d_df = df[(df['date'] == d) & (df['volume'].rolling(20).mean() >= liquidity_threshold)]
        # Re-calc for specific date if needed, but we assumed vectorized is enough
        d_nh10 = int(df[(df['date'] == d) & (df['is_nh10'] == True)]['symbol'].nunique())
        if d_nh10 > 50:
            consistency_count += 1
            
    # 4. Xuất kết quả JSON
    pulse_data = {
        "date": latest_date.strftime('%Y-%m-%d'),
        "total_active": total_active,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "health_score_ma20": round(health_pct, 2),
        "nh10_count": nh10_count,
        "nh10_consistency_3d": consistency_count
    }

    output_dir = os.path.join(src.config.PROJECT_ROOT, "data", "output")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "market_pulse.json"), "w", encoding="utf-8") as f:
        json.dump(pulse_data, f, indent=4, ensure_ascii=False)

    # 5. In báo cáo Console (Giao diện Diamond Shield)
    print(f"🔥 MARKET PULSE | {pulse_data['date']}")
    print("-" * 40)
    print(f"📈 Tang: {advancers} | 📉 Giam: {decliners} | 🟡 TC: {unchanged}")
    print(f"💪 Suc khoe (Price > MA20): {pulse_data['health_score_ma20']}%")
    print(f"🎯 New Highs (NH10): {nh10_count} | Consistency (3D): {consistency_count}/3")
    
    if health_pct > 70:
        print("🚩 Trạng thái: BULLISH (Hưng phấn)")
    elif health_pct < 30:
        print("🚩 Trạng thái: BEARISH (Co cụm)")
    else:
        print("🚩 Trạng thái: NEUTRAL (Phân hóa)")
    
    print("-" * 40)
    print(f"💡 (Dựa trên {total_active} mã có Vol 20d > {liquidity_threshold:,.00f})")

    return pulse_data

if __name__ == "__main__":
    run_breadth_analysis()

