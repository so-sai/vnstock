# -*- coding: utf-8 -*-
"""Cổng fail-closed "0 nến" của `eod-run` (Mục 19, NQ-14 03/11/2026).

[WHY]
    Ngày 02/11/2026 lúc 15:18, `ptck.py eod-run --date 2026-11-02` trả SUCCESS trong khi
    `daily_ohlcv` CHƯA có một nến nào của phiên đó. Hệ quả: `paper_equity_curve` sinh
    dòng 02/11 trùng khít dòng 30/10 (market_value 849.559.000, total 858.127.848) —
    một con số tài sản "ảo" chỉ lộ ra khi đối chiếu `created_at` (15:19:02) và thông báo
    "Ngày 2026-11-02 không có dữ liệu" của MARKET PULSE cùng lúc. Chỉ nhờ chạy lại
    `--force` lúc 15:25 thì equity thật (−2,99%) mới được ghi.

[ROOT CAUSE]
    `run_eod_pipeline()` không có bất kỳ kiểm tra nào về sự tồn tại của nến trước khi
    gọi `PaperTradingEngine.run_daily()`; engine MtM gặp panel rỗng thì lặng lẽ giữ
    nguyên giá trị phiên trước (carry-forward), và trạng thái vẫn là SUCCESS.

[INVARIANT]
    (a) Phiên giao dịch mà 0 nến ⇒ `EODNoBarsError`, KHÔNG ghi equity, ledger FAILED,
        CLI thoát mã ≠ 0 (3).
    (b) Ngày nghỉ (cuối tuần / phantom holiday) ⇒ SKIPPED NON_TRADING_SESSION — không
        hạch toán nhưng cũng không báo động giả ("không giao dịch" ≠ "thiếu dữ liệu").
    (c) Cổng phải đứng TRƯỚC idempotency để `--force` không lách được.
"""

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

# ── Path Setup (đồng bộ với conftest.py) ────────────────────────────────────
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


def _seed(tmp_path: Path, symbols: int = 3) -> sqlite3.Connection:
    """[WHY] Chỉ cần đủ để `COUNT(*)` phân biệt "có nến" và "0 nến" — cố ý không dựng
    panel lớn để test chạy < 1s và không phụ thuộc dữ liệu thị trường.
    """
    db = tmp_path / "eod_bars.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
        "close REAL, adj_close REAL, volume REAL, source TEXT, is_stale INTEGER DEFAULT 0, "
        "UNIQUE(symbol, date))"
    )
    con.executemany(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(f"S{i}", "2026-11-02", 100.0, 101.0, 99.0, 100.0, 100.0, 1e6, "TEST", 0) for i in range(symbols)],
    )
    con.commit()
    return con


def test_count_eod_bars_zero_on_empty_session(tmp_path):
    """[WHY] Cổng gốc của lỗi (a): 15:18 ngày 02/11 có 0 nến nhưng vẫn hạch toán."""
    from src.engine.eod_runner import count_eod_bars

    con = _seed(tmp_path)
    try:
        assert count_eod_bars("2026-11-02", conn=con) == 3
        assert count_eod_bars("2026-11-03", conn=con) == 0
    finally:
        con.close()


def test_assert_eod_bars_present_raises_on_trading_day(tmp_path):
    """[WHY] DoD Mục 19: phiên giao dịch mà 0 nến ⇒ EODNoBarsError, cấm ghi equity."""
    from src.engine.eod_runner import EODNoBarsError, assert_eod_bars_present

    con = _seed(tmp_path)
    try:
        assert assert_eod_bars_present("2026-11-02", conn=con) == 3
        with pytest.raises(EODNoBarsError):
            assert_eod_bars_present("2026-11-03", conn=con)
    finally:
        con.close()


def test_stale_only_bars_do_not_count():
    """[WHY] Chống vá quá tay: nến `is_stale=1` (bản sao/không xác nhận) KHÔNG được tính
    là dữ liệu EOD — nếu tính, cổng sẽ mở đúng vào lúc dữ liệu chưa chốt.
    """
    import tempfile

    from src.engine.eod_runner import count_eod_bars

    with tempfile.TemporaryDirectory() as td:
        con = _seed(Path(td), symbols=2)
        try:
            con.execute("UPDATE daily_ohlcv SET is_stale = 1")
            con.commit()
            assert count_eod_bars("2026-11-02", conn=con) == 0
        finally:
            con.close()


def test_non_trading_day_is_exempt_from_zero_bar_failure():
    """[WHY] Cuối tuần/phantom holiday không được biến thành FAILED, nhưng cũng không
    được hạch toán (AGENTS §2: phân biệt ba thứ khác nhau).
    """
    from src.engine.eod_runner import is_non_trading_day

    assert is_non_trading_day("2026-11-07") is True, "thứ Bảy"
    assert is_non_trading_day("2026-11-08") is True, "Chủ nhật"
    assert is_non_trading_day("2026-11-06") is False, "thứ Sáu — phiên giao dịch"
    assert is_non_trading_day("2026-09-01") is True, "phantom holiday đã xác nhận"


def test_eod_pipeline_guards_bars_before_accounting():
    """[WHY] Guard phải đứng TRƯỚC idempotency + trước khi PaperTradingEngine ghi equity;
    nếu chỉ đặt sau, đường carry-forward giả vẫn mở.
    """
    from src.engine import eod_runner

    src = inspect.getsource(eod_runner.run_eod_pipeline)
    assert "assert_eod_bars_present" in src
    i_guard = src.index("assert_eod_bars_present")
    assert i_guard < src.index("_is_already_processed")
    assert i_guard < src.index("PaperTradingEngine.run_daily")
