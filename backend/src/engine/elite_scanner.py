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
            # Săn lùng Root dựa trên các điểm neo độc bản (screener.py, .kit)
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

import argparse
from datetime import datetime
from typing import Dict, List

import pandas as pd

import src.config
from src.database.db_core import get_connection
from src.engine.money_flow_engine import MoneyFlowEngine
from src.engine.strategy_commander import StrategyCommander
from src.engine.unit_normalizer import UnitNormalizer


def run_elite_scanner(deep_scan: bool = False) -> pd.DataFrame:
    """
    Elite Scanner Alpha V2.0 (Supreme Alpha - Alpha Brain)
    Đạt chuẩn Python 3.14 - Hợp nhất Nấc 0 (Macro), Nấc 1 (Market), Nấc 2 (Alpha).
    """
    mode_str: str = "DEEP SCAN (FULL HOSE)" if deep_scan else "QUICK SCAN (TOP 50)"
    print("\n" + "🏛️ " * 20)
    print(f"      KÍCH HOẠT BỘ CHỈ HUY TỐI CAO: {mode_str}      ")
    print("🏛️ " * 20)

    # 1. Khởi tạo các Engine Chỉ huy (Strict Typing)
    commander: StrategyCommander = StrategyCommander(show_log=False)
    money_flow: MoneyFlowEngine = MoneyFlowEngine(show_log=True)
    norm: UnitNormalizer = UnitNormalizer(show_log=True)

    # 0. CẬP NHẬT DỮ LIỆU TỨ THỜI
    print("\n📡 Đang cập nhật cảm biến Ngoại lực & Vĩ mô...")

    # 2. Nạp dữ liệu RS Rating
    rs_path: str = os.path.join(src.config.DATA_DIR, "market_rs.json")
    if not os.path.exists(rs_path):
        print("⚠️ Không tìm thấy market_rs.json. Hãy chạy rs_ranker.py trước.")
        return pd.DataFrame()

    rs_df: pd.DataFrame = pd.read_json(rs_path)

    # Ở chế độ Deep Scan, chúng ta quét TOÀN BỘ mã có trong RS Engine
    if deep_scan:
        top_candidates: List[str] = rs_df['symbol'].tolist()
        print(f"🕵️ Deep Scan: Nhận diện {len(top_candidates)} mã mục tiêu.")
    else:
        top_candidates: List[str] = rs_df.head(50)['symbol'].tolist()
        print(f"⚡ Quick Scan: Nhận diện {len(top_candidates)} mã Top đầu.")

    # Cập nhật Foreign Flow (Snapshot Accumulation)
    money_flow.update_foreign_history(top_candidates)

    # 3. PHÂN TÍCH BATTLE MAP (Nấc 0 -> 2)
    print("\n🧠 Đang xử lý Bản đồ chiến thuật (Battle Map)...")
    battle_report: pd.DataFrame = commander.analyze_battle_map(top_candidates)

    # 4. TRUY XUẤT NGÀNH DẪN DẮT (Heatmap)
    heatmap_path: str = os.path.join(src.config.DATA_DIR, "output", "sector_heatmap.json")
    top_3_sectors: List[str] = []
    if os.path.exists(heatmap_path):
        heatmap_df: pd.DataFrame = pd.read_json(heatmap_path)
        top_3_sectors = heatmap_df.head(3)['sector'].tolist()
        print(f"🔥 Nhóm ngành dẫn dắt mục tiêu: {', '.join(top_3_sectors)}")

    # 5. HỢP NHẤT DỮ LIỆU CUỐI CÙNG (Deduplicated & Cleaned)
    with get_connection() as conn:
        industry_map: pd.DataFrame = pd.read_sql("SELECT symbol, icb_name3 as sector FROM symbol_industry", conn)

    # Merge chuẩn snake_case
    final_df: pd.DataFrame = pd.merge(battle_report, industry_map, on='symbol', how='left')

    # 6. MASKING LAYER (Presentation Layer)
    # Tách biệt Dữ liệu (Internal) và Hiển thị (External) - Python 3.14 Decoupling
    display_map: Dict[str, str] = {
        "symbol": "Symbol",
        "rs_score": "RS Score",
        "rvol": "RVOL",
        "foreign_10d_acc": "Foreign 10D Acc (Bn)",
        "market_phase": "Market Phase",
        "breadth_pct": "Breadth",
        "action": "Action",
        "sector": "Sector"
    }

    # 7. HIỂN THỊ BÁO CÁO ALPHA BRAIN
    print("\n" + "💎" * 40)
    print("      DANH SÁCH KHUYẾN NGHỊ TÁC CHIẾN (SUPREME ALPHA V2.0)      ")
    print("💎" * 40)

    # Lọc chỉ lấy các mã có Action tích cực hoặc mạnh
    buy_list = final_df[final_df['action'].str.contains("BUY|ACCUMULATE")].copy()

    if buy_list.empty:
        print("⚠️ Không tìm thấy tín hiệu BẮN (BUY) đạt chuẩn Alpha Brain trong đợt quét này.")
    else:
        # Làm nổi bật các mã thuộc ngành dẫn dắt (Top 3)
        buy_list['In_Top_Sector'] = buy_list['sector'].isin(top_3_sectors)
        buy_list = buy_list.sort_values(['In_Top_Sector', 'rs_score'], ascending=[False, False])

        # Áp dụng Mapping Lớp Hiển thị cho Báo cáo
        presentation_df = buy_list.copy()
        presentation_df = presentation_df.rename(columns=display_map)

        print("-" * 115)
        print(f"{'SYMBOL':<8} | {'RS':<3} | {'RVOL':<5} | {'FOR-10D':<8} | {'SECTOR':<20} | {'ACTION'}")
        print("-" * 115)
        for _, row in buy_list.iterrows():
            marker = "🔥" if row['In_Top_Sector'] else "  "
            print(f"{marker} {row['symbol']:<6} | {int(row['rs_score']):>2} | {row['rvol']:>5.2f} | {row['foreign_10d_acc']:>8.2f} | {str(row['sector'])[:20]:<20} | {row['action']}")
        print("-" * 115)

        # 8. Lưu báo cáo Supreme Alpha (Dùng Mapping cho Header CSV)
        report_dir: str = os.path.join(src.config.DATA_DIR, "output", "reports")
        os.makedirs(report_dir, exist_ok=True)
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path: str = os.path.join(report_dir, f"supreme_alpha_report_{timestamp}.csv")

        # Lưu CSV với Header thân thiện (PascalCase) theo chỉ thị của Bộ Chỉ Huy
        presentation_df.to_csv(report_path, index=False)

        print(f"📝 Nhật ký quân cơ đã được lưu: {report_path}")
        if not buy_list.empty:
            print(f"💡 Tình trạng thị trường (Nấc 0): {buy_list.iloc[0]['market_phase']}")
            print(f"💡 Độ rộng thị trường (Nấc 1): {buy_list.iloc[0]['breadth_pct']}")

    return final_df

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alpha Brain Elite Scanner v2.0")
    parser.add_argument("--deep", action="store_true", help="Kích hoạt chế độ Deep Scan toàn thị trường")
    args = parser.parse_args()

    run_elite_scanner(deep_scan=args.deep)

