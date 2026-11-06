# -*- coding: utf-8 -*-
"""Chốt chặn "tính biến động qua ngày khuyết" (Mục 26, NQ-17 05/11/2026).

[WHY]
    Ngày 04/11/2026 EOD **không chạy** (cron miss) ⇒ `daily_ohlcv` không có bar 04/11.
    `eod-run --date 2026-11-05` lúc 15:42 vẫn hạch toán, ghi `paper_equity_curve` 05/11
    và ledger `SUCCESS`; phải tới 17:33 catch-up mới bù 04/11 (`CATCHUP_FULL`) rồi **ghi
    lại** equity 05/11. Trong khoảng 15:42–17:33, mọi phép so sánh "phiên này vs phiên
    trước" đang so 05/11 với 03/11 — tức gộp 2 phiên thành 1, đúng cơ chế sinh ra con số
    "+41 điểm ảo" mà Hội đồng đã cảnh báo.

[ROOT CAUSE]
    Tầng Python không có hàm nào trả lời "phiên liền trước của X là ngày nào và nó có
    thật trong `daily_ohlcv` không". Mỗi nơi tự suy diễn `date - 1 ngày` hoặc `shift(1)`,
    mù với cuối tuần, ngày lễ (`VN_HOLIDAYS`) và phantom holiday (2026-09-01). Rust đã có
    `expected_prev_trading_day`/`missing_trading_days_between`, Python thì chưa.

[INVARIANT]
    (a) "Phiên liền trước" = ngày làm việc gần nhất TRƯỚC X (không phải X−1);
    (b) lễ/phantom holiday không phải phiên ⇒ không bị báo "khuyết";
    (c) khuyết phiên liền trước ⇒ `EODSessionGapError`, KHÔNG hạch toán, ledger ghi FAILED;
    (d) cổng đứng SAU catch-up (catch-up có quyền bù trước) nhưng TRƯỚC mọi đường ghi.
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

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

LIVE_DB = BACKEND_DIR / "data" / "screener_cache.db"


# ===================================================================
# 1. Hàm thuần: "phiên liền trước" ≠ "hôm qua"
# ===================================================================
def test_previous_session_is_last_working_day():
    """[WHY] Lỗi gốc của mọi phép "1D": thứ Hai trừ 1 ngày ra Chủ nhật. Nếu hàm này sai,
    breadth/momentum sẽ tính qua 3 ngày lịch và gọi đó là "biến động 1 phiên".
    """
    from src.database.session_continuity import previous_session

    assert previous_session("2026-11-05") == "2026-11-04", "thứ Năm → thứ Tư"
    assert previous_session("2026-11-02") == "2026-10-30", "thứ Hai → thứ Sáu"
    assert previous_session("2026-11-09") == "2026-11-06", "thứ Hai kế tiếp → thứ Sáu"


def test_previous_session_skips_holidays_and_phantom_session():
    """[WHY] Chống vá quá tay theo hướng ngược: nếu coi 01/09 (phantom holiday) hoặc
    02/09 (Quốc khánh) là phiên, hệ thống sẽ báo "khuyết phiên" SAI vào đúng kỳ nghỉ và
    chặn EOD vô cớ.

    [SỬA 06/11/2026] Bản cũ khẳng định `previous_session("2026-09-03") == "2026-08-31"`,
    tức coi 31/08 là phiên. Sai: Rust (`src/main.rs:839`) xếp **cả** 31/08 vào `VN_HOLIDAYS`
    và có test `missing_trading_days_between("2026-08-31","2026-09-03") == []`; ba bảng dữ
    liệu đều trống 31/08. Phiên liền trước 03/09 là **28/08** (thứ Sáu).
    """
    from src.database.session_continuity import previous_session

    assert previous_session("2026-09-03") == "2026-08-28", "31/08 + 01/09 + 02/09 nghỉ → 28/08"
    assert previous_session("2026-04-28") == "2026-04-24", "27/04 lễ → 24/04 (thứ Sáu)"


def test_find_missing_sessions_detects_single_hole():
    """[WHY] Mô phỏng đúng sự cố 04/11: có 03/11 và 05/11, thiếu 04/11."""
    from src.database.session_continuity import find_missing_sessions

    missing = find_missing_sessions({"2026-11-03", "2026-11-05"}, "2026-11-02", "2026-11-06")
    assert missing == ["2026-11-04"], missing


def test_assert_raises_with_actionable_list():
    """[WHY] Thông điệp lỗi phải nêu TÊN ngày để người trực backfill đúng phiên; chỉ nói
    "thiếu dữ liệu" là vô dụng lúc 15:45 và dễ bị lờ đi.
    """
    from src.database.session_continuity import SessionGapError, assert_previous_session_present

    with pytest.raises(SessionGapError) as exc:
        assert_previous_session_present("2026-11-05", {"2026-11-03", "2026-11-05"})
    msg = str(exc.value)
    assert "2026-11-04" in msg
    assert "KHÔNG hạch toán" in msg


def test_assert_passes_when_prev_session_present():
    """[WHY] Chống vá quá tay: dữ liệu đầy đủ thì KHÔNG được chặn."""
    from src.database.session_continuity import assert_previous_session_present

    assert assert_previous_session_present("2026-11-05", {"2026-11-04", "2026-11-05"}) == "2026-11-04"


# ===================================================================
# 2. Cổng trong EOD pipeline (hành vi, không phải string match)
# ===================================================================
def _bars_only(tmp_path: Path, dates: list[str], symbols: int = 3) -> sqlite3.Connection:
    con = sqlite3.connect(str(tmp_path / "bars.db"))
    con.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
        "close REAL, adj_close REAL, volume REAL, source TEXT, is_stale INTEGER DEFAULT 0, "
        "UNIQUE(symbol, date))"
    )
    con.executemany(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (f"S{i}", d, 100.0, 101.0, 99.0, 100.0, 100.0, 1e6, "TEST", 0)
            for d in dates
            for i in range(symbols)
        ],
    )
    con.commit()
    return con


def _stub_pipeline(monkeypatch, conn, ledger_calls):
    import src.daily_updater as daily_updater
    import src.database.acid as acid
    import src.engine.eod_runner as eod
    import src.engine.paper_trading_engine as pte

    class _Txn:
        def __enter__(self):
            return (conn, SimpleNamespace(correlation_id="corr-gap"))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(acid, "global_transaction", lambda *a, **k: _Txn(), raising=True)
    monkeypatch.setattr(pte.PaperTradingEngine, "run_daily", staticmethod(lambda **k: {}))
    monkeypatch.setattr(daily_updater, "run_post_update_engines", lambda *a, **k: {})
    monkeypatch.setattr(eod, "_is_already_processed", lambda *a, **k: False, raising=True)
    monkeypatch.setattr(eod, "count_eod_bars", lambda *a, **k: 30, raising=True)
    monkeypatch.setattr(eod, "is_non_trading_day", lambda *a, **k: False, raising=True)
    monkeypatch.setattr(eod, "assert_eod_bars_present", lambda *a, **k: 30, raising=True)
    monkeypatch.setattr(eod, "_catchup_gap_days", lambda *a, **k: {"gap_days": []}, raising=True)
    monkeypatch.setattr(
        eod,
        "_record_ledger",
        lambda as_of, pid, status, **kw: ledger_calls.append({"as_of_date": as_of, "status": status}),
        raising=True,
    )
    return eod


def test_eod_refuses_when_previous_session_has_no_bars(monkeypatch, tmp_path):
    """[WHY] Tái hiện đúng 05/11/2026: DB chỉ có bar 05/11, thiếu 04/11 ⇒ `eod-run` phải
    DỪNG (EODSessionGapError) thay vì ghi equity + SUCCESS rồi bị catch-up sửa lại sau.
    """
    conn = _bars_only(tmp_path, ["2026-11-05"])
    ledger_calls: list[dict] = []
    eod = _stub_pipeline(monkeypatch, conn, ledger_calls)
    try:
        with pytest.raises(eod.EODSessionGapError) as exc:
            eod.run_eod_pipeline(as_of_date="2026-11-05", catchup=True)
        assert "2026-11-04" in str(exc.value)
    finally:
        conn.close()
    assert [c["status"] for c in ledger_calls] == [eod.STATUS_FAILED], ledger_calls


def test_eod_proceeds_when_previous_session_present(monkeypatch, tmp_path):
    """[WHY] Chống vá quá tay: có đủ 04/11 + 05/11 thì pipeline phải chạy bình thường và
    ghi SUCCESS — nếu cổng này chặn cả trường hợp bình thường thì EOD sẽ chết mỗi ngày.
    """
    conn = _bars_only(tmp_path, ["2026-11-04", "2026-11-05"])
    ledger_calls: list[dict] = []
    eod = _stub_pipeline(monkeypatch, conn, ledger_calls)
    try:
        result = eod.run_eod_pipeline(as_of_date="2026-11-05", catchup=True)
    finally:
        conn.close()
    assert result["status"] == "SUCCESS"
    assert [c["status"] for c in ledger_calls] == [eod.STATUS_SUCCESS]


def test_gap_gate_runs_after_catchup_before_accounting():
    """[WHY] Thứ tự là một phần của thiết kế: catch-up phải có quyền bù trước (nếu catch-up
    bù được 04/11 thì không cần chặn), nhưng cổng phải đứng TRƯỚC `PaperTradingEngine`.
    """
    import inspect

    from src.engine import eod_runner

    src = inspect.getsource(eod_runner.run_eod_pipeline)
    i_catchup = src.index("_catchup_gap_days")
    i_gate = src.index("_assert_previous_session_accounted")
    i_paper = src.index("PaperTradingEngine.run_daily")
    assert i_catchup < i_gate < i_paper


# ===================================================================
# 3. Bất biến trên CSDL THẬT (Sentinel cho lỗi 04/11)
# ===================================================================
def test_live_ohlcv_has_no_hole_before_latest_session():
    """[WHY] Chốt chặn thường trực: phiên mới nhất phải có phiên liền trước thật. Nếu cron
    lại miss một ngày, test này đỏ NGAY sáng hôm sau thay vì để EOD ghi số gộp phiên.
    """
    from src.database.session_continuity import assert_previous_session_present

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        present = {r[0] for r in con.execute("SELECT DISTINCT date FROM daily_ohlcv WHERE COALESCE(is_stale,0)=0")}
        latest = max(present)
    finally:
        con.close()
    assert assert_previous_session_present(latest, present) is not None
