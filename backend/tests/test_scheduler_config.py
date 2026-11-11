"""test_scheduler_config.py — Guard lịch scheduler (setup_scheduler.py TASKS).

Chốt benchmark lịch 12 task (2026-08-09) sau khi:
  - PTCK_DAILY_BACKUP 23:00 → 16:30 (máy tắt ban đêm)
  - PTCK_WEEKLY_MAINTENANCE CN 02:00 → CN 08:00
  - PTCK_WEEKLY_MACRO CN 03:00 → CN 09:00
  - PTCK_BACKFILL_NOW gỡ khỏi scheduler (on-demand qua CLI)

Ngăn drift: mọi thay đổi giờ/lịch vô tình sẽ fail test này.

Run: python -m pytest backend/tests/test_scheduler_config.py -v
"""
import pathlib
import re
import sys

import pytest

import setup_scheduler
from setup_scheduler import TASKS

# ── Benchmark lịch chuẩn đã chốt ─────────────────────────────────────
# (frequency, start_time, days_of_week) — days rỗng "" = DAILY (mọi ngày)
APPROVED_SCHEDULE = {
    "PTCK_SBV_FIXTURE": ("DAILY", "07:45", ""),
    "PTCK_MORNING_CYCLE": ("WEEKLY", "08:00", "MON,TUE,WED,THU,FRI"),
    "PTCK_EARNINGS_CYCLE": ("WEEKLY", "08:20", "MON"),
    "PTCK_VIETSTOCK_CRAWL": ("WEEKLY", "08:30", "MON,TUE,WED,THU,FRI"),
    "PTCK_VGB10Y_SEED": ("WEEKLY", "08:45", "MON,TUE,WED,THU,FRI"),
    "PTCK_CAFEF_CRAWL": ("WEEKLY", "09:00", "MON,TUE,WED,THU,FRI"),
    "PTCK_DAILY_UPDATE": ("WEEKLY", "15:30", "MON,TUE,WED,THU,FRI"),
    "PTCK_CLOSE_CYCLE": ("WEEKLY", "15:45", "MON,TUE,WED,THU,FRI"),
    "PTCK_FLOW_MAP_REPORT": ("WEEKLY", "16:00", "MON,TUE,WED,THU,FRI"),
    "PTCK_DAILY_BACKUP": ("DAILY", "16:30", ""),
    "PTCK_WEEKLY_MAINTENANCE": ("WEEKLY", "08:00", "SUN"),
    "PTCK_WEEKLY_MACRO": ("WEEKLY", "09:00", "SUN"),
}

_ST_RE = re.compile(r"/ST\s+(\d{2}:\d{2})", re.IGNORECASE)
_D_RE = re.compile(r"/D\s+([A-Z,]+)", re.IGNORECASE)


def _parse_start_time(task):
    m = _ST_RE.search(task["schedule"])
    return m.group(1) if m else None


def _parse_days(task):
    m = _D_RE.search(task["schedule"])
    return m.group(1).upper() if m else ""


# ── 1. Số lượng task ─────────────────────────────────────────────────
def test_task_count():
    """Đúng 12 task, không thừa/thiếu, không còn on-demand BACKFILL_NOW."""
    assert len(TASKS) == 12
    names = {t["name"] for t in TASKS}
    assert "PTCK_BACKFILL_NOW" not in names


def test_no_duplicate_task_names():
    names = [t["name"] for t in TASKS]
    assert len(names) == len(set(names)), "Trùng tên task"


def test_all_approved_tasks_present():
    """Mọi task trong benchmark đều có mặt (không thiếu task)."""
    names = {t["name"] for t in TASKS}
    assert names == set(APPROVED_SCHEDULE.keys())


# ── 2. Khớp benchmark ────────────────────────────────────────────────
def test_frequency_matches_approved():
    for task in TASKS:
        expected_freq, _, _ = APPROVED_SCHEDULE[task["name"]]
        assert task["frequency"] == expected_freq, (
            f"{task['name']}: frequency={task['frequency']} != {expected_freq}")


def test_start_time_matches_approved():
    for task in TASKS:
        _, expected_st, _ = APPROVED_SCHEDULE[task["name"]]
        actual_st = _parse_start_time(task)
        assert actual_st == expected_st, (
            f"{task['name']}: start_time={actual_st} != {expected_st}")


def test_days_of_week_match_approved():
    for task in TASKS:
        _, _, expected_days = APPROVED_SCHEDULE[task["name"]]
        actual_days = _parse_days(task)
        assert actual_days == expected_days, (
            f"{task['name']}: days={actual_days or '(rỗng)'} != {expected_days or '(rỗng)'}")


def test_schedule_has_start_time():
    """Mọi task phải có /ST (lỗi PTCK_BACKFILL_NOW cũ: ONCE thiếu /ST)."""
    for task in TASKS:
        assert _parse_start_time(task) is not None, (
            f"{task['name']} thiếu /ST trong schedule")


def test_weekly_tasks_have_days():
    """WEEKLY bắt buộc khai /D; DAILY không được khai /D."""
    for task in TASKS:
        days = _parse_days(task)
        if task["frequency"] == "WEEKLY":
            assert days, f"{task['name']}: WEEKLY nhưng thiếu /D"
        else:
            assert days == "", f"{task['name']}: DAILY nhưng có /D={days}"


# ── 3. Khung giờ máy bật (guard) ─────────────────────────────────────
def test_no_execution_while_machine_off():
    """Mọi task chạy trong 07:00–18:00 (máy tắt ban đêm → không chạy)."""
    for task in TASKS:
        st = _parse_start_time(task)
        hour = int(st.split(":")[0])
        assert 7 <= hour <= 18, (
            f"{task['name']} cài giờ {st} — ngoài khung máy bật 07:00–18:00")


def test_daily_backup_after_close_cycle():
    """Backup (16:30) phải sau Close Cycle (15:45) + Flow Map (16:00)."""
    backup = _parse_start_time(next(t for t in TASKS if t["name"] == "PTCK_DAILY_BACKUP"))
    close = _parse_start_time(next(t for t in TASKS if t["name"] == "PTCK_CLOSE_CYCLE"))
    assert backup > close, "Backup phải chạy sau Close Cycle"


# ── 4. Đường dẫn tuyệt đối trong /TR (2026-11-11) ────────────────────
def test_actions_fit_schtasks_tr_limit():
    """[WHY] `schtasks /Create /TR` từ chối chuỗi dài hơn 261 ký tự (đo thật
    2026-11-11 khi chuyển dự án sang gốc D: dài hơn gốc E: cũ ~9 ký tự).
    Task quá dài sẽ không được tạo/cập nhật, im lặng giữ nguyên lệnh trỏ
    về checkout cũ — đúng kiểu "Missing > Fake" bị vi phạm.
    [INVARIANT] Mọi action đăng ký qua schtasks phải ≤ 261 ký tự; chuỗi dài
    phải gói trong wrapper .cmd ngắn."""
    for task in TASKS:
        length = len(task["action"])
        assert length <= 261, (
            f"{task['name']}: action dài {length} ký tự > 261 — schtasks sẽ từ chối /TR"
        )


def test_actions_have_no_external_workspace_path():
    """[WHY] 11 task trên máy này từng trỏ về checkout cũ trên ổ ngoài sau khi
    dự án chuyển sang ổ D:, và giữ `Last Result = 2` (file not found) nhiều
    tháng mà không ai thấy. [INVARIANT] Không action nào được chứa đường dẫn của
    checkout cũ. Chuỗi cần chặn được ghép lúc chạy để test không tự bắt chính nó.
    """
    drive = "E" + ":"
    sep = "\\"
    needles = (drive + sep + "DEV", drive + "/DEV", drive + sep + sep + "DEV")
    offenders = [
        f"{task['name']}: {needle}"
        for task in TASKS
        for needle in needles
        if needle in task["action"]
    ]
    assert not offenders, (
        "Action hẹn giờ còn trỏ về checkout ngoài ổ dự án:\n  " + "\n  ".join(offenders)
    )


def test_action_project_scripts_exist():
    """[WHY] Task trỏ tới file không tồn tại vẫn đăng ký thành công nhưng chạy
    ra `Last Result = 2` — hỏng im lặng. Mọi đường dẫn thuộc PROJECT_ROOT trong
    action phải trỏ tới file có thật tại thời điểm đăng ký.
    [INVARIANT] Chỉ kiểm các path nằm dưới PROJECT_ROOT (interpreter hệ thống
    nằm ngoài phạm vi dự án và có thể không tồn tại trên CI Linux).
    """
    missing = []
    for task in TASKS:
        for quoted in re.findall(r'"([^"]+)"', task["action"]):
            if not quoted.startswith(str(setup_scheduler.PROJECT_ROOT)):
                continue
            if not pathlib.Path(quoted).is_file():
                missing.append(f"{task['name']}: {quoted}")
    assert not missing, "Action trỏ tới file không tồn tại:\n  " + "\n  ".join(missing)


def test_setup_tasks_fails_closed_when_schtasks_denied(monkeypatch):
    """[WHY] Khi không có quyền admin, `schtasks /Create` trả "Access is denied"
    cho mọi task, nhưng script cũ vẫn exit 0 nên không ai biết task chưa được
    cập nhật. [INVARIANT] Có bất kỳ task lỗi ⇒ hàm trả mã lỗi (≠ 0)."""

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(setup_scheduler.subprocess, "run", lambda *a, **k: FakeResult())
    assert setup_scheduler.setup_tasks() == 1


def test_flow_map_action_uses_existing_short_wrapper():
    """[WHY] Chuỗi `eod-run && flow-map` dài 314 ký tự từng bị /TR từ chối; giờ
    nó phải nằm sau wrapper .cmd ngắn có thật trên đĩa, và wrapper vẫn phải
    nhận interpreter để không tự đoán python từ PATH."""
    assert setup_scheduler.EOD_FLOWMAP_WRAPPER.is_file(), (
        f"Thiếu wrapper: {setup_scheduler.EOD_FLOWMAP_WRAPPER}"
    )
    task = next(t for t in TASKS if t["name"] == "PTCK_FLOW_MAP_REPORT")
    assert str(setup_scheduler.EOD_FLOWMAP_WRAPPER) in task["action"]
    assert str(setup_scheduler.PYTHON_EXE) in task["action"]
    wrapper_text = setup_scheduler.EOD_FLOWMAP_WRAPPER.read_text(encoding="utf-8")
    assert "eod-run" in wrapper_text and "flow-map" in wrapper_text


def test_working_directory_command_targets_project_root():
    """[WHY] `schtasks /Create` không đặt được WorkingDirectory nên task chạy
    với CWD = C:\\Windows\\System32; mọi đường dẫn tương đối (log/output/.env)
    sẽ rơi vào thư mục hệ thống. [INVARIANT] Lệnh bù phải nhắm đúng task và
    đặt WorkingDirectory về PROJECT_ROOT hiện tại (không phải đường dẫn khác).
    """
    command = setup_scheduler.working_directory_command("PTCK_DEMO")
    assert "PTCK_DEMO" in command
    assert "WorkingDirectory" in command
    assert setup_scheduler._ps_quote(str(setup_scheduler.PROJECT_ROOT)) in command
    # Execute/Arguments phải được giữ nguyên từ task đã đăng ký
    assert "$a.Execute" in command and "$a.Arguments" in command


@pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler chỉ có trên Windows")
def test_setup_tasks_applies_working_directory(monkeypatch):
    """[WHY] Sau khi tạo task thành công phải áp WorkingDirectory cho ĐỦ 12
    task — bỏ sót một task nghĩa là task đó chạy trong system32."""

    class OkResult:
        returncode = 0
        stdout = ""
        stderr = ""

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return OkResult()

    monkeypatch.setattr(setup_scheduler.subprocess, "run", fake_run)
    assert setup_scheduler.setup_tasks() == 0
    powershell_calls = [c for c in calls if c and c[0] == "powershell"]
    assert len(powershell_calls) == len(TASKS)
    for call in powershell_calls:
        assert str(setup_scheduler.PROJECT_ROOT) in call[4]
