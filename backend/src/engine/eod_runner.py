"""eod_runner.py — Tiến trình EOD 16:00 tự phục hồi + lũy đẳng + chống race.

Ba cơ chế bắt buộc cho Forward Testing integrity:

  1. SELF-HEALING RETRY (Ràng buộc 2):
     Bao bọc run_post_update_engines() trong retry: gặp lỗi kết nối → sleep
     15 phút, thử lại tối đa 3 lần, rồi mới phát báo động qua log.

  2. IDEMPOTENCY (Ràng buộc 3):
     Nếu as_of_date đã có trong paper_trades_log/paper_equity_curve → SKIP,
     tuyệt đối không nhân đôi giao dịch hoặc Unrealized P&L.

  3. SQLITE ADVISORY LOCK (câu hỏi khai thác sâu):
     Chống Race Condition khi 2 Scheduler chạy đồng thời. Dùng atomic INSERT
     vào scheduler_locks với PRIMARY KEY constraint + BEGIN IMMEDIATE:
       - INSERT thành công = chiếm lock (chỉ 1 luồng thắng do UNIQUE).
       - IntegrityError = luồng khác đang giữ lock → thoát an toàn.
       - Lock có TTL: stale lock (tiến trình chết) tự bị chiếm lại sau timeout.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config  # noqa: E402
from src.database.db_core import get_connection  # noqa: E402

logger = logging.getLogger("PTCK_SYSTEM")

# --- Cấu hình Retry & Lock ----------------------------------------------------
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 15 * 60      # 15 phút
LOCK_TTL_SECONDS = 60 * 60         # lock tự hết hạn sau 60 phút (chống stale)
EOD_LOCK_KEY = "EOD_PIPELINE"
DEFAULT_PORTFOLIO_ID = "SEL_PAPER_V1"


class SchedulerLock:
    """Advisory lock trên SQLite — atomic, chống Race Condition đa luồng.

    Nguyên lý (câu hỏi khai thác sâu):
      SQLite tuần tự hóa mọi ghi qua write-lock cấp file. Ta khai thác điều này:
      dùng `BEGIN IMMEDIATE` để giành write-lock NGAY (không đợi đến lúc COMMIT),
      rồi INSERT một hàng có PRIMARY KEY = lock_key. Do PRIMARY KEY là UNIQUE,
      nếu hai luồng cùng chạy, chỉ MỘT luồng INSERT thành công; luồng kia nhận
      IntegrityError → biết mình thua và thoát. Đây là compare-and-swap nguyên tử
      ở tầng lưu trữ, không cần khóa ngoài (file lock / OS mutex).
    """

    def __init__(self, lock_key: str = EOD_LOCK_KEY,
                 ttl_seconds: int = LOCK_TTL_SECONDS):
        self.lock_key = lock_key
        self.ttl_seconds = ttl_seconds
        self.owner = f"{os.getpid()}@{datetime.now().isoformat()}"
        self._acquired = False
        self._ensure_schema()

    def _ensure_schema(self):
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_locks (
                    lock_key    TEXT PRIMARY KEY,
                    owner       TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                )
            """)
            conn.commit()

    def acquire(self) -> bool:
        """Cố giành lock nguyên tử. Trả True nếu chiếm được, False nếu bị chiếm.

        Quy trình atomic:
          1. BEGIN IMMEDIATE → giành write-lock file NGAY (tuần tự hóa 2 luồng).
          2. Dọn lock hết hạn (stale) nếu có.
          3. INSERT lock_key. UNIQUE PK đảm bảo chỉ 1 luồng thành công.
          4. COMMIT → nhả write-lock file, giữ advisory lock (hàng trong bảng).
        """
        now = datetime.now()
        expires = now + timedelta(seconds=self.ttl_seconds)
        with get_connection() as conn:
            # Kiểm soát transaction thủ công (tắt autocommit của sqlite3)
            conn.isolation_level = None
            try:
                # BEGIN IMMEDIATE: giành RESERVED lock ngay → tuần tự hóa 2 luồng
                conn.execute("BEGIN IMMEDIATE")

                # Dọn stale lock (tiến trình chết giữa chừng, đã quá TTL)
                conn.execute(
                    "DELETE FROM scheduler_locks WHERE lock_key=? AND expires_at < ?",
                    (self.lock_key, now.isoformat())
                )

                # Atomic claim: INSERT raise IntegrityError nếu lock đang tồn tại
                conn.execute(
                    "INSERT INTO scheduler_locks (lock_key, owner, acquired_at, expires_at) "
                    "VALUES (?,?,?,?)",
                    (self.lock_key, self.owner, now.isoformat(), expires.isoformat())
                )
                conn.execute("COMMIT")
                self._acquired = True
                logger.info(f"[LOCK] Acquired '{self.lock_key}' by {self.owner}")
                return True
            except sqlite3.IntegrityError:
                # Luồng khác đang giữ lock (PK collision) → thua cuộc, thoát an toàn
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.warning(f"[LOCK] '{self.lock_key}' held by another process — skip.")
                return False
            except sqlite3.OperationalError as e:
                # 'database is locked' — luồng khác đang trong BEGIN IMMEDIATE
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.warning(f"[LOCK] Busy acquiring '{self.lock_key}': {e} — skip.")
                return False

    def release(self):
        """Nhả lock (chỉ owner mới xóa được hàng của mình)."""
        if not self._acquired:
            return
        try:
            with get_connection() as conn:
                conn.execute(
                    "DELETE FROM scheduler_locks WHERE lock_key=? AND owner=?",
                    (self.lock_key, self.owner)
                )
                conn.commit()
            logger.info(f"[LOCK] Released '{self.lock_key}'")
        except Exception as e:
            logger.warning(f"[LOCK] Release failed: {e}")
        finally:
            self._acquired = False

    def __enter__(self):
        self.ok = self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def _is_already_processed(as_of_date: str,
                          portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> bool:
    """Idempotency check: as_of_date đã được xử lý chưa?

    Kiểm tra CẢ paper_trades_log (có lệnh) LẪN paper_equity_curve (đã MtM).
    Nếu bất kỳ dấu vết nào tồn tại → coi như đã xử lý → SKIP.
    """
    with get_connection() as conn:
        try:
            n_trades = conn.execute(
                "SELECT COUNT(*) FROM paper_trades_log "
                "WHERE portfolio_id=? AND decision_date=?",
                (portfolio_id, as_of_date)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n_trades = 0
        try:
            n_equity = conn.execute(
                "SELECT COUNT(*) FROM paper_equity_curve "
                "WHERE portfolio_id=? AND date=?",
                (portfolio_id, as_of_date)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n_equity = 0
    return (n_trades > 0) or (n_equity > 0)


def _resolve_eod_date() -> str:
    """Ngày EOD = ngày dữ liệu mới nhất trong daily_ohlcv (offline, không API)."""
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
    return row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")


def run_eod_pipeline(as_of_date: Optional[str] = None,
                     portfolio_id: str = DEFAULT_PORTFOLIO_ID,
                     max_retries: int = MAX_RETRIES,
                     retry_sleep: int = RETRY_SLEEP_SECONDS,
                     force: bool = False) -> Dict:
    """Điểm vào EOD tự phục hồi + lũy đẳng + chống race.

    Args:
      as_of_date: ngày xử lý. None → EOD mới nhất trong DB.
      force: True → bỏ qua idempotency check (chạy lại có chủ đích).

    Returns: dict trạng thái {status, reason, ...}.
    """
    if as_of_date is None:
        as_of_date = _resolve_eod_date()

    result = {"as_of_date": as_of_date, "portfolio_id": portfolio_id,
              "timestamp": datetime.now().isoformat()}

    # --- 1. SQLITE ADVISORY LOCK (chống race condition) ---
    lock = SchedulerLock(lock_key=EOD_LOCK_KEY)
    if not lock.acquire():
        result.update({"status": "SKIPPED", "reason": "LOCK_HELD",
                       "note": "Tiến trình EOD khác đang chạy — bỏ qua an toàn."})
        logger.warning(f"[EOD] {as_of_date}: lock held — skipped (anti-race).")
        return result

    try:
        # --- 2. IDEMPOTENCY (chống nhân đôi) ---
        if not force and _is_already_processed(as_of_date, portfolio_id):
            result.update({"status": "SKIPPED", "reason": "ALREADY_PROCESSED",
                           "note": f"{as_of_date} đã xử lý — không chạy lại."})
            logger.info(f"[EOD] {as_of_date}: already processed — skipped (idempotent).")
            return result

        # --- 3. SELF-HEALING RETRY ---
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[EOD] {as_of_date}: attempt {attempt}/{max_retries}")
                from src.daily_updater import run_post_update_engines
                engine_results = run_post_update_engines()
                result.update({"status": "SUCCESS", "attempt": attempt,
                               "engine_results": engine_results})
                logger.info(f"[EOD] {as_of_date}: SUCCESS on attempt {attempt}")
                return result
            except (ConnectionError, TimeoutError, OSError, sqlite3.OperationalError) as e:
                last_error = str(e)
                logger.warning(
                    f"[EOD] {as_of_date}: attempt {attempt} failed "
                    f"(connection): {e}")
                if attempt < max_retries:
                    logger.info(f"[EOD] Sleeping {retry_sleep}s before retry...")
                    time.sleep(retry_sleep)
            except Exception as e:
                # Lỗi không phải kết nối → không retry, báo động ngay
                last_error = str(e)
                logger.exception(f"[EOD] {as_of_date}: non-recoverable error: {e}")
                result.update({"status": "FAILED", "reason": "NON_RECOVERABLE",
                               "error": last_error})
                return result

        # Hết retry → phát báo động
        logger.error(
            f"[EOD] {as_of_date}: ALARM — failed after {max_retries} retries. "
            f"Last error: {last_error}")
        result.update({"status": "FAILED", "reason": "MAX_RETRIES_EXCEEDED",
                       "error": last_error, "attempts": max_retries})
        return result
    finally:
        lock.release()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="PTCK EOD Runner (self-healing)")
    parser.add_argument("--date", default=None, help="as_of_date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true",
                        help="Bỏ qua idempotency check")
    parser.add_argument("--retry-sleep", type=int, default=RETRY_SLEEP_SECONDS,
                        help="Giây ngủ giữa các lần retry")
    args = parser.parse_args()
    out = run_eod_pipeline(as_of_date=args.date, force=args.force,
                           retry_sleep=args.retry_sleep)
    print(out)
