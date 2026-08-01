"""setup_scheduler.py — Windows Task Scheduler Manager for PTCK_VNSTOCK.

WHY: PTCK_VNSTOCK relies on Windows Task Scheduler for automated EOD pipelines.

WIN11 BLACK-SCREEN BUG (Documented 2026-08-01):
  On Windows 11, when the monitor auto-offs during a scheduled task, the
  screen often stays BLACK even after the task completes. This is NOT a
  hardware fault. Root cause chain:
    1. Win11 uses Modern Standby (S0 Low Power Idle) — GPU drops to D3 cold.
    2. Task Scheduler wakes Python/Playwright processes that request GPU
       rendering.
    3. DWM.exe sends a handshake to the GPU; if GPU takes >2s to wake
       (D3→D0), Windows triggers TDR (Timeout Detection and Recovery) and
       resets the driver.
    4. Because no active display context exists (monitor off), the driver
       reset gets stuck, leaving the screen permanently black.
  Fix applied to this scheduler config:
    - PTCK_SBV_FIXTURE moved to 07:45 (15 min before Morning Cycle) so the
      monitor is already ON when it runs.
    - PTCK_CLOSE_CYCLE moved to 15:45 (15 min after Daily Update) to avoid
      concurrent GPU access at 15:30.
    - All Playwright tasks MUST run headless=True (no GUI browser window).
  Manual recovery if screen goes black: Win+Ctrl+Shift+B (resets GPU driver).
  Preventive: disable Fast Startup + PCI Express Link State Power Management.
"""

import io
import os
import shlex
import subprocess
import sys
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
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

PYTHON_EXE = sys.executable
BACKEND_DIR = PROJECT_ROOT / "backend"
PTCK_CLI = PROJECT_ROOT / "ptck.py"
DAILY_UPDATER = BACKEND_DIR / "src" / "daily_updater.py"
DB_MAINTENANCE = BACKEND_DIR / "src" / "db_maintenance.py"
DB_GUARDIAN = BACKEND_DIR / "src" / "database" / "database_guardian.py"

TASKS = [
    {
        "name": "PTCK_DAILY_UPDATE",
        # WIN11: 15:30 — runs BEFORE PTCK_CLOSE_CYCLE (15:45) to avoid
        # concurrent GPU access. Both use Playwright which can trigger
        # TDR black-screen if they overlap on a sleeping GPU.
        "description": "Cập nhật dữ liệu EOD hàng ngày (Thứ 2-6, 15:30) — chạy trước PTCK_CLOSE_CYCLE 15 phút",
        "action": f'"{PYTHON_EXE}" "{DAILY_UPDATER}"',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 15:30",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_FLOW_MAP_REPORT",
        # WIN11: 16:00 = 15 min after PTCK_CLOSE_CYCLE (15:45).
        # Stagger avoids concurrent GPU access — prevents TDR
        # black-screen when both tasks try to use Playwright
        # while the monitor is off.
        "description": "EOD Pipeline tự phục hồi + lũy đẳng (Thứ 2-6, 16:00 — chạy sau PTCK_CLOSE_CYCLE 15 phút)",
        "action": f'cmd.exe /c ""{PYTHON_EXE}" "{PTCK_CLI}" eod-run && "{PYTHON_EXE}" "{PTCK_CLI}" flow-map"',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 16:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MAINTENANCE",
        "description": "Bảo trì DB hàng tuần (Chủ nhật, 02:00)",
        "action": f'"{PYTHON_EXE}" "{DB_MAINTENANCE}" --full',
        "frequency": "WEEKLY",
        "schedule": "/D SUN /ST 02:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MACRO",
        "description": "Cập nhật dữ liệu Vĩ mô hàng tuần (Chủ nhật, 03:00)",
        "action": f'"{PYTHON_EXE}" "{PROJECT_ROOT / "backend" / "screener.py"}" --mode macro',
        "frequency": "WEEKLY",
        "schedule": "/D SUN /ST 03:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_DAILY_BACKUP",
        "description": "Database Guardian — Integrity Check + Online Backup mỗi tối (23:00)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" db backup',
        "frequency": "DAILY",
        "schedule": "/ST 23:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_SBV_FIXTURE",
        # WIN11: 07:45 = 15 min before Morning Cycle (08:00).
        # Monitor is ON by this time; avoids black-screen bug when
        # GPU wakes from D3 cold while display is still off.
        "description": "Chụp fixture HTML thô sbv.gov.vn hàng ngày — Self-healing Parser (07:45)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" sbv-update --save-fixture',
        "frequency": "DAILY",
        "schedule": "/ST 07:45",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_VIETSTOCK_CRAWL",
        "description": "Crawl BCTC Vietstock Finance cho nhóm cổ phiếu trọng điểm (Thứ 2-6, 08:30)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" cafef-crawl --symbols FPT ACB HDB MBB VCB HPG BCM VRE VHM MWG --source vietstock --playwright',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 08:30",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_CAFEF_CRAWL",
        "description": "Crawl BCTC 20 quý CafeF (requests + Playwright fallback) — bổ sung CFO dòng tiền (Thứ 2-6, 09:00)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" cafef-crawl --symbols FPT ACB HDB MBB VCB --source cafef --playwright --delay 1',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 09:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_VGB10Y_SEED",
        "description": "Seed VGB 10Y yield từ World Bank API (Thứ 2-6, 08:45)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" vgb10y',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 08:45",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_MORNING_CYCLE",
        "description": "Daily Cycle — morning: SBV/Sensors → MacroState → Governor → System Audit (Thứ 2-6, 08:00 — SBV Fixture chạy trước 15 phút ở 07:45)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" morning --persist',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 08:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_CLOSE_CYCLE",
        # WIN11: 15:45 = 15 min after Daily Update (15:30).
        # Stagger avoids concurrent GPU access with PTCK_DAILY_UPDATE
        # which also uses Playwright at 15:30 — prevents TDR black-screen.
        "description": "Daily Cycle — close: EOD → Breadth → Sector → Governor → System Audit (Thứ 2-6, 15:45)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" close --persist',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 15:45",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_BACKFILL_NOW",
        "description": "On-demand backfill: thu thập dữ liệu bù lịch sử 100% mật độ 30 quý (2019Q1–2026Q2)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" backfill-history --full --quarters 30',
        "frequency": "ONCE",
        "schedule": "",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_EARNINGS_CYCLE",
        "description": "Daily Cycle — earnings: Crawl → Health v2 → Valuation → Governor → System Audit (Thứ 2, 08:20 — stale_window 7d tự skip nếu chưa tới mùa)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" earnings --persist',
        "frequency": "WEEKLY",
        "schedule": "/D MON /ST 08:20",
        "run_level": "HIGHEST",
    },
]

def _build_args(task: dict) -> list:
    """Mảng tham số chuẩn cho subprocess.run — KHÔNG có /SC trùng lặp.

    LƯU Ý: /COMMENT bị schtasks.exe trên hệ thống này từ chối
    ("Invalid argument/option"), nên không dùng — task name đã mô tả đủ.
    """
    args = ["schtasks", "/Create", "/TN", task["name"],
            "/TR", task["action"],
            "/SC", task["frequency"]]
    if task.get("schedule"):
        args += shlex.split(task["schedule"])
    args += ["/RL", task["run_level"], "/F"]
    return args

def setup_tasks():
    print("\n" + "=" * 60)
    print("🛡️ PTCK WINDOWS TASK SCHEDULER SETUP")
    print("=" * 60)
    print(f"📍 Project Root: {PROJECT_ROOT}")
    print(f"🐍 Python: {PYTHON_EXE}")
    print()

    for task in TASKS:
        cmd_args = _build_args(task)
        print(f"⚡ Tạo task: {task['name']}")
        print(f"   📝 Mô tả: {task['description']}")
        print(f"   🔧 Lệnh: {task['action']}")
        print(f"   ⏰ Lịch: /SC {task['frequency']} {task['schedule']}")

        result = subprocess.run(
            cmd_args, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if result.returncode == 0:
            print("   ✅ THÀNH CÔNG\n")
        else:
            err = (result.stderr or result.stdout or "").strip()
            print(f"   ❌ LỖI: {err}\n")

    print("=" * 60)
    print("📋 Xem danh sách tasks:")
    print('   schtasks /Query /FO LIST | findstr "PTCK"')
    print("\n🗑️ Xóa tất cả tasks:")
    for task in TASKS:
        print(f'   schtasks /Delete /TN "{task["name"]}" /F')
    print("=" * 60)

def remove_tasks():
    print("\n🗑️ Xóa tất cả PTCK tasks...")
    for task in TASKS:
        cmd = f'schtasks /Delete /TN "{task["name"]}" /F'
        os.system(cmd)
        print(f"   ✅ Đã xóa: {task['name']}")
    print("✅ Hoàn tất.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PTCK Task Scheduler Manager")
    parser.add_argument("--setup", action="store_true", help="Tạo Windows Scheduled Tasks")
    parser.add_argument("--remove", action="store_true", help="Xóa tất cả PTCK tasks")
    args = parser.parse_args()

    if args.setup:
        setup_tasks()
    elif args.remove:
        remove_tasks()
    else:
        print("Sử dụng: python setup_scheduler.py --setup hoặc --remove")
