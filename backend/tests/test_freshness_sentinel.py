"""test_freshness_sentinel.py — Giải pháp C: Freshness Sentinel Gate.

8 ca: cuối tuần · ngày lễ · trước 16:30 · sau 16:30 · đúng biên 16:30 ·
halt fail-closed · force-stale + ledger · bộ lọc is_stale (dòng nhiễm).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.core import freshness_sentinel as fs

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _dt(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=VN_TZ)


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch):
    """Mặc định bypass cho các test khác; test gate sẽ tự gỡ cờ này."""
    monkeypatch.setenv(fs.TEST_MODE_ENV, "1")


@pytest.fixture
def memdb():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_ohlcv (date TEXT, symbol TEXT, is_stale INTEGER)")
    yield conn
    conn.close()


def _seed(conn, date_str, n_clean=0, n_dirty=0, prefix="S"):
    for i in range(n_clean):
        conn.execute("INSERT INTO daily_ohlcv VALUES (?,?,?)", (date_str, f"{prefix}{i:04d}", 0))
    for i in range(n_dirty):
        conn.execute("INSERT INTO daily_ohlcv VALUES (?,?,?)", (date_str, f"D{i:04d}", 1))
    conn.commit()


# ── 1. Cuối tuần ───────────────────────────────────────────────────────
def test_weekend():
    # Thứ Bảy 2026-10-10 → kỳ vọng Thứ Sáu 2026-10-09
    assert fs.expected_trading_date(now=_dt(2026, 10, 10, 10, 0)) == "2026-10-09"
    # Chủ Nhật 2026-10-11 → vẫn Thứ Sáu 2026-10-09
    assert fs.expected_trading_date(now=_dt(2026, 10, 11, 10, 0)) == "2026-10-09"


# ── 2. Ngày lễ (dùng calendar THẬT + weekend_holidays.json) ────────────
def test_holiday():
    # 2026-09-02 và 09-03 là ngày lễ trong config → lùi về 2026-09-01
    assert fs.expected_trading_date(now=_dt(2026, 9, 3, 10, 0)) == "2026-09-01"
    # Ngày lễ mà SAU cutoff vẫn không được coi là phiên có dữ liệu
    assert fs.expected_trading_date(now=_dt(2026, 9, 3, 17, 0)) == "2026-09-01"


# ── 3. Ngày thường TRƯỚC 16:30 ─────────────────────────────────────────
def test_weekday_before_1630():
    # Thứ Hai 2026-10-12 10:00 → Thứ Sáu 2026-10-09 (EOD hôm nay chưa chạy)
    assert fs.expected_trading_date(now=_dt(2026, 10, 12, 10, 0)) == "2026-10-09"


# ── 4. Ngày thường SAU 16:30 ───────────────────────────────────────────
def test_weekday_after_1630():
    assert fs.expected_trading_date(now=_dt(2026, 10, 12, 17, 0)) == "2026-10-12"


# ── 5. Đúng biên 16:30 (16:29 vs 16:30) ────────────────────────────────
def test_cutoff_boundary():
    assert fs.expected_trading_date(now=_dt(2026, 10, 12, 16, 29)) == "2026-10-09"
    assert fs.expected_trading_date(now=_dt(2026, 10, 12, 16, 30)) == "2026-10-12"


# ── 6. Halt fail-closed ────────────────────────────────────────────────
def test_stale_halt_fail_closed(monkeypatch, memdb):
    monkeypatch.delenv(fs.TEST_MODE_ENV, raising=False)
    monkeypatch.setenv("PTCK_MIN_CLEAN_ROWS", "2")
    _seed(memdb, "2026-10-02", n_clean=5)
    # Thứ Tư 2026-10-07 17:00 → kỳ vọng 2026-10-07, data chỉ tới 10-02 → HALT
    with pytest.raises(fs.DataStaleHalt) as exc:
        fs.assert_fresh("analyze", conn=memdb, now=_dt(2026, 10, 7, 17, 0))
    assert "DATA_STALE_HALT" in str(exc.value)
    assert "2026-10-02" in str(exc.value)
    assert fs.EXIT_CODE_STALE == 2


# ── 7. force-stale → cho qua + ghi ledger đúng format ──────────────────
def test_force_stale_override_and_ledger(monkeypatch, memdb, tmp_path):
    monkeypatch.delenv(fs.TEST_MODE_ENV, raising=False)
    monkeypatch.setenv("PTCK_MIN_CLEAN_ROWS", "2")
    ledger = tmp_path / "stale_override_ledger.jsonl"
    monkeypatch.setattr(fs, "ledger_path", lambda: ledger)
    _seed(memdb, "2026-10-02", n_clean=5)

    fs.assert_fresh("analyze", force_stale=True, conn=memdb, now=_dt(2026, 10, 7, 17, 0))

    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert entry["event"] == "DATA_STALE_OVERRIDE"
    assert entry["command"] == "analyze"
    assert entry["max_clean_date"] == "2026-10-02"
    assert entry["expected_date"] == "2026-10-07"
    assert entry["reason"] == "user_provided_force_flag"
    assert entry["timestamp"].endswith("+07:00")


# ── 8a. Dữ liệu đủ tươi → không halt ───────────────────────────────────
def test_same_session_no_halt(monkeypatch, memdb):
    monkeypatch.delenv(fs.TEST_MODE_ENV, raising=False)
    monkeypatch.setenv("PTCK_MIN_CLEAN_ROWS", "2")
    _seed(memdb, "2026-10-07", n_clean=5)
    fs.assert_fresh("final-decision", conn=memdb, now=_dt(2026, 10, 7, 17, 0))


# ── 8b. Ngày toàn dòng nhiễm KHÔNG được coi là tươi ────────────────────
def test_stale_filter_excludes_dirty(monkeypatch, memdb):
    monkeypatch.setenv("PTCK_MIN_CLEAN_ROWS", "500")
    # 01/10 từng có 901 dòng nhiễm + 180 dòng sạch (< 500) → KHÔNG tính là tươi
    _seed(memdb, "2026-10-01", n_clean=180, n_dirty=901)
    # 30/09 đủ sạch (600 >= 500) → đây mới là mốc tươi thật
    _seed(memdb, "2026-09-30", n_clean=600, n_dirty=301)
    assert fs.latest_clean_date(conn=memdb) == "2026-09-30"

    # Nếu KHÔNG lọc is_stale thì MAX(date) trần sẽ trả 2026-10-01 → gate mù
    row = memdb.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
    assert row[0] == "2026-10-01"
