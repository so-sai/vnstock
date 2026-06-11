
import os
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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

PYTHON_EXE = sys.executable
BACKEND_DIR = PROJECT_ROOT / "backend"
PTCK_CLI = PROJECT_ROOT / "ptck.py"
DAILY_UPDATER = BACKEND_DIR / "src" / "daily_updater.py"
DB_MAINTENANCE = BACKEND_DIR / "src" / "db_maintenance.py"

TASKS = [
    {
        "name": "PTCK_DAILY_UPDATE",
        "description": "Cập nhật dữ liệu EOD hàng ngày (Thứ 2-6, 15:30)",
        "action": f'"{PYTHON_EXE}" "{DAILY_UPDATER}"',
        "schedule": "/SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:30",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_FLOW_MAP_REPORT",
        "description": "Báo cáo Dòng vốn Liên thị trường (Thứ 2-6, 16:00)",
        "action": f'cmd.exe /c ""{PYTHON_EXE}" "{PTCK_CLI}" daily-update && "{PYTHON_EXE}" "{PTCK_CLI}" flow-map"',
        "schedule": "/SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MAINTENANCE",
        "description": "Bảo trì DB hàng tuần (Chủ nhật, 02:00)",
        "action": f'"{PYTHON_EXE}" "{DB_MAINTENANCE}" --full',
        "schedule": "/SC WEEKLY /D SUN /ST 02:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MACRO",
        "description": "Cập nhật dữ liệu Vĩ mô hàng tuần (Chủ nhật, 03:00)",
        "action": f'"{PYTHON_EXE}" "{PROJECT_ROOT / "backend" / "screener.py"}" --mode macro',
        "schedule": "/SC WEEKLY /D SUN /ST 03:00",
        "run_level": "HIGHEST",
    },
]

def setup_tasks():
    print("\n" + "=" * 60)
    print("🛡️ PTCK WINDOWS TASK SCHEDULER SETUP")
    print("=" * 60)
    print(f"📍 Project Root: {PROJECT_ROOT}")
    print(f"🐍 Python: {PYTHON_EXE}")
    print()

    for task in TASKS:
        cmd = (
            f'schtasks /Create /TN "{task["name"]}" '
            f'/TR "{task["action"]}" '
            f'/SC WEEKLY '
            f'{task["schedule"]} '
            f'/RL {task["run_level"]} '
            f'/F '
            f'/COMMENT "{task["description"]}"'
        )
        print(f"⚡ Tạo task: {task['name']}")
        print(f"   📝 Mô tả: {task['description']}")
        print(f"   🔧 Lệnh: {task['action']}")
        print(f"   ⏰ Lịch: {task['schedule']}")

        result = os.system(cmd)
        if result == 0:
            print(f"   ✅ THÀNH CÔNG\n")
        else:
            print(f"   ❌ LỖI (cần chạy PowerShell với quyền Admin)\n")

    print("=" * 60)
    print("📋 Xem danh sách tasks:")
    print('   schtasks /Query /FO LIST | findstr "PTCK"')
    print("\n🗑️ Xóa tất cả tasks:")
    print('   schtasks /Delete /TN "PTCK_DAILY_UPDATE" /F')
    print('   schtasks /Delete /TN "PTCK_FLOW_MAP_REPORT" /F')
    print('   schtasks /Delete /TN "PTCK_WEEKLY_MAINTENANCE" /F')
    print('   schtasks /Delete /TN "PTCK_WEEKLY_MACRO" /F')
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
