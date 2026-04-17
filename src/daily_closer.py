
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
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
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

def create_markdown_report(verdict, target_date):
    """Lưu nhật ký tác chiến (War Journal) dưới dạng Markdown"""
    report_dir = src.config.DATA_DIR / "reports"
    if not report_dir.exists():
        report_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = report_dir / f"{target_date}_verdict.md"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 🛡️ NHẬT KÝ TÁC CHIẾN - {target_date}\n\n")
        f.write(f"**Thời gian thực thi:** {datetime.now().strftime('%H:%M:%S')}\n")
        f.write(f"**Phán quyết Sentinel:** `{verdict['final_status']}`\n\n")
        
        f.write("## 🔍 Chi tiết Giám định\n\n")
        f.write(f"- **Momentum Expansion (Lớp 1):** {verdict['layer1_mom_expansion']['status']} ({verdict['layer1_mom_expansion']['value']}/{verdict['layer1_mom_expansion']['threshold']})\n")
        f.write(f"- **NH10 Consistency (Lớp 2):** {verdict['layer2_nh10_consistency']['status']} ({verdict['layer2_nh10_consistency']['value']}/{verdict['layer2_nh10_consistency']['threshold']} ngày)\n")
        f.write(f"- **Foreign Absorption (Lớp 3):** {verdict['layer3_foreign_absorption']['status']}\n\n")
        
        f.write("---\n")
        f.write("*Bản báo cáo này được tạo tự động bởi PTCK_VNSTOCK Sentinel Alert System.*")
    
    return report_path

def run_daily_closer():
    """Quy trình đóng phiên tự động (The Dragon Shield Automation)"""
    target_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"🐉 THE DRAGON SHIELD: DAILY CLOSER - {target_date}")
    print(f"{'='*60}")

    # Step 1: Sync Data
    run_daily_update(target_date)

    # Step 2: Run Alert
    verdict = evaluate_sentinel_status()

    # Step 3: Generate Report
    if verdict:
        report_file = create_markdown_report(verdict, target_date)
        print(f"\n✅ War Journal saved to: {report_file}")

    print(f"\n{'='*60}")
    print(f"🏁 CLOSER COMPLETE. SENTINEL STANDING BY.")
    print(f"{'='*60}")

if __name__ == "__main__":
    run_daily_closer()
