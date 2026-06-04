
import sys
import os
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
from src.daily_updater import run_daily_update
from src.engine.sentinel_alert import evaluate_sentinel_status
from src.engine.decision_engine import merge_decisions
from src.database.timeline_manager import log_regime_state, get_regime_history
from src.database.db_core import optimize_sqlite_engine

def create_markdown_report(verdict, target_date):
    """Lưu nhật ký tác chiến (War Journal) dưới dạng Markdown"""
    report_dir = src.config.DATA_DIR / "reports"
    if not report_dir.exists():
        report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"{target_date}_verdict.md"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 🛡️ NHẬT KÝ TÁC CHIẾN - {target_date}\n\n")
        f.write(f"**Thời gian thực thi:** {datetime.now().strftime('%H:%M:%S')}\n")
        
        # Section 1: Institutional Decision
        if 'decision' in verdict:
            d = verdict['decision']
            f.write(f"## 🏛️ PHÁN QUYẾT BỘ CHỈ HUY (THE BOARDROOM)\n\n")
            f.write(f"- **TRẠNG THÁI THỊ TRƯỜNG:** `{d['market_status']}` (Score: {d['regime_score']})\n")
            f.write(f"- **MÔ HÌNH ƯU TIÊN:** `{d['active_model']}`\n")
            f.write(f"- **PHÁN QUYẾT CUỐI CÙNG:** **{d['consensus']}**\n")
            f.write(f"- **ĐỘ TIN CẬY (CONFIDENCE):** `{d['confidence'] * 100}%`\n\n")
            
            if d['model_b']['top_picks']:
                f.write("### 🎯 Danh sách Quan tâm (Mean Reversion Selection)\n")
                for pick in d['model_b']['top_picks']:
                    f.write(f"- {pick['symbol']} (Z-Score: {pick['z_score']}, RSI: {pick['rsi']})\n")
                f.write("\n")

        # Section 2: Decision Trajectory (Lighthouse)
        f.write("## 📡 QUỸ ĐẠO QUYẾT ĐỊNH (THE LIGHTHOUSE)\n\n")
        history = get_regime_history(limit=5)
        if not history.empty:
            f.write("| Ngày | Score | Vị thế | Gia tốc |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            for _, h in history.iterrows():
                f.write(f"| {h['date']} | {h['regime_score']} | `{h['status']}` | {h['breadth_velocity']:+.1f}% |\n")
            f.write("\n")

        # Section 3: Ignition Switch (Recovery)
        if 'decision' in verdict:
            rec = verdict['decision']['recovery']
            f.write("## 🚀 BỘ ĐÁNH LỬA (IGNITION SWITCH)\n\n")
            f.write(f"- **TRẠNG THÁI PHỤC HỒI:** `{rec['status']}`\n")
            f.write(f"- **GIA TỐC ĐỘ RỘNG (5D):** `{rec['details']['velocity_5d']:+.1f}%` (Ngưỡng: +15%)\n")
            f.write(f"- **XÁC NHẬN MA10:** `{'YES' if rec['ma10_reclaim'] else 'NO'}`\n\n")

        # Section 4: Sentinel Details
        f.write("## 🔍 Chi tiết Giám định Sentinel (Model A)\n\n")
        f.write(f"- **Momentum Expansion (Lớp 1):** {verdict['layer1_mom_expansion']['status']} ({verdict['layer1_mom_expansion']['value']}/{verdict['layer1_mom_expansion']['threshold']})\n")
        f.write(f"- **NH10 Consistency (Lớp 2):** {verdict['layer2_nh10_consistency']['status']} ({verdict['layer2_nh10_consistency']['value']}/{verdict['layer2_nh10_consistency']['threshold']} ngày)\n")
        f.write(f"- **Foreign Absorption (Lớp 3):** {verdict['layer3_foreign_absorption']['status']}\n\n")
        
        f.write("---\n")
        f.write("*Bản báo cáo này được tạo tự động bởi PTCK_VNSTOCK Multi-Model Decision Stack.*")
    
    return report_path

def run_daily_closer():
    """Quy trình đóng phiên tự động (The Dragon Shield Automation)"""
    target_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"🐉 THE DRAGON SHIELD: DAILY CLOSER - {target_date}")
    # Step 0: Optimize/Init DB
    optimize_sqlite_engine()

    # Step 1: Sync Data
    run_daily_update(target_date)

    # Step 2: Run Sentinel (Model A)
    verdict = evaluate_sentinel_status()

    # Step 3: Run Decision Engine (Consensus)
    decision = merge_decisions(verdict)
    verdict['decision'] = decision
    
    # Step 3.1: Log to Timeline
    log_regime_state(decision)

    # Step 4: Generate Report
    if verdict:
        report_file = create_markdown_report(verdict, target_date)
        print(f"\n✅ War Journal saved to: {report_file}")

    print(f"\n{'='*60}")
    print(f"🏁 CLOSER COMPLETE. SENTINEL STANDING BY.")
    print(f"{'='*60}")

if __name__ == "__main__":
    run_daily_closer()
