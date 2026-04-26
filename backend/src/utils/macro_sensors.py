import sys
import os
from pathlib import Path
import sqlite3
import pandas as pd

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

def check_macro_exceptions():
    """
    Alpha V4.4 - Macro Sentinel: Báo cáo ngoại lệ (Exception-based Alerts).
    Chỉ lên tiếng khi các 'Cầu dao' (Breakers) bị kích hoạt.
    """
    db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
    if not os.path.exists(db_path):
        return

    with get_connection() as conn:
        df_macro = pd.read_sql("SELECT * FROM macro_history", conn)

    if df_macro.empty: return

    # Xử lý dữ liệu vĩ mô
    df_macro['date'] = pd.to_datetime(df_macro['date'])
    pivot_macro = df_macro.pivot_table(index='date', columns='variable', values='value', aggfunc='max').ffill()
    
    # Tính MA20
    macro_ma20 = pivot_macro.rolling(20).mean()
    latest_vals = pivot_macro.iloc[-1]
    latest_ma20 = macro_ma20.iloc[-1]
    
    alerts = []
    
    # 🚨 RUI RO L1: TY GIA & DXY (100% Cash-out Breakers)
    if 'DXY' in latest_vals.index:
        if latest_vals['DXY'] > latest_ma20['DXY'] * 1.01:
            alerts.append(f"RED ALERT: [L1] DXY HIKE: {latest_vals['DXY']:.2f} > MA20 ({latest_ma20['DXY']:.2f}) -> [ACTION: CASH-OUT]")
    
    if 'USD_VND' in latest_vals.index:
        if latest_vals['USD_VND'] > latest_ma20['USD_VND'] * 1.005:
            alerts.append(f"RED ALERT: [L1] FX TENSION: {latest_vals['USD_VND']:,.0f} > MA20 ({latest_ma20['USD_VND']:,.0f}) -> [ACTION: CASH-OUT]")

    # ⚠️ RUI RO L2: BTC & GOLD (Stop-Buy Sensors)
    if 'BTC' in latest_vals.index:
        if latest_vals['BTC'] < latest_ma20['BTC'] * 0.90:
            alerts.append(f"WARNING: [L2] BTC CRASH: {latest_vals['BTC']:,.0f} < 90% MA20 ({latest_ma20['BTC']:,.0f}) -> [ACTION: STOP-BUY]")

    if 'GOLD_XAU' in latest_vals.index:
        if latest_vals['GOLD_XAU'] > latest_ma20['GOLD_XAU'] * 1.05:
            alerts.append(f"⚠️ [L2] GOLD SPIKE: {latest_vals['GOLD_XAU']:.2f} > 105% MA20 ({latest_ma20['GOLD_XAU']:.2f}) -> [ACTION: STOP-BUY]")

    # KẾT LUẬN: Chỉ in nếu có Alert
    if alerts:
        print("\n" + "!"*60)
        print("HỘI ĐỒNG VĨ MÔ: BÁO ĐỘNG ĐỎ (MACRO EXCEPTIONS)")
        print("!"*60)
        for msg in alerts:
            print(msg)
        print("!"*60 + "\n")
    else:
        # Nếu im lặng, in một dòng nhỏ để biết hệ thống vẫn đang Sentinel
        # (Theo yêu cầu 'Im lặng là Vàng' nhứng vẫn cần nhịp thở)
        pass 

if __name__ == "__main__":
    check_macro_exceptions()

