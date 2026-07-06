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
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
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
from core.macro.gold_regime_engine import analyze_gold_regime
from core.macro.gold_spread_engine import analyze_domestic_premium

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

    # ⚠️ RUI RO L2: BTC & GOLD (Stop-Buy Sensors + Cognitive)
    if 'BTC' in latest_vals.index:
        if latest_vals['BTC'] < latest_ma20['BTC'] * 0.90:
            alerts.append(f"WARNING: [L2] BTC CRASH: {latest_vals['BTC']:,.0f} < 90% MA20 ({latest_ma20['BTC']:,.0f}) -> [ACTION: STOP-BUY]")
        elif latest_vals['BTC'] > latest_ma20['BTC'] * 1.10:
            alerts.append(f"INFO: [L2] BTC PUMP: {latest_vals['BTC']:,.0f} > 110% MA20 ({latest_ma20['BTC']:,.0f}) -> [WATCH: RISK-ON]")

    gold_regime = analyze_gold_regime()
    gold_signals = gold_regime.get("signals", {})
    gold_latest = latest_vals.get('GOLD_XAU', 0)
    gold_ma = latest_ma20.get('GOLD_XAU', 0) if 'GOLD_XAU' in latest_ma20.index else 0

    if gold_signals.get("above_ma20_5pct", False):
        velocity = gold_regime.get("velocity", 0)
        spread = gold_regime.get("spread_pressure", 0)
        label = gold_regime.get("gold_regime", "NEUTRAL")
        if spread > 0.4:
            alerts.append(f"⚠️ [L2] GOLD SPIKE + SPREAD MỞ RỘNG: ${gold_latest:.2f} > 105% MA20 | velocity={velocity:.2f} spread={spread:.2f} -> [ACTION: STOP-BUY | EPISTEMIC: DEFENSIVE]")
        else:
            alerts.append(f"⚠️ [L2] GOLD SPIKE: ${gold_latest:.2f} > 105% MA20 ({gold_ma:.2f}) | regime={label} -> [ACTION: STOP-BUY]")
    elif gold_signals.get("velocity_high", False):
        alerts.append(f"INFO: [L2] GOLD VELOCITY CAO: velocity={gold_regime.get('velocity', 0):.2f} -> [WATCH]")
    
    # Gold regime summary
    if gold_regime.get("macro_bias") == "DEFENSIVE":
        scenarios = gold_regime.get("signals", {})
        if scenarios.get("spread_pressure_high"):
            alerts.append(f"INFO: [GOLD] Phòng thủ + spread mở rộng — liquidity distortion đang hình thành")

    # Domestic Premium sensor
    try:
        premium = analyze_domestic_premium()
        p_regime = premium.get("premium_regime", "PREMIUM_NORMAL")
        p_pct = premium.get("premium_pct", 0)
        if p_regime == "PREMIUM_SURGE":
            alerts.append(f"⚠️ [GOLD] PREMIUM SURGE: +{p_pct}% — Cầu trú ẩn nội địa cực mạnh, méo mó thanh khoản")
        elif p_regime == "PREMIUM_ELEVATED":
            alerts.append(f"INFO: [GOLD] Premium tăng: +{p_pct}% — Tâm lý phòng thủ nội địa")
        elif p_regime == "PREMIUM_DISCOUNT":
            alerts.append(f"INFO: [GOLD] Premium âm: {p_pct}% — Vàng trong nước rẻ hơn thế giới, tâm lý ổn định")
    except Exception:
        pass

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

