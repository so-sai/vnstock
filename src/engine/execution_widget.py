import sys
import os
from pathlib import Path
import sqlite3
import pandas as pd
from datetime import datetime

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản (screener.py, .kit)
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

def generate_daily_orders():
    """
    Alpha V4.6 - Execution Widget: Xuat lenh tac chien hang ngay.
    Iron Gate: Loc 2 lop (TB 20 phien >= 5B + Phien hom nay >= 5B).
    Dung de bridge tu Terminal sang App giao dich (TCBS/VNDirect).
    """
    print("\n" + "="*65)
    print("🚀  TRẠM LỆNH TÁC CHIẾN ALPHA V4.6 (THE SNIPER — IRON GATE)")
    print("="*65)

    # 1. Tai du lieu moi nhat
    with get_connection() as conn:
        df_ohlcv = pd.read_sql("""
            SELECT o.symbol, o.date, o.close, o.volume, i.icb_name2 as industry
            FROM daily_ohlcv o
            JOIN symbol_industry i ON o.symbol = i.symbol
            WHERE o.symbol NOT IN ('VNINDEX', 'VN30')
            ORDER BY o.date ASC
        """, conn)
        
        df_macro = pd.read_sql("SELECT * FROM macro_history ORDER BY date DESC LIMIT 40", conn)

    if df_ohlcv.empty:
        print("❌ Vault trong. Hay chay seeding truoc.")
        return

    # 2. Tinh toan RS va Diffusion (Logic V4.6)
    df_ohlcv['date'] = pd.to_datetime(df_ohlcv['date'], format='ISO8601', errors='coerce')
    pivot_price = df_ohlcv.pivot_table(index='date', columns='symbol', values='close', aggfunc='last').ffill()
    pivot_vol = df_ohlcv.pivot_table(index='date', columns='symbol', values='volume', aggfunc='last').ffill()

    # --- V4.6 IRON GATE: Gia tri giao dich thuc te (Price * Volume * 1000) ---
    pivot_value = pivot_price * pivot_vol * 1000
    avg_value_20d = pivot_value.rolling(20).mean().iloc[-1]   # Trung binh 20 phien
    value_today = pivot_value.iloc[-1]                         # Gia tri phien hien tai
    
    # --- V4.6: CẢNH BÁO XU HƯỚNG (MA20 TREND FILTER) ---
    pivot_ma20 = pivot_price.rolling(20).mean().iloc[-1]
    
    perf_1m = pivot_price.pct_change(21).iloc[-1]
    perf_3m = pivot_price.pct_change(63).iloc[-1]
    rs_combined = (perf_1m.rank(pct=True).fillna(0)*100 + perf_3m.rank(pct=True).fillna(0)*100) / 2
    
    # Check Macro level
    is_macro_safe = True
    allow_buy = True
    
    if not df_macro.empty:
        df_macro_p = df_macro.pivot_table(index='date', columns='variable', values='value', aggfunc='last').ffill()
        latest_macro = df_macro_p.iloc[-1]
        ma20_macro = df_macro_p.rolling(20).mean().iloc[-1]
        
        if (latest_macro.get('DXY', 0) > ma20_macro.get('DXY', 0) * 1.01) or \
           (latest_macro.get('USD_VND', 0) > ma20_macro.get('USD_VND', 0) * 1.005):
            is_macro_safe = False
            
        if (latest_macro.get('BTC', 0) < ma20_macro.get('BTC', 0) * 0.90):
            allow_buy = False

    # 3. Xac dinh Target NAV
    target_nav = "100% (FULL)"
    if not is_macro_safe: target_nav = "0% (CASH)"
    elif not allow_buy: target_nav = "30% (DEFENSIVE)"
    
    # 4. Xuat file lenh
    order_file = os.path.join(PROJECT_ROOT, "orders_today.txt")
    
    with open(order_file, "w", encoding="utf-8") as f:
        f.write(f"🛡️ ALPHA V4.6 (THE SNIPER) - CHIẾN LỆNH NGÀY {datetime.now().strftime('%d/%m/%Y')}\n")
    # ...
    # (Simplified for targetContent and ReplacementContent match)
        f.write("="*50 + "\n")
        f.write(f"🔴 TRẠNG THÁI VĨ MÔ: {'SAFE' if is_macro_safe else 'RED ALERT'}\n")
        f.write(f"🟡 KHẨU VỊ RỦI RO: {'LONG' if allow_buy else 'STOP-BUY'}\n")
        f.write(f"📊 MỤC TIÊU NAV: {target_nav}\n")
        f.write("-" * 50 + "\n\n")
        
        if not is_macro_safe:
            f.write("🚨 HÀNH ĐỘNG: BÁN TOÀN BỘ DANH MỤC - THU TIỀN VỀ (CASH-OUT).\n")
        else:
            # V4.6 IRON GATE: Double-Gate Liquidity Filter
            # Cua 1: TB 20 phien >= 5 Ty (Suc manh lich su)
            # Cua 2: Phien hom nay  >= 5 Ty (Suc manh hien tai)
            # Cua 3: Gia > MA20 (Xu huong tang)
            # Cua 4: Gia >= 10k (Loc penny)
            price_latest = pivot_price.iloc[-1]
            valid_universe = (
                (price_latest >= 10) &
                (avg_value_20d >= 5_000_000_000) &
                (value_today >= 5_000_000_000) &
                (price_latest > pivot_ma20)
            )
            candidates = rs_combined[valid_universe].sort_values(ascending=False).head(10)
            
            f.write("🟢 DANH SÁCH THEO DÕI MUA (TOP RS + LIQUIDITY):\n")
            for ticker, rs in candidates.items():
                price = pivot_price.iloc[-1][ticker]
                f.write(f"- MUA: {ticker:<5} | RS: {rs:>4.1f} | Gia hien tai: {price:>7,.0f} VNĐ\n")
            
            f.write("\n⚠️ LƯU Ý: Kiem tra stop-loss -5% tai cho neu co bien dong vol lon (Rumor Shield).\n")

    print(f"✅ Da xuat chien lenh tai: {order_file}")
    print(f"📊 Muc tieu NAV hien tai: {target_nav}")

if __name__ == "__main__":
    generate_daily_orders()

