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
import logging
import shlex
import subprocess
import sys
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        # [WHY]: Cú pháp "except A, B, C:" không ngoặc chỉ hợp lệ từ Python 3.14
        # (PEP 758) — trên 3.12/3.13 (môi trường CI) đây là SyntaxError, làm hỏng
        # toàn bộ module scheduler. Giữ dạng có ngoặc để chạy được trên mọi bản.
        except OSError, AttributeError, ValueError:
            logging.getLogger(__name__).debug("Không reconfigure được stdout sang UTF-8 (bỏ qua)")
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


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

BACKEND_DIR = PROJECT_ROOT / "backend"
PTCK_CLI = PROJECT_ROOT / "ptck.py"
DAILY_UPDATER = BACKEND_DIR / "src" / "daily_updater.py"
DB_MAINTENANCE = BACKEND_DIR / "src" / "db_maintenance.py"
DB_GUARDIAN = BACKEND_DIR / "src" / "database" / "database_guardian.py"
# [WHY]: Chuỗi "eod-run && flow-map" dài 314 ký tự sau khi gốc dự án chuyển sang
# D:\04. Software\... (dài hơn gốc E: cũ) — vượt giới hạn 261 ký tự của
# `schtasks /TR` nên task không được cập nhật mà im lặng giữ lệnh cũ.
# Wrapper giữ nguyên ngữ nghĩa "&&" (flow-map chỉ chạy khi eod-run thành công)
# và nhận interpreter qua tham số để mọi đường dẫn vẫn nằm trong PROJECT_ROOT.
EOD_FLOWMAP_WRAPPER = BACKEND_DIR / "scripts" / "run_eod_flowmap.cmd"


def _resolve_python_exe() -> str:
    """Interpreter dùng cho mọi task hẹn giờ.

    [WHY] Task phải chạy ĐÚNG ổ đĩa của dự án và ĐÚNG bộ phụ thuộc: dùng venv
    của dự án khi có (đường dẫn luôn nằm dưới PROJECT_ROOT), chỉ khi không có
    venv (bản frozen/đóng gói) mới rơi về `sys.executable`.
    [INVARIANT] Trả về đường dẫn tuyệt đối của một file thực thi tồn tại; không
    bao giờ đoán "python" từ PATH.
    """
    venv = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


PYTHON_EXE = _resolve_python_exe()

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
        # [WHY]: /TR chỉ nhận ≤261 ký tự; chuỗi nối trực tiếp trước đây dài 314
        # ký tự nên schtasks từ chối và task giữ nguyên lệnh trỏ checkout cũ.
        "action": f'cmd.exe /c ""{EOD_FLOWMAP_WRAPPER}" "{PYTHON_EXE}""',
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 16:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MAINTENANCE",
        # WIN11: Chủ nhật 08:00 (thay vì 02:00 ban đêm) — máy tắt vào ban đêm
        # nên task cũ không bao giờ chạy. Chạy ban ngày khi máy bật.
        "description": "Bảo trì DB hàng tuần (Chủ nhật, 08:00 — máy bật ban ngày)",
        "action": f'"{PYTHON_EXE}" "{DB_MAINTENANCE}" --full',
        "frequency": "WEEKLY",
        "schedule": "/D SUN /ST 08:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_WEEKLY_MACRO",
        # WIN11: Chủ nhật 09:00 — sau PTCK_WEEKLY_MAINTENANCE (08:00) 1 giờ
        # để tránh đụng khóa DB. Máy bật ban ngày nên chạy được.
        "description": "Cập nhật dữ liệu Vĩ mô hàng tuần (Chủ nhật, 09:00 — máy bật ban ngày)",
        "action": f'"{PYTHON_EXE}" "{PROJECT_ROOT / "backend" / "screener.py"}" --mode macro',
        "frequency": "WEEKLY",
        "schedule": "/D SUN /ST 09:00",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_DAILY_BACKUP",
        # WIN11: 16:30 — chạy sau PTCK_FLOW_MAP_REPORT (16:00), khi EOD đã xong.
        # Trước đây để 23:00 nhưng máy tắt lúc đi ngủ nên task không bao giờ chạy.
        "description": "Database Guardian — Integrity Check + Online Backup (16:30, sau close cycle)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" db backup',
        "frequency": "DAILY",
        "schedule": "/ST 16:30",
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
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" cafef-crawl --symbols FPT ACB HDB MBB VCB HPG BCM VRE VHM MWG --source vietstock --playwright',  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        "frequency": "WEEKLY",
        "schedule": "/D MON,TUE,WED,THU,FRI /ST 08:30",
        "run_level": "HIGHEST",
    },
    {
        "name": "PTCK_CAFEF_CRAWL",
        "description": "Crawl BCTC 20 quý CafeF (requests + Playwright fallback) — bổ sung CFO dòng tiền (Thứ 2-6, 09:00)",
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" cafef-crawl --symbols FPT ACB HDB MBB VCB --source cafef --playwright --delay 1',  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
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
        "description": "Daily Cycle — morning: SBV/Sensors → MacroState → Governor → System Audit (Thứ 2-6, 08:00 — SBV Fixture chạy trước 15 phút ở 07:45)",  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
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
        "name": "PTCK_EARNINGS_CYCLE",
        "description": "Daily Cycle — earnings: Crawl → Health v2 → Valuation → Governor → System Audit (Thứ 2, 08:20 — stale_window 7d tự skip nếu chưa tới mùa)",  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        "action": f'"{PYTHON_EXE}" "{PTCK_CLI}" earnings --persist',
        "frequency": "WEEKLY",
        "schedule": "/D MON /ST 08:20",
        "run_level": "HIGHEST",
    },
]

# NOTE: PTCK_BACKFILL_NOW đã bị gỡ khỏi danh sách task tự động đăng ký.
# Backfill 30 quý là công việc on-demand (bảo trì chủ động), không có tính
# chu kỳ → không phù hợp đăng ký vào Windows Task Scheduler.
# Khi cần chạy, kích hoạt trực tiếp qua CLI:
#   python ptck.py backfill-history --full --quarters 30
# (Config cũ dùng /SC ONCE + schedule rỗng gây lỗi schtasks "No value
#  specified for /ST option" — đã loại bỏ để --setup không bao giờ fail.)


def _ps_quote(value: str) -> str:
    """Bọc một chuỗi thành literal PowerShell an toàn (nháy đơn, nhân đôi `'`)."""
    return "'" + str(value).replace("'", "''") + "'"


def working_directory_command(task_name: str) -> str:
    """Lệnh PowerShell đặt WorkingDirectory của 1 task về PROJECT_ROOT.

    [WHY] `schtasks /Create` KHÔNG có tham số WorkingDirectory, nên task tạo ra
    chạy với CWD = `C:\\Windows\\System32`. Mọi đường dẫn tương đối (log, output,
    `.env`, cache) khi đó rơi vào thư mục hệ thống thay vì gốc dự án — hỏng im
    lặng và khó truy (đúng loại lỗi "Missing > Fake"). Đặt tường minh về
    PROJECT_ROOT để task luôn chạy trong đúng checkout.
    [INVARIANT] Chỉ đổi WorkingDirectory; Execute/Arguments giữ nguyên chuỗi đã
    đăng ký (không viết lại đường dẫn).
    """
    return (
        f"$t = Get-ScheduledTask -TaskName {_ps_quote(task_name)}; "
        "$a = $t.Actions[0]; "
        f"Set-ScheduledTask -TaskName {_ps_quote(task_name)} -Action "
        "(New-ScheduledTaskAction -Execute $a.Execute -Argument $a.Arguments "
        f"-WorkingDirectory {_ps_quote(str(PROJECT_ROOT))}) | Out-Null"
    )


def apply_working_directory(task_name: str) -> tuple[bool, str]:
    """Áp WorkingDirectory cho task vừa tạo. Trả (ok, thông điệp lỗi)."""
    if sys.platform != "win32":
        return True, ""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", working_directory_command(task_name)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "").strip()


def _build_args(task: dict) -> list:
    """Mảng tham số chuẩn cho subprocess.run — KHÔNG có /SC trùng lặp.

    LƯU Ý: /COMMENT bị schtasks.exe trên hệ thống này từ chối
    ("Invalid argument/option"), nên không dùng — task name đã mô tả đủ.
    """
    args = ["schtasks", "/Create", "/TN", task["name"], "/TR", task["action"], "/SC", task["frequency"]]
    if task.get("schedule"):
        args += shlex.split(task["schedule"])
    args += ["/RL", task["run_level"], "/F"]
    return args


def setup_tasks() -> int:
    print("\n" + "=" * 60)
    print("🛡️ PTCK WINDOWS TASK SCHEDULER SETUP")
    print("=" * 60)
    print(f"📍 Project Root: {PROJECT_ROOT}")
    print(f"🐍 Python: {PYTHON_EXE}")
    print()

    failed = []
    for task in TASKS:
        cmd_args = _build_args(task)
        print(f"⚡ Tạo task: {task['name']}")
        print(f"   📝 Mô tả: {task['description']}")
        print(f"   🔧 Lệnh: {task['action']}")
        print(f"   ⏰ Lịch: /SC {task['frequency']} {task['schedule']}")

        result = subprocess.run(cmd_args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            wd_ok, wd_err = apply_working_directory(task["name"])
            if wd_ok:
                print(f"   ✅ THÀNH CÔNG (WorkingDirectory: {PROJECT_ROOT})\n")
            else:
                failed.append(task["name"])
                print(f"   ⚠️  Đã tạo task nhưng KHÔNG đặt được WorkingDirectory: {wd_err}\n")
        else:
            err = (result.stderr or result.stdout or "").strip()
            # [WHY]: trước đây script vẫn exit 0 dù mọi task đều "Access is denied",
            # nên 11 task tiếp tục trỏ về checkout ngoài mà không ai thấy (12 task
            # giữ Last Result = 2 trong nhiều tháng). Ghi nhận lỗi + trả mã lỗi
            # để runner/CI không coi là thành công.
            failed.append(task["name"])
            print(f"   ❌ LỖI: {err}\n")

    print("=" * 60)
    print("📋 Xem danh sách tasks:")
    print('   schtasks /Query /FO LIST | findstr "PTCK"')
    print("\n🗑️ Xóa tất cả tasks:")
    for task in TASKS:
        print(f'   schtasks /Delete /TN "{task["name"]}" /F')
    print("=" * 60)

    if failed:
        print(f"\n❌ {len(failed)}/{len(TASKS)} task KHÔNG được đăng ký: {', '.join(failed)}")
        print("   Nguyên nhân thường gặp: thiếu quyền quản trị (Access is denied).")
        return 1
    print(f"\n✅ Đã đăng ký {len(TASKS)}/{len(TASKS)} task.")
    return 0


def remove_tasks() -> int:
    print("\n🗑️ Xóa tất cả PTCK tasks...")
    failed = []
    for task in TASKS:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task["name"], "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            print(f"   ✅ Đã xóa: {task['name']}")
            continue
        err = (result.stderr or result.stdout or "").strip()
        # [WHY]: `os.system` bỏ qua mã trả về nên script in "✅ Đã xóa" cho cả task
        # chưa từng bị xóa — sau đó task cũ vẫn chạy đúng lịch cũ. Fail-closed:
        # chỉ báo xóa khi schtasks thật sự trả 0.
        failed.append(task["name"])
        print(f"   ❌ Không xóa được: {task['name']} — {err}")
    if failed:
        print(f"\n❌ {len(failed)}/{len(TASKS)} task chưa xóa được.")
        return 1
    print("✅ Hoàn tất.")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PTCK Task Scheduler Manager")
    parser.add_argument("--setup", action="store_true", help="Tạo Windows Scheduled Tasks")
    parser.add_argument("--remove", action="store_true", help="Xóa tất cả PTCK tasks")
    args = parser.parse_args()

    if args.setup:
        sys.exit(setup_tasks())
    elif args.remove:
        sys.exit(remove_tasks())
    else:
        print("Sử dụng: python setup_scheduler.py --setup hoặc --remove")
