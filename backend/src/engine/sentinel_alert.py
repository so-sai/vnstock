
import sys
import os
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
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.db_core import get_connection
from src.engine.breadth_engine import run_breadth_analysis

def evaluate_sentinel_status():
    """
    Hệ thống Sentinel Alert v2.0 (The Architect Custom): 
    Giám sát 3 lớp phòng thủ để xác nhận 'Rừng hồi sinh' (First Green Shoots).
    """
    print("\n" + "🛡️ " * 20)
    print("      SENTINEL ALERT SYSTEM v2.0: EVALUATING DEFENSES      ")
    print("🛡️ " * 20)

    # 1. Thu thập dữ liệu Độ rộng (NH10 & Consistency)
    pulse = run_breadth_analysis()
    if not pulse:
        return {"status": "UNKNOWN"}

    # 2. Thu thập dữ liệu Động lượng (Momentum 6M > 0)
    with get_connection() as conn:
        df_ohlcv = pd.read_sql("SELECT symbol, date, close FROM daily_ohlcv WHERE date >= '2025-01-01'", conn)
    
    df_ohlcv['date'] = pd.to_datetime(df_ohlcv['date'], format='mixed')
    df_ohlcv = df_ohlcv.sort_values(['symbol', 'date'])
    g = df_ohlcv.groupby('symbol')
    df_ohlcv['return_6m'] = g['close'].transform(lambda x: (x / x.shift(125)) - 1)
    
    latest_date = df_ohlcv['date'].max()
    latest_mom = df_ohlcv[df_ohlcv['date'] == latest_date]
    mom_expansion_count = int((latest_mom['return_6m'] > 0).sum())

    # 3. Thu thập dữ liệu Dòng tiền (Foreign Net)
    with get_connection() as conn:
        df_foreign = pd.read_sql("SELECT date, SUM(net_value) as net_sum FROM market_foreign_history GROUP BY date ORDER BY date DESC LIMIT 3", conn)
    
    foreign_3d_all_above_limit = False
    if len(df_foreign) >= 3:
        # Check if all 3 latest days have net_sum > -100 (in Billion)
        # Note: net_value is already in Billion in MoneyFlowEngine
        recent_net = df_foreign['net_sum'].tolist()
        if all(val > -100 for val in recent_net):
            foreign_3d_all_above_limit = True

    # === THẨM ĐỊNH LỚP PHÒNG THỦ ===
    layer1_mom = mom_expansion_count >= 150
    layer2_breadth = pulse['nh10_consistency_3d'] == 3
    layer3_flow = foreign_3d_all_above_limit

    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_date": latest_date.strftime("%Y-%m-%d"),
        "layer1_mom_expansion": {
            "value": mom_expansion_count,
            "threshold": 150,
            "status": "PASS" if layer1_mom else "FAIL"
        },
        "layer2_nh10_consistency": {
            "value": pulse['nh10_consistency_3d'],
            "threshold": 3,
            "status": "PASS" if layer2_breadth else "FAIL"
        },
        "layer3_foreign_absorption": {
            "status": "PASS" if layer3_flow else "FAIL"
        }
    }

    # === PHÁN QUYẾT CUỐI CÙNG ===
    total_passed = sum([layer1_mom, layer2_breadth, layer3_flow])
    
    print("\n--- BÁO CÁO GIÁM ĐỊNH SENTINEL ---")
    print(f"Lớp 1 (Momentum Expansion > 150): {results['layer1_mom_expansion']['status']} ({mom_expansion_count}/150)")
    print(f"Lớp 2 (NH10 Consistency 3D):      {results['layer2_nh10_consistency']['status']} ({pulse['nh10_consistency_3d']}/3)")
    print(f"Lớp 3 (Foreign Absorption > -100B): {results['layer3_foreign_absorption']['status']}")
    print("-" * 40)

    if total_passed == 3:
        final_status = "GREEN (ALL CLEAR - BUY SIGNAL)"
    elif total_passed >= 1:
        final_status = "YELLOW (WATCHING - GREEN SHOOTS)"
    else:
        final_status = "RED (STANDBY - PHANTOM CITADEL)"

    flag = ">>" if sys.platform == "win32" else "\U0001f6a9"
    print(f"{flag} HỆ THỐNG CẢNH BÁO: {final_status}")
    print("-" * 40)

    # Lưu phán quyết
    results["final_status"] = final_status
    sentinel_path = os.path.join(src.config.DATA_DIR, "output", "sentinel_verdict.json")
    with open(sentinel_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)

    return results

if __name__ == "__main__":
    evaluate_sentinel_status()
