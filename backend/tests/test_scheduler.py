"""test_scheduler.py — Test Suite cho tiến trình EOD tự động (giai đoạn b).

Bao phủ 3 ràng buộc bắt buộc:
  1. Calendar-day fee (đã test ở test_production_gates — nhắc lại boundary).
  2. SQLite Locking chống Race Condition (2 luồng đồng thời).
  3. Idempotency (chạy lặp không nhân đôi).

Run: python -m pytest backend/tests/test_scheduler.py -v
"""
import concurrent.futures
import threading
import time

import pytest

from conftest import TEST_PORTFOLIO, TEST_SYMBOL, TEST_DATES

TEST_LOCK_KEY = "__TEST__EOD_LOCK"


@pytest.fixture
def clean_locks(clean_db):
    """Đảm bảo không còn lock test trước/sau."""
    from src.database.db_core import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM scheduler_locks WHERE lock_key LIKE ?",
                     ("__TEST__%",))
        conn.commit()
    yield
    with get_connection() as conn:
        conn.execute("DELETE FROM scheduler_locks WHERE lock_key LIKE ?",
                     ("__TEST__%",))
        conn.commit()


# ==================================================== SQLITE LOCKING
class TestSchedulerLock:
    """Chống Race Condition trên SQLite bằng atomic advisory lock."""

    def test_single_acquire_release(self, clean_locks):
        """Acquire → True, release → hàng bị xóa."""
        from src.engine.eod_runner import SchedulerLock
        from src.database.db_core import get_connection

        lock = SchedulerLock(lock_key=TEST_LOCK_KEY)
        assert lock.acquire() is True

        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM scheduler_locks WHERE lock_key=?",
                (TEST_LOCK_KEY,)).fetchone()[0]
        assert n == 1

        lock.release()
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM scheduler_locks WHERE lock_key=?",
                (TEST_LOCK_KEY,)).fetchone()[0]
        assert n == 0

    def test_second_acquire_blocked(self, clean_locks):
        """Luồng 2 KHÔNG chiếm được lock khi luồng 1 đang giữ."""
        from src.engine.eod_runner import SchedulerLock
        lock1 = SchedulerLock(lock_key=TEST_LOCK_KEY)
        lock2 = SchedulerLock(lock_key=TEST_LOCK_KEY)
        assert lock1.acquire() is True
        assert lock2.acquire() is False  # bị chặn (PK collision)
        lock1.release()
        # sau khi lock1 nhả, lock2 mới chiếm được
        assert lock2.acquire() is True
        lock2.release()

    def test_concurrent_acquire_only_one_wins(self, clean_locks):
        """RACE CONDITION: N luồng đồng thời → CHỈ 1 chiếm được lock.

        Đây là bằng chứng cốt lõi chống xung đột ghi khi 2 Scheduler
        vô tình khởi chạy đồng thời trên cùng file CSDL.
        """
        from src.engine.eod_runner import SchedulerLock

        N = 8
        barrier = threading.Barrier(N)
        results = []
        lock_holders = []

        def worker(idx):
            lock = SchedulerLock(lock_key=TEST_LOCK_KEY)
            barrier.wait()  # đồng bộ: tất cả cùng lao vào acquire 1 lúc
            ok = lock.acquire()
            results.append(ok)
            if ok:
                lock_holders.append(lock)
            return ok

        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            futures = [ex.submit(worker, i) for i in range(N)]
            concurrent.futures.wait(futures)

        # ĐÚNG 1 luồng thắng, N-1 luồng thua
        assert sum(results) == 1, f"Race: {sum(results)} luồng thắng (phải =1)"
        assert results.count(False) == N - 1
        for lk in lock_holders:
            lk.release()

    def test_stale_lock_reclaimed(self, clean_locks):
        """Lock hết hạn TTL (tiến trình chết) → luồng mới chiếm lại được."""
        from src.engine.eod_runner import SchedulerLock
        from src.database.db_core import get_connection
        from datetime import datetime, timedelta

        # Giả lập stale lock: expires_at trong quá khứ
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO scheduler_locks (lock_key, owner, acquired_at, expires_at) "
                "VALUES (?,?,?,?)",
                (TEST_LOCK_KEY, "dead_process", past, past))
            conn.commit()

        # Luồng mới phải dọn stale + chiếm được
        lock = SchedulerLock(lock_key=TEST_LOCK_KEY)
        assert lock.acquire() is True
        lock.release()

    def test_context_manager(self, clean_locks):
        """SchedulerLock dùng được as context manager (with ... as)."""
        from src.engine.eod_runner import SchedulerLock
        from src.database.db_core import get_connection
        with SchedulerLock(lock_key=TEST_LOCK_KEY) as lk:
            assert lk.ok is True
            with get_connection() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM scheduler_locks WHERE lock_key=?",
                    (TEST_LOCK_KEY,)).fetchone()[0]
            assert n == 1
        # sau khi thoát context → tự release
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM scheduler_locks WHERE lock_key=?",
                (TEST_LOCK_KEY,)).fetchone()[0]
        assert n == 0


# ==================================================== IDEMPOTENCY
class TestIdempotency:
    """Chạy lặp EOD không nhân đôi giao dịch / P&L."""

    def _seed_trade(self, date):
        """Bơm 1 paper trade + equity curve cho ngày date (namespace test)."""
        from src.database.db_core import get_connection
        # Đảm bảo schema tồn tại
        from src.engine.paper_trading_engine import PaperTradingEngine
        PaperTradingEngine(portfolio_id=TEST_PORTFOLIO)
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO paper_trades_log "
                "(trade_id, portfolio_id, decision_date, symbol, side, quantity, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (f"TID_{date}", TEST_PORTFOLIO, date, TEST_SYMBOL, "BUY", 100,
                 "2099-01-01T00:00:00"))
            conn.commit()

    def test_already_processed_detected(self, clean_db):
        """as_of_date đã có trade → _is_already_processed = True."""
        from src.engine.eod_runner import _is_already_processed
        date = TEST_DATES[0]
        assert _is_already_processed(date, TEST_PORTFOLIO) is False
        self._seed_trade(date)
        assert _is_already_processed(date, TEST_PORTFOLIO) is True

    def test_run_skips_when_already_processed(self, clean_db):
        """run_eod_pipeline SKIP khi đã xử lý (không chạy engine)."""
        from src.engine.eod_runner import run_eod_pipeline
        date = TEST_DATES[0]
        self._seed_trade(date)
        result = run_eod_pipeline(as_of_date=date, portfolio_id=TEST_PORTFOLIO)
        assert result["status"] == "SKIPPED"
        assert result["reason"] == "ALREADY_PROCESSED"

    def test_no_duplicate_trades_on_double_run(self, clean_db):
        """Chạy 2 lần → số trade KHÔNG nhân đôi."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import run_eod_pipeline
        date = TEST_DATES[0]
        self._seed_trade(date)

        def count_trades():
            with get_connection() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM paper_trades_log "
                    "WHERE portfolio_id=? AND decision_date=?",
                    (TEST_PORTFOLIO, date)).fetchone()[0]

        n0 = count_trades()
        run_eod_pipeline(as_of_date=date, portfolio_id=TEST_PORTFOLIO)
        run_eod_pipeline(as_of_date=date, portfolio_id=TEST_PORTFOLIO)
        assert count_trades() == n0  # không nhân đôi

    def test_lock_released_after_skip(self, clean_db):
        """Sau khi SKIP (idempotent), lock phải được nhả (không kẹt)."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import run_eod_pipeline, EOD_LOCK_KEY
        date = TEST_DATES[0]
        self._seed_trade(date)
        run_eod_pipeline(as_of_date=date, portfolio_id=TEST_PORTFOLIO)
        # Lock EOD không còn treo
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM scheduler_locks WHERE lock_key=?",
                (EOD_LOCK_KEY,)).fetchone()[0]
        assert n == 0, "Lock bị kẹt sau SKIP!"
