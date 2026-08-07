import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, "frozen", False):
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
    Hệ thống Sentinel Alert v2.0:
    Giám sát 3 lớp để xác nhận dấu hiệu phục hồi (Recovery Signals).
    """
    print("\n" + "─" * 50)
    print("      SENTINEL ALERT SYSTEM v2.0: EVALUATING MARKET STATUS      ")
    print("─" * 50)

    # 0. Bản thiết lập mặc định (Fallback) phòng ngừa lỗi sập luồng
    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_date": datetime.now().strftime("%Y-%m-%d"),
        "layer1_mom_expansion": {"value": 0, "threshold": 150, "status": "FAIL"},
        "layer2_nh10_consistency": {"value": 0, "threshold": 3, "status": "FAIL"},
        "layer3_foreign_absorption": {"status": "FAIL"},
        "final_status": "RED (STANDBY)",
    }

    pulse = None
    try:
        # 1. Thu thập dữ liệu Độ rộng (NH10 & Consistency)
        pulse = run_breadth_analysis()
    except Exception as e:
        print(f"[Sentinel Alert] LỖI khi chạy run_breadth_analysis: {e}")

    nh10_val = 0
    if pulse and isinstance(pulse, dict):
        nh10_val = pulse.get("nh10_consistency_3d", 0)
        results["layer2_nh10_consistency"]["value"] = nh10_val
        if nh10_val == 3:
            results["layer2_nh10_consistency"]["status"] = "PASS"

    mom_expansion_count = 0
    try:
        # 2. Thu thập dữ liệu Động lượng (Momentum 6M > 0)
        with get_connection() as conn:
            df_ohlcv = pd.read_sql("SELECT symbol, date, close FROM daily_ohlcv WHERE date >= '2025-01-01'", conn)

        if df_ohlcv is not None and not df_ohlcv.empty:
            df_ohlcv["date"] = pd.to_datetime(df_ohlcv["date"], format="mixed", errors="coerce")
            df_ohlcv = df_ohlcv.dropna(subset=["date", "close", "symbol"])
            if not df_ohlcv.empty:
                df_ohlcv = df_ohlcv.sort_values(["symbol", "date"])
                g = df_ohlcv.groupby("symbol")
                # Sử dụng bfill/ffill để xử lý NaN an toàn
                df_ohlcv["return_6m"] = g["close"].transform(lambda x: (x / x.shift(125).ffill()) - 1 if len(x) > 125 else 0)
                df_ohlcv["return_6m"] = df_ohlcv["return_6m"].fillna(0)

                latest_date = df_ohlcv["date"].max()
                if pd.notnull(latest_date):
                    results["market_date"] = latest_date.strftime("%Y-%m-%d")
                    latest_mom = df_ohlcv[df_ohlcv["date"] == latest_date]
                    mom_expansion_count = int((latest_mom["return_6m"] > 0).sum())
    except Exception as e:
        print(f"[Sentinel Alert] LỖI khi tính toán layer1_mom_expansion: {e}")

    results["layer1_mom_expansion"]["value"] = mom_expansion_count
    layer1_mom = mom_expansion_count >= 150
    results["layer1_mom_expansion"]["status"] = "PASS" if layer1_mom else "FAIL"

    foreign_3d_all_above_limit = False
    try:
        # 3. Thu thập dữ liệu Dòng tiền (Foreign Net)
        with get_connection() as conn:
            df_foreign = pd.read_sql(
                "SELECT date, SUM(net_value) as net_sum FROM market_foreign_history GROUP BY date ORDER BY date DESC LIMIT 3",
                conn,
            )

        if df_foreign is not None and len(df_foreign) >= 3:
            recent_net = df_foreign["net_sum"].tolist()
            if all(val > -100 for val in recent_net):
                foreign_3d_all_above_limit = True
    except Exception as e:
        print(f"[Sentinel Alert] LỖI khi đọc market_foreign_history: {e}")

    layer2_breadth = nh10_val == 3
    layer3_flow = foreign_3d_all_above_limit

    results["layer3_foreign_absorption"]["status"] = "PASS" if layer3_flow else "FAIL"

    # === PHÁN QUYẾT CUỐI CÙNG ===
    total_passed = sum([layer1_mom, layer2_breadth, layer3_flow])

    print("\n--- BÁO CÁO TRẠNG THÁI SENTINEL ---")
    print(f"Lớp 1 (Momentum Expansion > 150): {results['layer1_mom_expansion']['status']} ({mom_expansion_count}/150)")
    print(f"Lớp 2 (NH10 Consistency 3D):      {results['layer2_nh10_consistency']['status']} ({nh10_val}/3)")
    print(f"Lớp 3 (Foreign Absorption > -100B): {results['layer3_foreign_absorption']['status']}")
    print("-" * 40)

    if total_passed == 3:
        final_status = "GREEN (CONFIRMED - BUY SIGNAL)"
    elif total_passed >= 1:
        final_status = "YELLOW (RECOVERING)"
    else:
        final_status = "RED (STANDBY)"

    flag = ">>" if sys.platform == "win32" else "\U0001f6a9"
    print(f"{flag} HỆ THỐNG CẢNH BÁO: {final_status}")
    print("-" * 40)

    # Lưu phán quyết
    results["final_status"] = final_status
    try:
        sentinel_path = os.path.join(src.config.DATA_DIR, "output", "sentinel_verdict.json")
        # Đảm bảo thư mục tồn tại
        os.makedirs(os.path.dirname(sentinel_path), exist_ok=True)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[Sentinel Alert] Không thể ghi file sentinel_verdict.json: {e}")

    return results


if __name__ == "__main__":
    evaluate_sentinel_status()
