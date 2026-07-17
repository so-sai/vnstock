"""acid.py — Giao thức ACID cho luồng hạch toán EOD PTCK.

THIẾT KẾ PHÂN MẢNH LƯU TRỮ (Bifurcated Storage Architecture):

  Lõi Hạch toán (Accounting Core)  → screener_cache.db (SQLite/WAL)
    - paper_mtm, book_buy, process_settlements, apply_corporate_actions,
      mark_to_market, record_trade, catch-up queue.
    - TẤT CẢ chạy chung 1 Global Transaction (BEGIN IMMEDIATE).
    - Chịu ROLLBACK toàn phần nếu bất kỳ bút toán nào lỗi → bảo vệ tính
      nguyên tử (Atomicity), chống Double-entry Corruption khi cronjob tự
      chạy lại sau sự cố sập luồng giữa chừng.

  Lõi Viễn trắc (Telemetry Core)  → tệp phẳng .jsonl (append-only)
    - Tín hiệu chẩn đoán VQA / SEL / Macro Governor / stacktrace.
    - NẰM NGOÀI SQLite → SỐNG SÓT kể cả khi conn.rollback() dọn sạch
      bút toán kế toán lỗi. Dùng NumpyEncoder (bản vá fe05c2d).

  Sổ cái vận hành (Ledger Fallback) → screener_cache.db (kết nối RIÊNG)
    - eod_run_ledger ghi trạng thái FAILED/SUCCESS MỞ KẾT NỐI MỚI SAU KHI
      rollback() + nhả lock → tránh deadlock khóa ghi SQLite (WAL chỉ cho
      1 writer). TUYỆT ĐỐI không dùng chung cursor với Global Transaction.

  Concurrency Guard (Zero-Overhead Design):
    - KHÔNG dùng bảng khóa ứng dụng (scheduler_locks). Mọi cơ chế chống chạy
      đồng thời dựa trên khóa vật lý BEGIN IMMEDIATE của SQLite.
    - BEGIN IMMEDIATE giành RESERVED lock cấp tệp ngay lập tức. Tiến trình
      thứ hai văng sqlite3.OperationalError với error_code=6 (SQLITE_LOCKED)
      hoặc 5 (SQLITE_BUSY) → phân loại qua bitmask (raw_code & 0xFF).
    - Không Stale Lock: nếu tiến trình crash, SQLite rollback tự động giải
      phóng RESERVED lock trong mili-giây — không cần TTL 60 phút.
    - Idempotency (ngăn xử lý kép): eod_run_ledger đảm nhiệm.

Correlation ID: mã định danh động gắn xuyên suốt 1 phiên EOD, xuất hiện CẢ
trong telemetry .jsonl VÀ ledger fallback → người vận hành đối chiếu được
sự cố Rollback ở CSDL với luồng chẩn đoán VQA tương ứng trong log rời rạc.
"""
import gc
import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = None
for p in [Path(__file__).resolve().parent.parent.parent,
          Path(__file__).resolve().parent.parent]:
    if (p / "AGENTS.md").exists():
        PROJECT_ROOT = p
        break
if PROJECT_ROOT is None:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

from src.database.db_core import get_connection, safe_json_dumps  # noqa: E402

logger = logging.getLogger("PTCK_SYSTEM")

# Tệp telemetry viễn trắc (append-only, nằm ngoài SQLite)
TELEMETRY_DIR = PROJECT_ROOT / "backend" / "data" / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)

# --- Hằng số Error Code SQLite (Primary Codes, chuẩn https://sqlite.org/rescode.html) ---
# & 0xFF để lọc Extended Code về Primary Code
SQLITE_BUSY = 5       # database is locked (timeout)
SQLITE_LOCKED = 6     # database table is locked
SQLITE_IOERR = 10     # disk I/O error
SQLITE_CORRUPT = 11   # database disk image is malformed
SQLITE_FULL = 13      # database or disk is full
SQLITE_CANTOPEN = 14  # unable to open database file
SQLITE_NOTADB = 26    # file is not a database

LOCKED_CODES = {SQLITE_BUSY, SQLITE_LOCKED}
CRITICAL_CODES = {SQLITE_IOERR, SQLITE_CORRUPT, SQLITE_FULL, SQLITE_CANTOPEN, SQLITE_NOTADB}

# Legacy cleanup: xoá bảng scheduler_locks (nếu còn sót từ commit b24d21e)
_LEGACY_CLEANUP_DONE = False


def _drop_legacy_lock_table():
    global _LEGACY_CLEANUP_DONE
    if _LEGACY_CLEANUP_DONE:
        return
    try:
        with get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS scheduler_locks")
            conn.commit()
        _LEGACY_CLEANUP_DONE = True
        logger.info("[ACID] Đã xoá bảng legacy scheduler_locks.")
    except Exception:
        pass  # non-blocking; lần sau sẽ thử lại


class ResourceLockedException(Exception):
    """Tiến trình EOD khác đang chiếm khóa CSDL (concurrency guard). Không phải lỗi hệ thống."""
    pass


class TransactionTimeout(Exception):
    """Giao dịch vượt quá SLA thời gian cho phép → conn.interrupt() đã kích hoạt."""
    pass


# SLA: thời gian tối đa cho 1 Global Transaction (giây)
DEFAULT_TXN_TIMEOUT = 60.0


class CorrelationContext:
    """Mã định danh tương quan động cho 1 phiên EOD (luồng điều khiển cấp cao)."""

    def __init__(self, as_of_date: str, portfolio_id: str = "SEL_PAPER_V1"):
        self.correlation_id = uuid.uuid4().hex[:16]
        self.as_of_date = as_of_date
        self.portfolio_id = portfolio_id
        self.started_at = datetime.now().isoformat()

    def as_dict(self) -> dict:
        return {
            "correlation_id": self.correlation_id,
            "as_of_date": self.as_of_date,
            "portfolio_id": self.portfolio_id,
        }


# Context động — set bởi run_eod_pipeline, đọc bởi telemetry logger.
_ACTIVE_CORRELATION: Optional[CorrelationContext] = None


def set_correlation(ctx: Optional[CorrelationContext]):
    global _ACTIVE_CORRELATION
    _ACTIVE_CORRELATION = ctx


def get_correlation() -> Optional[CorrelationContext]:
    return _ACTIVE_CORRELATION


class TelemetryLogger:
    """Lõi Viễn trắc: ghi tín hiệu chẩn đoán ra .jsonl (sống sót rollback).

    Mỗi hàm ghi 1 dòng JSON Lines (append) vào tệp theo ngày. Dùng
    safe_json_dumps → chịu lỗi kiểu numpy. Gắn sẵn correlation_id để đối
    chiếu với sự cố Rollback trong CSDL kế toán.
    """

    def __init__(self, component: str):
        self.component = component
        self._fh = None
        self._path = None

    def _ensure_file(self):
        if self._fh is None or self._fh.closed:
            today = datetime.now().strftime("%Y-%m-%d")
            self._path = TELEMETRY_DIR / f"{self.component}_{today}.jsonl"
            self._fh = open(self._path, "a", encoding="utf-8")

    def emit(self, signal_type: str, payload: dict):
        """Ghi 1 sự kiện viễn trắc. Không ném lỗi (best-effort, luôn sống sót)."""
        try:
            self._ensure_file()
            rec = {
                "ts": datetime.now().isoformat(),
                "component": self.component,
                "signal": signal_type,
            }
            ctx = get_correlation()
            if ctx is not None:
                rec["correlation_id"] = ctx.correlation_id
                rec["as_of_date"] = ctx.as_of_date
            rec["payload"] = payload
            self._fh.write(safe_json_dumps(rec) + "\n")
            self._fh.flush()
        except Exception as e:  # telemetry tuyệt đối không làm sập luồng chính
            logger.warning(f"[TELEMETRY] emit thất bại ({self.component}): {e}")

    def close(self):
        if self._fh is not None and not self._fh.closed:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# Các logger mặc định theo tác nhân yêu cầu
vqa_telemetry = TelemetryLogger("vqa_diagnostics")
sel_telemetry = TelemetryLogger("sel_diagnostics")
macro_telemetry = TelemetryLogger("macro_governor")
exception_telemetry = TelemetryLogger("eod_exceptions")


@contextmanager
def global_transaction(as_of_date: str, portfolio_id: str = "SEL_PAPER_V1",
                       timeout_sec: float = DEFAULT_TXN_TIMEOUT):
    """Global Transaction Boundary — 1 kết nối, BEGIN IMMEDIATE, share cursor.

    Concurrency Guard dùng khóa vật lý SQLite (BEGIN IMMEDIATE), KHÔNG dùng
    bảng khóa ứng dụng. Phân loại lỗi qua sqlite_errorcode & 0xFF (bitmask):

      - SQLITE_LOCKED(6) / SQLITE_BUSY(5) → ResourceLockedException
        (tiến trình EOD khác đang chiếm khóa, có thể retry an toàn).
      - SQLITE_IOERR(10) / CORRUPT(11) / FULL(13) / CANTOPEN(14) / NOTADB(26)
        → SystemError (sự cố phần cứng/CSDL, cần can thiệp ngay).
      - "interrupted" (do conn.interrupt() từ kill-switch) → TransactionTimeout
        (giao dịch vượt quá timeout_sec, luồng phụ đã ngắt kết nối).

    Kill-switch: dùng threading.Timer + conn.interrupt() để ngắt giao dịch
    nếu vượt quá timeout_sec, portable trên Windows/Linux/macOS.

    Yields (conn, correlation_ctx). Toàn bộ hạch toán kế toán (settle → sinh
    lệnh → MtM → lưu lịch sử vốn) MUST dùng `conn` này, KHÔNG tự mở connection
    riêng, KHÔNG tự commit. Mọi ngoại lệ → ROLLBACK sạch 100% bút toán lỗi.

    Cách dùng:
        try:
            with global_transaction(date) as (conn, corr):
                engine.generate_orders_from_signals(date, conn=conn)
        except ResourceLockedException:
            handle_concurrency_gracefully()
        except TransactionTimeout:
            handle_sla_violation()
    """
    corr = CorrelationContext(as_of_date, portfolio_id)
    set_correlation(corr)
    _drop_legacy_lock_table()

    with get_connection() as conn:
        conn.isolation_level = None  # manual transaction control
        committed = False
        timer = None
        try:
            # BEGIN IMMEDIATE: giành RESERVED lock cấp tệp ngay.
            # Nếu tiến trình khác đang giữ lock → văng OperationalError.
            conn.execute("BEGIN IMMEDIATE")

            # Kill-switch: threading.Timer portable (Windows/Linux/macOS)
            def _kill_txn():
                logger.warning(
                    f"[ACID] KILL-SWITCH as_of={as_of_date} corr={corr.correlation_id}: "
                    f"quá {timeout_sec}s — gọi conn.interrupt().")
                conn.interrupt()

            timer = threading.Timer(timeout_sec, _kill_txn)
            timer.start()

            yield (conn, corr)

            timer.cancel()
            timer = None
            conn.execute("COMMIT")
            committed = True
            logger.info(f"[ACID] COMMIT as_of={as_of_date} corr={corr.correlation_id}")
        except sqlite3.OperationalError as e:
            try:
                if not committed:
                    conn.execute("ROLLBACK")
            except Exception:
                pass
            # Kiểm tra "interrupted" từ kill-switch TRƯỚC khi làm bitmask
            err_str = str(e).lower()
            if "interrupted" in err_str:
                logger.error(
                    f"[ACID] TIMEOUT as_of={as_of_date} corr={corr.correlation_id}: "
                    f"giao dịch vượt quá {timeout_sec}s.")
                exception_telemetry.emit("eod_timeout", {
                    "timeout_sec": timeout_sec,
                    "as_of_date": as_of_date,
                })
                gc.collect()  # Cưỡng chế dọn dẹp DataFrames/Numpy arrays rác sau kill-switch
                raise TransactionTimeout(
                    f"Giao dịch vượt quá SLA {timeout_sec}s — đã ngắt bởi kill-switch."
                ) from e
            # Phân loại lỗi qua bitmask (8 bit cuối = Primary Error Code)
            raw_code = getattr(e, 'sqlite_errorcode', -1)
            primary_code = raw_code & 0xFF if raw_code != -1 else -1

            if primary_code in LOCKED_CODES:
                logger.warning(
                    f"[ACID] CONCURRENCY GUARD as_of={as_of_date} corr={corr.correlation_id}: "
                    f"Tiến trình EOD khác đang chiếm khóa CSDL."
                )
                telemetry_logger = TelemetryLogger("eod_exceptions")
                telemetry_logger.emit("concurrency_guard", {
                    "error_code": primary_code,
                    "raw_code": raw_code,
                    "message": str(e),
                    "as_of_date": as_of_date,
                })
                raise ResourceLockedException(
                    "Tiến trình EOD khác đang chiếm khóa CSDL."
                ) from e
            elif primary_code in CRITICAL_CODES:
                logger.critical(
                    f"[ACID] FATAL I/O as_of={as_of_date} corr={corr.correlation_id}: "
                    f"PrimaryCode={primary_code} RawCode={raw_code}: {e}"
                )
                telemetry_logger = TelemetryLogger("eod_exceptions")
                telemetry_logger.emit("fatal_io_error", {
                    "error_code": primary_code,
                    "raw_code": raw_code,
                    "message": str(e),
                    "as_of_date": as_of_date,
                })
                raise SystemError(
                    f"FATAL I/O ERROR: Không thể tiếp tục vận hành. "
                    f"SQLite code={primary_code}: {e}"
                ) from e
            else:
                logger.error(
                    f"[ACID] UNCLASSIFIED SQLITE ERROR as_of={as_of_date} "
                    f"corr={corr.correlation_id}: PrimaryCode={primary_code}: {e}"
                )
                raise
        except Exception as e:
            try:
                if not committed:
                    conn.execute("ROLLBACK")
            except Exception:
                pass
            logger.error(
                f"[ACID] ROLLBACK as_of={as_of_date} corr={corr.correlation_id}: {e}")
            exception_telemetry.emit("eod_rollback", {
                "error": str(e),
                "error_type": type(e).__name__,
                "as_of_date": as_of_date,
            })
            raise
        finally:
            if timer is not None:
                timer.cancel()
            set_correlation(None)
    # conn tự động close khi thoát with get_connection()
