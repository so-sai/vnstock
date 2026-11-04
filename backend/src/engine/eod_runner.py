"""eod_runner.py — Tiến trình EOD 16:00 tự phục hồi + lũy đẳng + chống race.

Ba cơ chế bắt buộc cho Forward Testing integrity:

  1. SELF-HEALING RETRY (Ràng buộc 2):
     Bao bọc run_post_update_engines() trong retry: gặp lỗi kết nối → sleep
     15 phút, thử lại tối đa 3 lần, rồi mới phát báo động qua log.

  2. IDEMPOTENCY (Ràng buộc 3):
     Nếu as_of_date đã có trong paper_trades_log/paper_equity_curve → SKIP,
     tuyệt đối không nhân đôi giao dịch hoặc Unrealized P&L.

  3. CONCURRENCY GUARD (Zero-Overhead Design):
     Chống Race Condition khi 2 Scheduler chạy đồng thời. Dùng khóa vật lý
     BEGIN IMMEDIATE của SQLite — khóa cấp tệp giành ngay lập tức, tiến trình
     thứ hai văng ResourceLockedException. KHÔNG dùng bảng khóa ứng dụng,
     không có Stale Lock (SQLite tự rollback khi crash).
"""

import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
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
from src.database.db_core import get_connection, safe_json_dumps

logger = logging.getLogger("PTCK_SYSTEM")


class EODNoBarsError(RuntimeError):
    """[WHY] EOD của một phiên KHÔNG có nến là lỗi dữ liệu, không phải "phiên trống".

    [ROOT CAUSE] Trước 03/11/2026, `run_eod_pipeline` không kiểm tra số nến: lần chạy
    15:18 ngày 02/11/2026 (khi `daily-update` chưa nạp bar nào) vẫn trả SUCCESS và ghi
    `paper_equity_curve` 02/11 TRÙNG KHÍT dòng 30/10 (858.127.848) — equity giả, chỉ lộ
    ra khi nhìn `created_at` (15:19:02) và `market_pulse` cùng lúc báo "không có dữ liệu".

    [INVARIANT] Không nến ⇒ không hạch toán, không ghi equity; ledger ghi FAILED để
    catch-up biết đây là ngày nợ. Ngày nghỉ (cuối tuần/phantom holiday) được miễn trừ
    TƯỜNG MINH — "không giao dịch" khác "thiếu dữ liệu" (AGENTS §2).
    """


def count_eod_bars(as_of_date: str, conn=None) -> int:
    """Số nến hợp lệ (`is_stale = 0`) của một phiên."""
    sql = "SELECT COUNT(*) FROM daily_ohlcv WHERE date = ? AND COALESCE(is_stale, 0) = 0"
    if conn is not None:
        row = conn.execute(sql, (as_of_date,)).fetchone()
        return int(row[0]) if row else 0
    with get_connection() as c:
        row = c.execute(sql, (as_of_date,)).fetchone()
    return int(row[0]) if row else 0


def is_non_trading_day(as_of_date: str) -> bool:
    """[WHY] Phân biệt "không có bar vì nghỉ" với "nguồn không có bar" (AGENTS §2):
    cuối tuần / phantom holiday không được biến thành FAILED, nhưng cũng không được
    hạch toán như một phiên có giao dịch.
    """
    from datetime import date as _date

    from src.database.panel_quality import is_non_trading_session

    if is_non_trading_session(as_of_date):
        return True
    return _date.fromisoformat(as_of_date).weekday() >= 5


def assert_eod_bars_present(as_of_date: str, conn=None) -> int:
    """Cổng fail-closed của Mục 19: 0 nến ⇒ ném `EODNoBarsError`."""
    n = count_eod_bars(as_of_date, conn=conn)
    if n == 0:
        raise EODNoBarsError(f"FAIL-CLOSED: không có nến EOD nào cho {as_of_date} — cấm ghi nhận equity curve giả mạo.")
    return n


# --- Cấu hình Retry -----------------------------------------------------------
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 15 * 60  # 15 phút
DEFAULT_PORTFOLIO_ID = "SEL_PAPER_V1"

# --- Cấu hình Catch-up (Giao dịch bù) ----------------------------------------
# Số PHIÊN tối đa mà tín hiệu giao dịch còn được coi là hợp lệ để "bù đầy đủ".
# Ngày nợ cách hiện tại > ngưỡng này → STALE: chỉ bù MtM/settlement/corp-action,
# TUYỆT ĐỐI không tái tạo lệnh mua/bán cũ (chống backfill bias & lệnh lỗi thời).
MAX_CATCHUP_DAYS = 3
# Trần an toàn: không bao giờ quét quá xa để tránh "bù" cả lịch sử khi DB mới.
CATCHUP_SCAN_LIMIT = 30
# Ngưỡng liền mạch (calendar days): một phiên chỉ được coi là "ngày nợ" nếu nó
# cách as_of KHÔNG quá ngưỡng này. Cách xa hơn = triển khai mới / nghỉ dài, KHÔNG
# phải EOD bị lỡ → tránh bù nhầm cả kho lịch sử khi khởi tạo hệ thống.
CATCHUP_MAX_CALENDAR_GAP = 30


# --- Ledger status codes ------------------------------------------------------
STATUS_SUCCESS = "SUCCESS"  # chạy đầy đủ (settle + lệnh + MtM)
STATUS_FAILED = "FAILED"  # thất bại sau retry (nợ, chờ bù)
STATUS_PENDING_CATCHUP = "PENDING_CATCHUP"  # đã đánh dấu là ngày nợ chờ bù
STATUS_CATCHUP_FULL = "CATCHUP_FULL"  # bù đầy đủ (còn trong cửa sổ tín hiệu)
STATUS_CATCHUP_MTM_ONLY = "CATCHUP_MTM_ONLY"  # bù stale: chỉ MtM/settlement


def _is_already_processed(as_of_date: str, portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> bool:
    """Idempotency check: as_of_date đã được xử lý chưa?

    Kiểm tra CẢ paper_trades_log (có lệnh) LẪN paper_equity_curve (đã MtM).
    Nếu bất kỳ dấu vết nào tồn tại → coi như đã xử lý → SKIP.
    """
    with get_connection() as conn:
        try:
            n_trades = conn.execute(
                "SELECT COUNT(*) FROM paper_trades_log WHERE portfolio_id=? AND decision_date=?", (portfolio_id, as_of_date)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n_trades = 0
        try:
            n_equity = conn.execute(
                "SELECT COUNT(*) FROM paper_equity_curve WHERE portfolio_id=? AND date=?", (portfolio_id, as_of_date)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n_equity = 0
    return (n_trades > 0) or (n_equity > 0)


def _resolve_eod_date() -> str:
    """Ngày EOD = ngày dữ liệu mới nhất trong daily_ohlcv (offline, không API)."""
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
    return row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")


# ============================================================================
#  EOD RUN LEDGER — Sổ cái vận hành bền vững (persistent memory cho Catch-up)
# ============================================================================
LEDGER_RETRIES = 5
# [WHY] 5 lần, backoff 0,2 → 0,4 → 0,8 → 1,6 → 3,2s (tổng ~6,2s): đủ dài để vượt một
# phiên ghi ngắn của cron/agent khác, đủ ngắn để ledger fallback không treo EOD.
LEDGER_BASE_DELAY = 0.2


def _ensure_ledger_schema(conn=None):
    """Tạo bảng eod_run_ledger nếu chưa có.

    Đây là 'bộ nhớ dài hạn' của scheduler: mỗi phiên EOD ghi lại kết quả.
    Nhờ nó, ngày T+1 biết chắc ngày T có thất bại (nợ) không — thay vì chỉ
    dựa vào log dễ mất. Là nền tảng cho Gap Detection + Sequential Backfill.

    [WHY] Nhận `conn` của transaction: `CREATE TABLE IF NOT EXISTS` là DDL (cần write
    lock). Mở kết nối thứ hai để DDL trong lúc transaction đang giữ `BEGIN IMMEDIATE`
    sẽ chờ tới hết `busy_timeout` rồi ném "database is locked" — đúng cơ chế đã làm
    catch-up chết và `eod_run_ledger` đứng yên từ 16/07/2026.
    [INVARIANT] Conn do transaction sở hữu thì KHÔNG commit ở đây (transaction tự
    COMMIT/ROLLBACK); chỉ commit khi hàm tự mở kết nối.
    """
    ddl = """
        CREATE TABLE IF NOT EXISTS eod_run_ledger (
            as_of_date   TEXT NOT NULL,
            portfolio_id TEXT NOT NULL,
            status       TEXT NOT NULL,
            attempts     INTEGER DEFAULT 0,
            last_error   TEXT,
            catchup_of   TEXT,           -- NULL nếu chạy đúng ngày; else = ngày gốc nợ
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (as_of_date, portfolio_id)
        )
    """
    if conn is not None:
        conn.execute(ddl)
        return
    with get_connection() as conn_own:
        conn_own.execute(ddl)
        conn_own.commit()


def _ledger_write(
    conn,
    as_of_date: str,
    portfolio_id: str,
    status: str,
    attempts: int,
    last_error: str | None,
    catchup_of: str | None,
    commit: bool,
) -> None:
    """UPSERT một dòng ledger + audit trên `conn` cho trước.

    [WHY] Tách khỏi `_record_ledger` để đường TRONG transaction (conn của Global
    Transaction) và đường fallback (kết nối riêng, có retry) dùng chung đúng một câu
    SQL. Nếu hai đường chép SQL riêng, chúng sẽ trôi khỏi nhau (bài học Mục 20).
    [INVARIANT] `commit=False` khi `conn` thuộc transaction: commit giữa chừng sẽ phá
    ROLLBACK toàn cục và để lại bút toán dở dang.
    """
    _ensure_ledger_schema(conn)
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO eod_run_ledger
            (as_of_date, portfolio_id, status, attempts, last_error,
             catchup_of, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(as_of_date, portfolio_id) DO UPDATE SET
            status=excluded.status,
            attempts=excluded.attempts,
            last_error=excluded.last_error,
            catchup_of=excluded.catchup_of,
            updated_at=excluded.updated_at
    """,
        (as_of_date, portfolio_id, status, attempts, last_error, catchup_of, now, now),
    )
    try:
        from src.database.eod_write_audit import record_eod_write_audit

        record_eod_write_audit(
            as_of_date=as_of_date,
            caller_module=f"eod_runner.{portfolio_id}",
            target_table="eod_run_ledger",
            parameters={"status": status, "attempts": attempts, "catchup_of": catchup_of},
            input_data={"status": status, "last_error": last_error},
            output_score=None,
            status=status,
            notes=f"EOD runner ledger update: {last_error or 'SUCCESS'}",
            conn=conn,
            commit=commit,
        )
    except (sqlite3.Error, TypeError, ValueError) as _e_audit:
        logger.debug("Audit write skipped: %s", _e_audit)


def _record_ledger(
    as_of_date: str,
    portfolio_id: str,
    status: str,
    attempts: int = 0,
    last_error: str | None = None,
    catchup_of: str | None = None,
    conn=None,
    retries: int | None = None,
    base_delay: float | None = None,
):
    """Ghi/cập nhật trạng thái một phiên EOD vào sổ cái (idempotent UPSERT).

    [WHY] `eod_run_ledger` đứng yên ở 16/07/2026 (23 dòng) suốt 3,5 tháng vì hàm này
    luôn mở KẾT NỐI RIÊNG, kể cả khi được gọi từ trong `global_transaction()`
    (`BEGIN IMMEDIATE`) — writer thứ hai chờ hết `busy_timeout` rồi ném
    "database is locked", và `_catchup_gap_days` nuốt lỗi đó (non-blocking) nên
    không ai biết. Hai chế độ:
      - `conn` được truyền (đang trong transaction) ⇒ ghi trên chính conn đó,
        KHÔNG commit, KHÔNG retry (khóa là của chính mình, retry vô nghĩa).
      - `conn=None` (ledger fallback sau rollback, hoặc gọi rời) ⇒ kết nối riêng,
        retry exponential backoff CHỈ với lỗi khóa.
    [INVARIANT] Lỗi không phải khóa (schema/data) phải ném NGAY — retry sẽ chỉ làm
    chậm pipeline và che giấu lỗi thật.
    """
    if conn is not None:
        _ledger_write(conn, as_of_date, portfolio_id, status, attempts, last_error, catchup_of, commit=False)
        return

    retries = LEDGER_RETRIES if retries is None else retries
    base = LEDGER_BASE_DELAY if base_delay is None else base_delay
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        try:
            with get_connection() as conn_own:
                _ledger_write(conn_own, as_of_date, portfolio_id, status, attempts, last_error, catchup_of, commit=True)
            return
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last_exc = exc
            logger.warning(
                "[LEDGER] %s/%s: khóa SQLite (lần %d/%d) — thử lại sau %.1fs",
                as_of_date,
                portfolio_id,
                attempt + 1,
                retries,
                base * (2**attempt),
            )
            if attempt + 1 < retries:
                time.sleep(base * (2**attempt))
    assert last_exc is not None  # chỉ tới đây khi đã có ít nhất 1 lỗi khóa
    raise last_exc


def _ledger_succeeded(as_of_date: str, portfolio_id: str, conn=None) -> bool:
    """Ngày này đã có kết quả 'xử lý xong' trong ledger chưa?

    Coi là xong nếu status thuộc {SUCCESS, CATCHUP_FULL, CATCHUP_MTM_ONLY}.

    [WHY] Nhận `conn` để lời gọi từ trong transaction không mở kết nối thứ hai
    (đọc thì không tranh khóa ghi, nhưng giữ một đường duy nhất dễ kiểm toán hơn).
    """
    done = (STATUS_SUCCESS, STATUS_CATCHUP_FULL, STATUS_CATCHUP_MTM_ONLY)
    if conn is not None:
        _ensure_ledger_schema(conn)
        row = conn.execute(
            "SELECT status FROM eod_run_ledger WHERE as_of_date=? AND portfolio_id=?", (as_of_date, portfolio_id)
        ).fetchone()
        return bool(row) and row[0] in done
    _ensure_ledger_schema()
    with get_connection() as conn_own:
        row = conn_own.execute(
            "SELECT status FROM eod_run_ledger WHERE as_of_date=? AND portfolio_id=?", (as_of_date, portfolio_id)
        ).fetchone()
    return bool(row) and row[0] in done


def _trading_sessions_up_to(as_of_date: str, limit: int) -> list:
    """Danh sách 'limit' phiên giao dịch GẦN NHẤT (<= as_of_date), tăng dần.

    Nguồn sự thật: các ngày distinct trong daily_ohlcv (lịch giao dịch thực tế,
    đã trừ cuối tuần/lễ). Offline — không gọi API.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date <= ? ORDER BY date DESC LIMIT ?", (as_of_date, limit)
        ).fetchall()
    return sorted(r[0] for r in rows)


def detect_gap_days(
    as_of_date: str,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
    scan_limit: int = CATCHUP_SCAN_LIMIT,
    conn=None,
) -> list:
    """GAP DETECTION: các phiên < as_of_date CHƯA được xử lý xong (ngày nợ).

    Quét 'scan_limit' phiên gần nhất TRƯỚC as_of_date, lọc ra những phiên
    KHÔNG có dấu vết hoàn tất (ledger chưa SUCCESS/CATCHUP và cũng chưa có
    paper trade/equity). Trả về danh sách TĂNG DẦN để bù đúng thứ tự thời gian.

    Chỉ tính ngày nợ TRƯỚC as_of_date (ngày hiện tại xử lý riêng ở luồng chính).

    [WHY] Nhận `conn` của transaction: hàm này GHI ledger (đồng bộ trạng thái cho
    phiên có trade/equity nhưng ledger trống) ⇒ phải dùng cùng writer, nếu không
    lại tái hiện "database is locked" (Mục 21).
    """
    sessions = _trading_sessions_up_to(as_of_date, scan_limit)
    gaps = []
    _as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    for d in sessions:
        if d >= as_of_date:
            continue
        # CONTIGUITY GUARD: bỏ qua phiên cách as_of quá xa (triển khai mới /
        # nghỉ dài) — không phải EOD bị lỡ, tránh bù nhầm cả kho lịch sử.
        try:
            gap_days = (_as_of_dt - datetime.strptime(d, "%Y-%m-%d")).days
        except ValueError:
            continue
        if gap_days > CATCHUP_MAX_CALENDAR_GAP:
            continue
        # Đã xử lý xong (ledger) hoặc đã có dấu vết trade/equity → không nợ
        if _ledger_succeeded(d, portfolio_id, conn=conn):
            continue
        if _is_already_processed(d, portfolio_id):
            # Có trade/equity nhưng ledger trống (chạy từ trước khi có ledger)
            # → coi như xong, đồng bộ ledger để lần sau khỏi quét lại
            _record_ledger(d, portfolio_id, STATUS_SUCCESS, conn=conn)
            continue
        gaps.append(d)
    return gaps


def _catchup_gap_days(as_of_date: str, portfolio_id: str, offline: bool = True, conn=None) -> dict:
    """SEQUENTIAL BACKFILL: bù tuần tự các ngày nợ TRƯỚC as_of_date.

    Cơ chế Stale-Signal Guard:
      - Ngày nợ cách 'as_of_date' <= MAX_CATCHUP_DAYS phiên → BÙ ĐẦY ĐỦ
        (phát lệnh theo as_of của ĐÚNG ngày đó → dùng giá/normalizer lịch sử,
        KHÔNG lookahead). Ghi ledger CATCHUP_FULL.
      - Xa hơn → STALE: chỉ hạch toán MtM/settlement/corp-action (mtm_only),
        KHÔNG tái tạo lệnh cũ. Ghi ledger CATCHUP_MTM_ONLY + cảnh báo con người.

    ACID: nhận conn (Global Transaction) → mọi ghi kế toán chạy chung cursor,
    chịu ROLLBACK nếu lỗi. Sổ cái vận hành (`_record_ledger`) cũng ghi trên
    CHÍNH conn đó — trước 03/11/2026 nó mở kết nối riêng và chết vì
    "database is locked" (Mục 21); ledger fallback sau transaction mới retry.

    Xử lý TĂNG DẦN theo thời gian để dòng tiền/settlement diễn ra đúng trình tự.
    """
    gaps = detect_gap_days(as_of_date, portfolio_id, conn=conn)
    report = {"gap_days": gaps, "caught_up": [], "mtm_only": [], "errors": []}

    from src.engine.paper_trading_engine import PaperTradingEngine

    if gaps:
        logger.warning(f"[CATCHUP] Phát hiện {len(gaps)} ngày nợ: {gaps}")

        # Cửa sổ tín hiệu còn hiệu lực: MAX_CATCHUP_DAYS phiên gần nhất TRƯỚC as_of
        recent_sessions = _trading_sessions_up_to(as_of_date, MAX_CATCHUP_DAYS + 1)
        fresh_window = set(d for d in recent_sessions if d < as_of_date)

        for d in gaps:  # đã sorted tăng dần
            is_fresh = d in fresh_window
            try:
                if is_fresh:
                    # BÙ ĐẦY ĐỦ — as_of=d → anti-lookahead tự động qua kiến trúc as_of.
                    # CATCH-UP EXECUTION RULE: tín hiệu tính tại d nhưng lệnh KHÔNG
                    # khớp giá lịch sử — nạp vào hàng đợi, sẽ ép khớp @ open ngày
                    # phục hồi (as_of_date) qua process_catchup_queue (chống lookback).
                    PaperTradingEngine.run_daily(
                        decision_date=d, offline=offline, mtm_only=False, catchup_enqueue=True, conn=conn
                    )
                    _record_ledger(d, portfolio_id, STATUS_CATCHUP_FULL, catchup_of=as_of_date, conn=conn)
                    report["caught_up"].append(d)
                    logger.info(f"[CATCHUP] {d}: tín hiệu bù ĐÃ NẠP HÀNG ĐỢI (sẽ ép khớp @ open {as_of_date}).")
                else:
                    # STALE — chỉ MtM/settlement, không phát lệnh cũ
                    PaperTradingEngine.run_daily(decision_date=d, offline=offline, mtm_only=True, conn=conn)
                    _record_ledger(d, portfolio_id, STATUS_CATCHUP_MTM_ONLY, catchup_of=as_of_date, conn=conn)
                    report["mtm_only"].append(d)
                    logger.warning(
                        f"[CATCHUP] {d}: STALE — chỉ MtM/settle (tín hiệu hết hạn). "
                        f"⚠️ Cần con người xem xét quyết định giao dịch ngày này."
                    )
            except Exception as e:
                # [WHY] Ghi FAILED trên cùng conn: nếu chính conn đã hỏng (interrupt/kill-switch)
                # thì để lỗi ledger giết luôn cả vòng catch-up là phản tác dụng — log rõ rồi đi tiếp.
                try:
                    _record_ledger(d, portfolio_id, STATUS_FAILED, last_error=str(e), catchup_of=as_of_date, conn=conn)
                except sqlite3.Error as _e_ledger:
                    logger.error(f"[CATCHUP] {d}: không ghi được ledger FAILED: {_e_ledger}")
                report["errors"].append({"date": d, "error": str(e)})
                logger.exception(f"[CATCHUP] {d}: bù thất bại: {e}")

    # --- TRANSPOSE & KILL-SWITCH: ép khớp hàng đợi @ open ngày phục hồi ---
    # Chạy SAU khi đã nạp mọi lệnh bù, TRƯỚC khi luồng chính sinh tín hiệu mới
    # của as_of_date → buying_power ngày as_of tự phản ánh vốn đã tiêu cho lệnh bù.
    try:
        eng = PaperTradingEngine(portfolio_id=portfolio_id, offline=offline)
        report["queue_execution"] = eng.process_catchup_queue(as_of_date, conn=conn)
    except Exception as e:
        logger.exception(f"[CATCHUP] process_catchup_queue lỗi: {e}")
        report["queue_execution"] = {"error": str(e)}

    return report


def run_eod_pipeline(
    as_of_date: str | None = None,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
    max_retries: int = MAX_RETRIES,
    retry_sleep: int = RETRY_SLEEP_SECONDS,
    force: bool = False,
    catchup: bool = True,
) -> dict:
    """Điểm vào EOD tự phục hồi + lũy đẳng + chống race + GIAO DỊCH BÙ.

    GIAO THỨC ACID (Bifurcated Storage):
      - Lõi Hạch toán (catch-up + paper trading: settle/CA/MtM/lệnh) chạy
        trong 1 Global Transaction (BEGIN IMMEDIATE) do global_transaction()
        quản lý → ROLLBACK sạch 100% nếu lỗi giữa chừng (chống double-entry
        corruption khi cronjob tự chạy lại).
      - Lõi Viễn trắc (VQA/SEL/macro/stacktrace) ghi .jsonl NGOÀI SQLite →
        sống sót rollback.
      - Sổ cái vận hành (eod_run_ledger) ghi HAI CHẾ ĐỘ (Mục 21):
          * trong Global Transaction → ghi trên chính `conn` đó (một writer duy nhất);
          * sau rollback + nhả lock (Ledger Fallback) → kết nối riêng + retry
            exponential backoff khi gặp "database is locked".
        Trước 03/11/2026 chỉ có chế độ thứ hai nhưng catch-up lại gọi nó TRONG
        transaction ⇒ luôn ném "database is locked" ⇒ ledger đứng yên từ 16/07/2026.

    Args:
      as_of_date: ngày xử lý. None → EOD mới nhất trong DB.
      force: True → bỏ qua idempotency check (chạy lại có chủ đích).
      catchup: True (mặc định) → bù tuần tự các ngày nợ trước khi xử lý hôm nay.
    """
    from src.daily_updater import run_post_update_engines
    from src.database.acid import DEFAULT_TXN_TIMEOUT, ResourceLockedException, TransactionTimeout, global_transaction
    from src.engine.paper_trading_engine import PaperTradingEngine

    if as_of_date is None:
        as_of_date = _resolve_eod_date()

    result = {"as_of_date": as_of_date, "portfolio_id": portfolio_id, "timestamp": datetime.now().isoformat()}
    last_error = None
    corr_id = None

    try:
        # --- IDEMPOTENCY CHECK (trước transaction — chỉ đọc) ---
        if not force and _is_already_processed(as_of_date, portfolio_id):
            result.update(
                {
                    "status": "SKIPPED",
                    "reason": "ALREADY_PROCESSED",
                    "note": f"{as_of_date} đã xử lý — không chạy lại.",
                }
            )
            logger.info(f"[EOD] {as_of_date}: already processed — skipped.")
            return result

        # --- CỔNG DỮ LIỆU (Mục 19): không có nến ⇒ DỪNG trước khi hạch toán ---
        # [WHY]: thiếu cổng này, 02/11/2026 đã ghi equity carry-forward giả và trả SUCCESS
        # (xem EODNoBarsError). Đứng SAU idempotency (một phép đọc rẻ, chỉ trả SKIP khi phiên
        # đã có dấu vết hạch toán ⇒ không có gì để bảo vệ) nhưng TRƯỚC mọi đường ghi; `--force`
        # bỏ qua idempotency nên vẫn đi qua cổng này.
        # Ngày nghỉ (cuối tuần/phantom holiday) = "không giao dịch", KHÔNG phải thiếu dữ liệu.
        if count_eod_bars(as_of_date) == 0 and is_non_trading_day(as_of_date):
            result.update(
                {
                    "status": "SKIPPED",
                    "reason": "NON_TRADING_SESSION",
                    "note": f"{as_of_date} là ngày nghỉ — không có nến, không hạch toán.",
                }
            )
            logger.info(f"[EOD] {as_of_date}: ngày nghỉ — skipped.")
            return result
        assert_eod_bars_present(as_of_date)

        # --- ANALYTICS ENGINES (NGOÀI Global Transaction) ---
        # Các engine phân tích/viễn trắc (breadth, RS, flow forecast, regime,
        # absorption) tự mở kết nối ghi snapshot riêng. Nếu chạy trong
        # global_transaction (BEGIN IMMEDIATE giữ write-lock), chúng tự tranh
        # chấp khóa SQLite → "database is locked" (fix EOD 17/08/2026).
        engine_results = run_post_update_engines()

        # Global Transaction: 1 connection, BEGIN IMMEDIATE, auto-rollback.
        # ResourceLockedException nếu tiến trình EOD khác đang chạy.
        # CHỈ bao hạch toán kế toán (catch-up + paper trading) — KHÔNG bao engines.
        with global_transaction(as_of_date, portfolio_id) as (conn, corr):
            corr_id = corr.correlation_id

            try:
                # --- 1.5. CATCH-UP: bù các ngày nợ TRƯỚC khi xử lý hôm nay ---
                # CHẠY TRONG transaction (conn chung) → settlement/MtM bù cũng nằm
                # dưới sự chi phối của ROLLBACK. Bù tuần tự theo thời gian.
                if catchup:
                    try:
                        result["catchup"] = _catchup_gap_days(as_of_date, portfolio_id, conn=conn)
                    except Exception as e:
                        logger.exception(f"[EOD] catch-up thất bại (non-blocking): {e}")
                        result["catchup"] = {"error": str(e)}

                # --- 3. PAPER TRADING (chỉ kế toán, nằm trong transaction) ---
                logger.info(f"[EOD] {as_of_date}: chạy paper trading (corr={corr_id})")
                paper = PaperTradingEngine.run_daily(decision_date=as_of_date, offline=True, conn=conn)
                summary = paper.get("summary", {})
                engine_results["paper_trading"] = {
                    "decision_date": paper.get("decision_date"),
                    "orders": len(paper.get("orders", [])),
                    "hdr": paper.get("hdr"),
                    "w1": paper.get("w1"),
                    "macro_state": paper.get("macro_state"),
                    "rejection_rate": summary.get("rejection_rate"),
                    "avg_slippage_bps": summary.get("avg_slippage_bps"),
                    "avg_latency_ms": summary.get("avg_latency_ms"),
                }
                result.update({"status": "SUCCESS", "engine_results": engine_results})
                logger.info(f"[EOD] {as_of_date}: SUCCESS (corr={corr_id})")
                # [WHY] KHÔNG `return` tại đây (Mục 24, 04/11/2026): thoát hàm ngay trong
                # khối `with global_transaction(...)` sẽ nhảy qua toàn bộ "LEDGER FALLBACK"
                # bên dưới — nơi DUY NHẤT ghi `STATUS_SUCCESS` cho phiên hiện tại. Hệ quả
                # thật: phiên 03/11 hạch toán xong, equity 841.146.848,35 đã ghi, nhưng
                # `eod_run_ledger` vẫn mang dòng FAILED của lần chạy 11:46 ⇒ hôm sau bị coi
                # là ngày nợ và bù lại (double-entry). — thêm `return` ở đây là tái sinh lỗi.
            except Exception as e:
                last_error = str(e)
                logger.exception(f"[EOD] {as_of_date}: accounting lỗi → sẽ rollback: {e}")
                result.update({"status": "FAILED", "reason": "ACCOUNTING_ERROR", "error": last_error})
                raise  # để global_transaction thực hiện ROLLBACK
    except EODNoBarsError as exc:
        # Fail-closed: ghi nợ vào ledger rồi ném tiếp để scheduler thấy mã thoát ≠ 0.
        last_error = str(exc)
        result.update({"status": "FAILED", "reason": "NO_EOD_BARS", "error": last_error})
        logger.critical(f"[EOD] {as_of_date}: {last_error}")
    except ResourceLockedException:
        # Concurrency Guard: tiến trình EOD khác đang chiếm khóa CSDL.
        # Global Transaction chưa từng mở → không cần rollback.
        result.update({"status": "SKIPPED", "reason": "LOCK_HELD", "note": "Tiến trình EOD khác đang chạy — bỏ qua an toàn."})
        logger.warning(f"[EOD] {as_of_date}: lock held — skipped (anti-race).")
        return result
    except TransactionTimeout:
        # Kill-switch: giao dịch vượt quá SLA.
        last_error = f"Transaction vượt quá {DEFAULT_TXN_TIMEOUT}s — kill-switch kích hoạt."
        logger.critical(f"[EOD] {as_of_date}: TRANSACTION TIMEOUT — {last_error}")
        result.update({"status": "FAILED", "reason": "TRANSACTION_TIMEOUT", "error": last_error})
        # Ledger fallback sẽ ghi FAILED ở bên dưới
    except Exception as exc:
        # [WHY] (Mục 24, 04/11/2026) Lỗi hạch toán được `raise` từ trong khối
        # `with global_transaction(...)` để transaction ROLLBACK, nhưng trước bản vá nó
        # thoát luôn khỏi hàm ⇒ khối ledger fallback bên dưới KHÔNG chạy ⇒ phiên lỗi không
        # có dòng nào trong `eod_run_ledger` (im lặng = "chưa từng chạy", đúng lỗi Mục 21).
        # Tới đây transaction đã rollback và nhả write-lock nên ghi nợ bằng kết nối riêng là
        # an toàn. Phải `raise` lại để scheduler/cron thấy exit ≠ 0 — cấm nuốt lỗi.
        last_error = last_error or str(exc)
        _record_ledger(as_of_date, portfolio_id, STATUS_FAILED, attempts=1, last_error=last_error)
        raise

    # --- LEDGER FALLBACK: ghi trạng thái qua KẾT NỐI RIÊNG (sau rollback+unlock) ---
    # Correlation ID đối chiếu rollback (CSDL) ↔ telemetry .jsonl (log rời rạc).
    final_status = result.get("status")
    if final_status in ("SUCCESS", "SKIPPED"):
        if final_status == "SUCCESS":
            _record_ledger(as_of_date, portfolio_id, STATUS_SUCCESS)
        # SKIPPED (ALREADY_PROCESSED) đã ghi ở trên hoặc không cần
    else:
        _record_ledger(as_of_date, portfolio_id, STATUS_FAILED, attempts=1, last_error=last_error)
        logger.error(f"[EOD] {as_of_date}: FAILED → ghi nợ ledger (corr={corr_id}).")
    if corr_id:
        result["correlation_id"] = corr_id
    if result.get("reason") == "NO_EOD_BARS":
        # [WHY] Ledger đã ghi FAILED ở trên; ném tiếp để cron/scheduler KHÔNG thấy exit 0
        # (đúng lỗi (a): báo SUCCESS khi không hạch toán được gì).
        raise EODNoBarsError(result.get("error") or "NO_EOD_BARS")
    return result


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="PTCK EOD Runner (self-healing)")
    parser.add_argument("--date", default=None, help="as_of_date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Bỏ qua idempotency check")
    parser.add_argument("--retry-sleep", type=int, default=RETRY_SLEEP_SECONDS, help="Giây ngủ giữa các lần retry")
    parser.add_argument("--no-catchup", dest="catchup", action="store_false", help="Tắt cơ chế Giao dịch bù (Catch-up)")
    parser.set_defaults(catchup=True)
    args = parser.parse_args()
    out = run_eod_pipeline(as_of_date=args.date, force=args.force, retry_sleep=args.retry_sleep, catchup=args.catchup)
    # safe_json_dumps: chống np.* trong engine_results làm ném TypeError
    print(safe_json_dumps(out, indent=2))
