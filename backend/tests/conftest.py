"""conftest.py — Fixtures cho Test Suite hồi quy 3 chốt chặn Production.

Nguyên tắc cách ly (isolation):
  - Mọi test dùng portfolio_id/symbol/variable có tiền tố '__TEST__' để KHÔNG
    bao giờ đụng chạm dữ liệu paper trading / macro thật.
  - Fixture tự động dọn dẹp (teardown) sau mỗi test → DB luôn sạch.
  - Test chạy trên DB thật (screener_cache.db) vì engine hard-code connection,
    nhưng chỉ thao tác trên namespace test biệt lập.
"""
import sys
import pathlib

import pytest


def _hydrate_path():
    current = pathlib.Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root_path = current
            break
        current = current.parent
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.append(p)
    # Guarantee: PROJECT_ROOT at index 0 for core/ resolution
    sp = str(root_path)
    if sp in sys.path:
        sys.path.remove(sp)
    sys.path.insert(0, sp)
    return root_path


PROJECT_ROOT = _hydrate_path()

TEST_PORTFOLIO = "__TEST__PF"
TEST_SYMBOL = "__TESTSYM__"
TEST_MACRO_PREFIX = "__TEST__"

# Lịch giao dịch giả lập (đủ để test T+2.5 lifecycle) — dùng cho daily_ohlcv test.
TEST_DATES = [
    "2099-01-04", "2099-01-05", "2099-01-06", "2099-01-07",
    "2099-01-08", "2099-01-11", "2099-01-12", "2099-01-13",
]


def _clean_test_data():
    """Xóa mọi dữ liệu test khỏi DB (idempotent)."""
    from src.database.db_core import get_connection
    with get_connection() as conn:
        paper_tables = [
            "paper_portfolio_state", "paper_lots", "paper_cash_ledger",
            "paper_realized_pnl", "paper_equity_curve", "paper_trades_log",
            "paper_performance_daily", "paper_catchup_queue",
        ]
        for t in paper_tables:
            try:
                conn.execute(f"DELETE FROM {t} WHERE portfolio_id=?",
                             (TEST_PORTFOLIO,))
            except Exception:
                pass
        try:
            conn.execute("DELETE FROM paper_corporate_actions WHERE symbol=?",
                         (TEST_SYMBOL,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM daily_ohlcv WHERE symbol=?", (TEST_SYMBOL,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM macro_history WHERE variable LIKE ?",
                         (TEST_MACRO_PREFIX + "%",))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM eod_run_ledger WHERE portfolio_id=?",
                         (TEST_PORTFOLIO,))
        except Exception:
            pass
        conn.commit()


@pytest.fixture
def clean_db():
    """Đảm bảo DB sạch trước và sau test."""
    _clean_test_data()
    yield
    _clean_test_data()


@pytest.fixture
def seed_test_ohlcv(clean_db):
    """Bơm dữ liệu OHLCV giả lập cho TEST_SYMBOL trên TEST_DATES.

    Giá tăng dần đều để P&L dễ kiểm chứng. Trả về dict {date: close}.
    """
    from src.database.db_core import get_connection
    closes = {}
    base = 10000.0
    with get_connection() as conn:
        for i, d in enumerate(TEST_DATES):
            close = base + i * 100.0  # tăng 100đ/phiên
            open_ = close - 50.0
            closes[d] = close
            conn.execute(
                "INSERT OR REPLACE INTO daily_ohlcv "
                "(symbol, date, open, high, low, close, adj_close, volume, source) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (TEST_SYMBOL, d, open_, close + 100, open_ - 100, close,
                 close, 1_000_000, "TEST")
            )
        conn.commit()
    return closes


@pytest.fixture
def mtm(seed_test_ohlcv):
    """PaperMtM instance cách ly, vốn 1 tỷ."""
    from src.engine.paper_mtm import PaperMtM
    engine = PaperMtM(portfolio_id=TEST_PORTFOLIO,
                      initial_capital=1_000_000_000.0)
    return engine
