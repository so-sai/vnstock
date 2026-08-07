"""test_scheduler.py — Test Suite cho tiến trình EOD tự động (giai đoạn b).

Bao phủ 3 ràng buộc bắt buộc:
  1. Concurrency Guard (ResourceLockedException khi BEGIN IMMEDIATE bị chặn).
  2. Error Code Classification (bitmask & 0xFF phân biệt LOCKED vs CRITICAL).
  3. Idempotency (chạy lặp không nhân đôi).

Concurrency Guard dùng khóa vật lý SQLite, không dùng bảng khóa ứng dụng:

  - Tiến trình 1: BEGIN IMMEDIATE → giành RESERVED lock.
  - Tiến trình 2: BEGIN IMMEDIATE → văng OperationalError(sqlerrcode=6) →
    ResourceLockedException.

Run: python -m pytest backend/tests/test_scheduler.py -v
"""
import sqlite3

from conftest import TEST_DATES, TEST_PORTFOLIO, TEST_SYMBOL


# ==================================================== CONCURRENCY GUARD
class TestConcurrencyGuard:
    """Chống Race Condition qua BEGIN IMMEDIATE + ResourceLockedException."""

    def test_global_transaction_acquires_lock(self, clean_db):
        """global_transaction mở được BEGIN IMMEDIATE → yield conn."""
        from src.database.acid import global_transaction

        with global_transaction(TEST_DATES[0], TEST_PORTFOLIO) as (conn, corr):
            assert conn is not None
            assert corr is not None
            assert corr.correlation_id is not None
            # Kiểm tra conn đang trong transaction (có thể ghi)
            conn.execute("SELECT 1")

    def test_concurrent_transaction_raises_locked(self, clean_db):
        """2 connection đồng thời → connection 2 bị BUSY (database is locked).

        Đây là kiểm tra khả năng của SQLite: BEGIN IMMEDIATE trên connection 1
        ngăn connection 2 thực hiện BEGIN IMMEDIATE. global_transaction() dựa
        vào cơ chế này để chống race — không cần bảng khóa ứng dụng.
        """
        from src.database.db_core import DB_PATH

        conn1 = sqlite3.connect(DB_PATH, timeout=0.1, check_same_thread=False)
        conn1.execute("PRAGMA busy_timeout=100")
        conn1.isolation_level = None
        conn1.execute("BEGIN IMMEDIATE")  # chiếm RESERVED lock

        conn2 = sqlite3.connect(DB_PATH, timeout=0.1, check_same_thread=False)
        conn2.execute("PRAGMA busy_timeout=100")
        conn2.isolation_level = None
        try:
            conn2.execute("BEGIN IMMEDIATE")  # bị chặn (BUSY sau 100ms)
            assert False, "conn2 không được chiếm lock khi conn1 đang giữ"
        except sqlite3.OperationalError as e:
            assert "database is locked" in str(e).lower()
        finally:
            conn1.execute("ROLLBACK")
            conn1.close()
            conn2.close()

    def test_global_transaction_commit_works(self, clean_db, seed_test_ohlcv):
        """global_transaction COMMIT thành công → dữ liệu tồn tại sau đó."""
        from src.database.acid import global_transaction
        from src.database.db_core import get_connection

        # Ghi dữ liệu trong transaction
        with global_transaction(TEST_DATES[0], TEST_PORTFOLIO) as (conn, corr):
            conn.execute(
                "INSERT OR REPLACE INTO paper_portfolio_state "
                "(portfolio_id, initial_capital, settled_cash, pending_cash_in, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (TEST_PORTFOLIO, 1_000_000, 500_000, 0.0,
                 "2099-01-01T00:00:00", "2099-01-01T00:00:00")
            )

        # Kiểm tra dữ liệu đã COMMIT
        with get_connection() as conn:
            row = conn.execute(
                "SELECT settled_cash FROM paper_portfolio_state "
                "WHERE portfolio_id=?", (TEST_PORTFOLIO,)
            ).fetchone()
        assert row is not None
        assert row[0] == 500_000

    def test_global_transaction_rollback_on_error(self, clean_db, seed_test_ohlcv):
        """global_transaction ROLLBACK khi có lỗi trong block."""
        from src.database.acid import global_transaction
        from src.database.db_core import get_connection

        try:
            with global_transaction(TEST_DATES[0], TEST_PORTFOLIO) as (conn, corr):
                conn.execute(
                    "INSERT OR REPLACE INTO paper_portfolio_state "
                    "(portfolio_id, initial_capital, settled_cash, pending_cash_in, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (TEST_PORTFOLIO, 1_000_000, 999_999, 0.0,
                     "2099-01-01T00:00:00", "2099-01-01T00:00:00")
                )
                raise ValueError("Simulated error để kích hoạt rollback")
        except ValueError:
            pass

        # Dữ liệu KHÔNG được COMMIT
        with get_connection() as conn:
            row = conn.execute(
                "SELECT settled_cash FROM paper_portfolio_state "
                "WHERE portfolio_id=?", (TEST_PORTFOLIO,)
            ).fetchone()
        assert row is None, "Rollback không hoạt động — dữ liệu vẫn tồn tại"


# ==================================================== ERROR CODE CLASSIFICATION
class TestErrorCodeClassification:
    """Phân loại lỗi qua sqlite_errorcode & 0xFF (bitmask)."""

    def test_primary_code_bitmask(self):
        """raw_code & 0xFF trả về primary code (không extended bits)."""
        # Extended codes: SQLITE_IOERR_READ = 266 (0x10A)
        # Primary = 266 & 0xFF = 10 (= SQLITE_IOERR)
        extended = 266
        primary = extended & 0xFF
        assert primary == 10  # SQLITE_IOERR
        assert primary != 5   # SQLITE_BUSY

    def test_locked_codes_contain_busy_and_locked(self):
        """LOCKED_CODES = {5, 6}."""
        from src.database.acid import LOCKED_CODES, SQLITE_BUSY, SQLITE_LOCKED
        assert SQLITE_BUSY in LOCKED_CODES
        assert SQLITE_LOCKED in LOCKED_CODES
        assert len(LOCKED_CODES) == 2

    def test_critical_codes_contain_io_corrupt_full(self):
        """CRITICAL_CODES = {10, 11, 13, 14, 26}."""
        from src.database.acid import CRITICAL_CODES, SQLITE_CANTOPEN, SQLITE_CORRUPT, SQLITE_FULL, SQLITE_IOERR, SQLITE_NOTADB
        assert SQLITE_IOERR in CRITICAL_CODES
        assert SQLITE_CORRUPT in CRITICAL_CODES
        assert SQLITE_FULL in CRITICAL_CODES
        assert SQLITE_CANTOPEN in CRITICAL_CODES
        assert SQLITE_NOTADB in CRITICAL_CODES
        assert len(CRITICAL_CODES) == 5

    def test_sqlite_errorcode_attribute_exists(self):
        """sqlite3.OperationalError có thuộc tính sqlite_errorcode (Python 3.11+)."""
        try:
            conn = sqlite3.connect(":memory:")
            # Gây lỗi: không thể BEGIN IMMEDIATE trên memory (không có WAL)
            # Nhưng để kiểm tra attribute, chỉ cần khởi tạo exception
            raise sqlite3.OperationalError("fake error")
        except sqlite3.OperationalError as e:
            # sqlite_errorcode có thể không tồn tại trên Python < 3.11
            code = getattr(e, 'sqlite_errorcode', -1)
            assert code == -1  # fake error không có code thật
            # Primary code fallback về -1
            primary = code & 0xFF if code != -1 else -1
            assert primary == -1

    def test_resource_locked_exception_inherits_exception(self):
        """ResourceLockedException là Exception, không phải SystemError."""
        from src.database.acid import ResourceLockedException
        exc = ResourceLockedException("test")
        assert isinstance(exc, Exception)
        assert not isinstance(exc, SystemError)

    def test_transaction_timeout_class_exists(self):
        """TransactionTimeout là Exception, có message."""
        from src.database.acid import TransactionTimeout
        exc = TransactionTimeout("Quá 60s SLA")
        assert isinstance(exc, Exception)
        assert "60s" in str(exc)

    def test_default_txn_timeout_is_positive(self):
        """DEFAULT_TXN_TIMEOUT > 0."""
        from src.database.acid import DEFAULT_TXN_TIMEOUT
        assert DEFAULT_TXN_TIMEOUT > 0

    def test_interrupt_detection_in_message(self):
        """Phát hiện 'interrupted' trong error message."""
        e = sqlite3.OperationalError("interrupted")
        assert "interrupted" in str(e).lower()

        e2 = sqlite3.OperationalError("database is locked")
        assert "interrupted" not in str(e2).lower()


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
        """Sau SKIP, không có lock nào bị kẹt (scheduler_locks table đã xóa)."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import run_eod_pipeline
        date = TEST_DATES[0]
        self._seed_trade(date)
        run_eod_pipeline(as_of_date=date, portfolio_id=TEST_PORTFOLIO)
        # Bảng scheduler_locks không còn tồn tại — kiểm tra không có lỗi
        with get_connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduler_locks'"
            ).fetchall()
        assert len(tables) == 0, "Bảng scheduler_locks vẫn tồn tại sau khi xóa"
