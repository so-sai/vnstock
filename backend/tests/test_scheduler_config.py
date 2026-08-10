"""test_scheduler_config.py — Guard lịch scheduler (setup_scheduler.py TASKS).

Chốt benchmark lịch 12 task (2026-08-09) sau khi:
  - PTCK_DAILY_BACKUP 23:00 → 16:30 (máy tắt ban đêm)
  - PTCK_WEEKLY_MAINTENANCE CN 02:00 → CN 08:00
  - PTCK_WEEKLY_MACRO CN 03:00 → CN 09:00
  - PTCK_BACKFILL_NOW gỡ khỏi scheduler (on-demand qua CLI)

Ngăn drift: mọi thay đổi giờ/lịch vô tình sẽ fail test này.

Run: python -m pytest backend/tests/test_scheduler_config.py -v
"""
import re

import pytest

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
