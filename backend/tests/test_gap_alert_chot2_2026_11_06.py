# -*- coding: utf-8 -*-
"""CHỐT #2 — Mục 27 (NQ-17 §IV): `daily-update` BÁO ĐỘNG lỗ hổng phiên, KHÔNG tự bù.

[WHY]
    Sự cố 04/11/2026: cron miss ⇒ không phiên nào cào 04/11. Sáng 05/11, `daily-update`
    chạy cho 05/11 như thường, KHÔNG một dòng cảnh báo; tới 15:42 `eod-run` vẫn hạch toán
    trên nền khuyết, phải 17:33 mới có người bù tay. Nếu `daily-update` đã in cảnh báo
    ngay đầu log thì sự cố đã lộ ra lúc 15:15 thay vì 2 giờ sau.

[ROOT CAUSE]
    `run_daily_update(target_date)` chỉ cào đúng `target_date`; không có bất kỳ phép so
    sánh nào giữa `MAX(date)` của `daily_ohlcv` và `target_date`. Không ai hỏi "các phiên
    TRƯỚC đã đủ chưa".

[INVARIANT]
    (a) có lỗ hổng ⇒ một dòng CRITICAL nêu ĐÚNG tên phiên + lệnh khắc phục;
    (b) TUYỆT ĐỐI không gọi `backfill_market_history` (NQ-17 §IV phương án B);
    (c) ba loại suy giảm phải phân biệt: missing (0 bar) / partial (<100 bar) / stale_only
        (toàn `is_stale=1`) — không gộp thành "mã thiếu";
    (d) không quét được ⇒ `None` + lỗi, KHÔNG ghi 0 (AGENTS §4A).
"""

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _db(rows: list[tuple[str, int, int]]) -> sqlite3.Connection:
    """`rows` = (date, so_bar_khong_stale, so_bar_stale)."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, is_stale INTEGER)")
    data = []
    for d, fresh, stale in rows:
        data += [(f"F{i}", d, 0) for i in range(fresh)]
        data += [(f"S{i}", d, 1) for i in range(stale)]
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?)", data)
    con.commit()
    return con


def _sessions_ending(as_of: str, n: int) -> list[str]:
    """`n` phiên giao dịch gần nhất kết thúc tại `as_of` (tăng dần)."""
    from src.database.session_continuity import previous_session

    out = [as_of]
    cur = as_of
    while len(out) < n:
        cur = previous_session(cur)
        out.append(cur)
    return list(reversed(out))


def _full_window(as_of: str, n: int, anomalies: dict[str, tuple[int, int]] | None = None):
    """Dựng cửa sổ ĐẦY ĐỦ (900 bar/phiên) rồi khoét đúng các bất thường cần đo.

    [WHY] Cửa sổ phải đủ mới đo được đúng thứ mình muốn đo: nếu DB giả chỉ có 3 phiên mà
    hỏi 10 phiên, cổng fail-closed sẽ (đúng đắn) báo 7 phiên khuyết và test đo sai thứ.
    """
    anomalies = anomalies or {}
    rows = []
    for d in _sessions_ending(as_of, n):
        rows.append((d, *anomalies.get(d, (900, 0))))
    return _db(rows)


# ===================================================================
# 1. Phân loại rạch ròi (missing / partial / stale_only)
# ===================================================================
def test_detects_hole_04_11_and_names_the_day():
    """[WHY] Tái hiện đúng sự cố: có 03/11 và 05/11, thiếu 04/11 ⇒ phải nêu tên ngày để
    người trực chạy `daily-update --date 2026-11-04` chứ không đoán.
    """
    from src.database.session_continuity import detect_session_gaps

    con = _full_window("2026-11-05", 10, {"2026-11-04": (0, 0)})
    try:
        gaps = detect_session_gaps(con, "2026-11-05", lookback_sessions=10)
    finally:
        con.close()
    assert gaps["has_gap"] is True
    assert gaps["missing"] == ["2026-11-04"], gaps
    assert gaps["partial"] == []
    assert gaps["stale_only"] == []
    assert gaps["checked_sessions"] == 10


def test_partial_ingest_is_not_counted_as_present():
    """[WHY] Chống "Fake": một phiên còn 40/900 bar là ingest chết giữa đường. Nếu coi là
    "đã có" thì cổng mù đúng vào loại sự cố nguy hiểm nhất (dữ liệu nửa vời).
    """
    from src.database.session_continuity import detect_session_gaps

    con = _full_window("2026-11-05", 5, {"2026-11-04": (40, 0)})
    try:
        gaps = detect_session_gaps(con, "2026-11-05", lookback_sessions=5)
    finally:
        con.close()
    assert gaps["partial"] == ["2026-11-04"]
    assert gaps["missing"] == []
    assert gaps["has_gap"] is True


def test_stale_only_session_is_its_own_category():
    """[WHY] AGENTS §2: "dòng bị lỗi/đã cách ly" khác "nguồn không có bar". Gộp chúng thành
    một con số "thiếu" sẽ khiến người trực đi bù một ngày mà nguồn đã trả dữ liệu cũ —
    bù lại vẫn ra dữ liệu cũ.
    """
    from src.database.session_continuity import detect_session_gaps

    con = _full_window("2026-11-05", 5, {"2026-11-04": (0, 900)})
    try:
        gaps = detect_session_gaps(con, "2026-11-05", lookback_sessions=5)
    finally:
        con.close()
    assert gaps["stale_only"] == ["2026-11-04"]
    assert gaps["missing"] == []
    assert gaps["partial"] == []


def test_holidays_are_never_reported_as_missing():
    """[WHY] Chống vá quá tay: nếu cửa sổ tính bằng ngày LỊCH thay vì phiên, kỳ nghỉ
    Quốc khánh 31/08–02/09 sẽ bị báo khuyết mỗi ngày và cảnh báo sẽ bị bỏ qua.
    """
    from src.database.session_continuity import detect_session_gaps

    con = _db([("2026-08-28", 900, 0), ("2026-09-03", 900, 0), ("2026-09-04", 900, 0)])
    try:
        gaps = detect_session_gaps(con, "2026-09-04", lookback_sessions=3)
    finally:
        con.close()
    assert gaps["has_gap"] is False, gaps
    assert gaps["checked_sessions"] == 3


def test_full_coverage_returns_no_gap():
    """[WHY] Chống vá quá tay theo hướng ngược: dữ liệu đầy đủ thì KHÔNG được báo động —
    cảnh báo luôn kêu sẽ bị bỏ qua (alarm fatigue).
    """
    from src.database.session_continuity import detect_session_gaps

    con = _db([("2026-11-03", 900, 0), ("2026-11-04", 900, 0), ("2026-11-05", 900, 0)])
    try:
        gaps = detect_session_gaps(con, "2026-11-05", lookback_sessions=3)
    finally:
        con.close()
    assert gaps["has_gap"] is False
    assert gaps["missing"] == []
    assert gaps["checked_sessions"] == 3


# ===================================================================
# 2. Hành vi: cảnh báo, KHÔNG backfill, KHÔNG làm chết daily-update
# ===================================================================
def test_alert_never_calls_backfill(monkeypatch):
    """[WHY] NQ-17 §IV chọn phương án (B). Test này là bản khóa: nếu một "cải tiến" sau này
    thêm tự động bù mạng vào đây, test đỏ ngay — vì tự bù che giấu sự cố vận hành và làm
    trễ hạn bản tin 15:30.
    """
    import src.daily_updater as du

    calls: list = []
    import src.services.market_backfill as mb

    monkeypatch.setattr(
        mb,
        "backfill_market_history",
        lambda *a, **k: calls.append((a, k)) or (0, 0, 0),
        raising=True,
    )

    con = _full_window("2026-11-05", 60, {"2026-11-04": (0, 0)})

    class _Ctx:
        def __enter__(self):
            return con

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(du, "get_connection", lambda *a, **k: _Ctx(), raising=True)
    report: dict = {}
    try:
        du.alert_session_gaps("2026-11-05", report)
    finally:
        con.close()

    assert report["session_gaps"]["missing"] == ["2026-11-04"]
    assert calls == [], f"alert_session_gaps KHÔNG được gọi backfill, nhưng đã gọi: {calls}"


def test_alert_failure_records_error_not_zero(monkeypatch):
    """[WHY] AGENTS §4A: "không biết" ≠ 0. Nếu quét lỗi mà ghi `session_gaps = {}` hay
    `missing = []`, hệ thống sẽ tưởng "không có lỗ hổng" — đúng kiểu fail-open đã gây ra
    sự cố 02/11.
    """
    import src.daily_updater as du

    def _boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(du, "get_connection", _boom, raising=True)
    report: dict = {}
    out = du.alert_session_gaps("2026-11-05", report)
    assert out is None
    assert report["session_gaps"] is None
    assert "database is locked" in report["session_gaps_error"]


def test_run_daily_update_wires_alert_before_any_crawl():
    """[WHY] Cảnh báo phải hiện ra TRƯỚC khi cào: nếu đặt sau phần cào 40 phút, người trực
    chỉ thấy sự cố khi đã quá muộn để kịp bản tin ATC.
    """
    import src.daily_updater as du

    src = inspect.getsource(du.run_daily_update)
    assert "alert_session_gaps(target_date, report)" in src
    assert src.index("alert_session_gaps(target_date, report)") < src.index("update_vnindex")


def test_detect_session_gaps_is_read_only():
    """[WHY] Cổng báo động không được phép ghi: một hàm "chỉ báo" mà lỡ ghi đè dữ liệu
    lịch sử sẽ tự tạo ra đúng loại restatement mà Chốt #5 đang phải canh.
    """
    from src.database.session_continuity import detect_session_gaps

    con = _db([("2026-11-05", 900, 0)])
    try:
        before = con.total_changes
        detect_session_gaps(con, "2026-11-05", lookback_sessions=2)
        assert con.total_changes == before
    finally:
        con.close()
