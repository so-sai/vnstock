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
from datetime import datetime
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
from src.database.db_core import get_connection, safe_json_dumps  # noqa: E402

logger = logging.getLogger("PTCK_SYSTEM")

# --- Cấu hình Retry -----------------------------------------------------------
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 15 * 60      # 15 phút
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
STATUS_SUCCESS = "SUCCESS"                 # chạy đầy đủ (settle + lệnh + MtM)
STATUS_FAILED = "FAILED"                   # thất bại sau retry (nợ, chờ bù)
STATUS_PENDING_CATCHUP = "PENDING_CATCHUP" # đã đánh dấu là ngày nợ chờ bù
STATUS_CATCHUP_FULL = "CATCHUP_FULL"       # bù đầy đủ (còn trong cửa sổ tín hiệu)
STATUS_CATCHUP_MTM_ONLY = "CATCHUP_MTM_ONLY"  # bù stale: chỉ MtM/settlement



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


# ============================================================================
#  EOD RUN LEDGER — Sổ cái vận hành bền vững (persistent memory cho Catch-up)
# ============================================================================
def _ensure_ledger_schema():
    """Tạo bảng eod_run_ledger nếu chưa có.

    Đây là 'bộ nhớ dài hạn' của scheduler: mỗi phiên EOD ghi lại kết quả.
    Nhờ nó, ngày T+1 biết chắc ngày T có thất bại (nợ) không — thay vì chỉ
    dựa vào log dễ mất. Là nền tảng cho Gap Detection + Sequential Backfill.
    """
    with get_connection() as conn:
        conn.execute("""
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
        """)
        conn.commit()


def _record_ledger(as_of_date: str, portfolio_id: str, status: str,
                   attempts: int = 0, last_error: Optional[str] = None,
                   catchup_of: Optional[str] = None):
    """Ghi/cập nhật trạng thái một phiên EOD vào sổ cái (idempotent UPSERT)."""
    _ensure_ledger_schema()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
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
        """, (as_of_date, portfolio_id, status, attempts, last_error,
              catchup_of, now, now))
        conn.commit()


def _ledger_succeeded(as_of_date: str, portfolio_id: str) -> bool:
    """Ngày này đã có kết quả 'xử lý xong' trong ledger chưa?

    Coi là xong nếu status thuộc {SUCCESS, CATCHUP_FULL, CATCHUP_MTM_ONLY}.
    """
    _ensure_ledger_schema()
    done = (STATUS_SUCCESS, STATUS_CATCHUP_FULL, STATUS_CATCHUP_MTM_ONLY)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM eod_run_ledger "
            "WHERE as_of_date=? AND portfolio_id=?",
            (as_of_date, portfolio_id)
        ).fetchone()
    return bool(row) and row[0] in done


def _trading_sessions_up_to(as_of_date: str, limit: int) -> list:
    """Danh sách 'limit' phiên giao dịch GẦN NHẤT (<= as_of_date), tăng dần.

    Nguồn sự thật: các ngày distinct trong daily_ohlcv (lịch giao dịch thực tế,
    đã trừ cuối tuần/lễ). Offline — không gọi API.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv "
            "WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (as_of_date, limit)
        ).fetchall()
    return sorted(r[0] for r in rows)


def detect_gap_days(as_of_date: str,
                    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
                    scan_limit: int = CATCHUP_SCAN_LIMIT) -> list:
    """GAP DETECTION: các phiên < as_of_date CHƯA được xử lý xong (ngày nợ).

    Quét 'scan_limit' phiên gần nhất TRƯỚC as_of_date, lọc ra những phiên
    KHÔNG có dấu vết hoàn tất (ledger chưa SUCCESS/CATCHUP và cũng chưa có
    paper trade/equity). Trả về danh sách TĂNG DẦN để bù đúng thứ tự thời gian.

    Chỉ tính ngày nợ TRƯỚC as_of_date (ngày hiện tại xử lý riêng ở luồng chính).
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
        if _ledger_succeeded(d, portfolio_id):
            continue
        if _is_already_processed(d, portfolio_id):
            # Có trade/equity nhưng ledger trống (chạy từ trước khi có ledger)
            # → coi như xong, đồng bộ ledger để lần sau khỏi quét lại
            _record_ledger(d, portfolio_id, STATUS_SUCCESS)
            continue
        gaps.append(d)
    return gaps


def _catchup_gap_days(as_of_date: str, portfolio_id: str,
                      offline: bool = True, conn=None) -> Dict:
    """SEQUENTIAL BACKFILL: bù tuần tự các ngày nợ TRƯỚC as_of_date.

    Cơ chế Stale-Signal Guard:
      - Ngày nợ cách 'as_of_date' <= MAX_CATCHUP_DAYS phiên → BÙ ĐẦY ĐỦ
        (phát lệnh theo as_of của ĐÚNG ngày đó → dùng giá/normalizer lịch sử,
        KHÔNG lookahead). Ghi ledger CATCHUP_FULL.
      - Xa hơn → STALE: chỉ hạch toán MtM/settlement/corp-action (mtm_only),
        KHÔNG tái tạo lệnh cũ. Ghi ledger CATCHUP_MTM_ONLY + cảnh báo con người.

    ACID: nhận conn (Global Transaction) → mọi ghi kế toán chạy chung cursor,
    chịu ROLLBACK nếu lỗi. _record_ledger (sổ cái vận hành) dùng KẾT NỐI RIÊNG.

    Xử lý TĂNG DẦN theo thời gian để dòng tiền/settlement diễn ra đúng trình tự.
    """
    gaps = detect_gap_days(as_of_date, portfolio_id)
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
                    PaperTradingEngine.run_daily(decision_date=d, offline=offline,
                                                 mtm_only=False, catchup_enqueue=True,
                                                 conn=conn)
                    _record_ledger(d, portfolio_id, STATUS_CATCHUP_FULL,
                                   catchup_of=as_of_date)
                    report["caught_up"].append(d)
                    logger.info(f"[CATCHUP] {d}: tín hiệu bù ĐÃ NẠP HÀNG ĐỢI "
                                f"(sẽ ép khớp @ open {as_of_date}).")
                else:
                    # STALE — chỉ MtM/settlement, không phát lệnh cũ
                    PaperTradingEngine.run_daily(decision_date=d, offline=offline,
                                                 mtm_only=True, conn=conn)
                    _record_ledger(d, portfolio_id, STATUS_CATCHUP_MTM_ONLY,
                                   catchup_of=as_of_date)
                    report["mtm_only"].append(d)
                    logger.warning(
                        f"[CATCHUP] {d}: STALE — chỉ MtM/settle (tín hiệu hết hạn). "
                        f"⚠️ Cần con người xem xét quyết định giao dịch ngày này.")
            except Exception as e:
                _record_ledger(d, portfolio_id, STATUS_FAILED, last_error=str(e),
                               catchup_of=as_of_date)
                report["errors"].append({"date": d, "error": str(e)})
                logger.exception(f"[CATCHUP] {d}: bù thất bại: {e}")

    # --- TRANSPOSE & KILL-SWITCH: ép khớp hàng đợi @ open ngày phục hồi ---
    # Chạy SAU khi đã nạp mọi lệnh bù, TRƯỚC khi luồng chính sinh tín hiệu mới
    # của as_of_date → buying_power ngày as_of tự phản ánh vốn đã tiêu cho lệnh bù.
    try:
        eng = PaperTradingEngine(portfolio_id=portfolio_id, offline=offline)
        report["queue_execution"] = eng.process_catchup_queue(as_of_date,
                                                               conn=conn)
    except Exception as e:
        logger.exception(f"[CATCHUP] process_catchup_queue lỗi: {e}")
        report["queue_execution"] = {"error": str(e)}

    return report


def run_eod_pipeline(as_of_date: Optional[str] = None,
                      portfolio_id: str = DEFAULT_PORTFOLIO_ID,
                      max_retries: int = MAX_RETRIES,
                      retry_sleep: int = RETRY_SLEEP_SECONDS,
                      force: bool = False,
                      catchup: bool = True) -> Dict:
    """Điểm vào EOD tự phục hồi + lũy đẳng + chống race + GIAO DỊCH BÙ.

    GIAO THỨC ACID (Bifurcated Storage):
      - Lõi Hạch toán (catch-up + paper trading: settle/CA/MtM/lệnh) chạy
        trong 1 Global Transaction (BEGIN IMMEDIATE) do global_transaction()
        quản lý → ROLLBACK sạch 100% nếu lỗi giữa chừng (chống double-entry
        corruption khi cronjob tự chạy lại).
      - Lõi Viễn trắc (VQA/SEL/macro/stacktrace) ghi .jsonl NGOÀI SQLite →
        sống sót rollback.
      - Sổ cái vận hành (eod_run_ledger) ghi QUA KẾT NỐI RIÊNG sau khi
        rollback + nhả lock (Ledger Fallback) → tránh deadlock khóa ghi WAL.

    Args:
      as_of_date: ngày xử lý. None → EOD mới nhất trong DB.
      force: True → bỏ qua idempotency check (chạy lại có chủ đích).
      catchup: True (mặc định) → bù tuần tự các ngày nợ trước khi xử lý hôm nay.
    """
    from src.database.acid import (global_transaction, get_correlation,
                                   ResourceLockedException,
                                   TransactionTimeout, DEFAULT_TXN_TIMEOUT,
                                   exception_telemetry, vqa_telemetry,
                                   sel_telemetry, macro_telemetry)
    from src.engine.paper_trading_engine import PaperTradingEngine
    from src.daily_updater import run_post_update_engines

    if as_of_date is None:
        as_of_date = _resolve_eod_date()

    result = {"as_of_date": as_of_date, "portfolio_id": portfolio_id,
              "timestamp": datetime.now().isoformat()}
    status = None
    last_error = None
    corr_id = None

    try:
        # Global Transaction: 1 connection, BEGIN IMMEDIATE, auto-rollback.
        # ResourceLockedException nếu tiến trình EOD khác đang chạy.
        with global_transaction(as_of_date, portfolio_id) as (conn, corr):
            corr_id = corr.correlation_id

            try:
                # --- 1.5. CATCH-UP: bù các ngày nợ TRƯỚC khi xử lý hôm nay ---
                # CHẠY TRONG transaction (conn chung) → settlement/MtM bù cũng nằm
                # dưới sự chi phối của ROLLBACK. Bù tuần tự theo thời gian.
                if catchup:
                    try:
                        result["catchup"] = _catchup_gap_days(as_of_date, portfolio_id,
                                                               conn=conn)
                    except Exception as e:
                        logger.exception(f"[EOD] catch-up thất bại (non-blocking): {e}")
                        result["catchup"] = {"error": str(e)}

                # --- 2. IDEMPOTENCY (chống nhân đôi) ---
                if not force and _is_already_processed(as_of_date, portfolio_id):
                    result.update({"status": "SKIPPED", "reason": "ALREADY_PROCESSED",
                                   "note": f"{as_of_date} đã xử lý — không chạy lại."})
                    logger.info(f"[EOD] {as_of_date}: already processed — skipped.")
                    return result

                # --- 3. ENGINE PHI KẾ TOÁN (read-only / offline) + PAPER TRADING ---
                logger.info(f"[EOD] {as_of_date}: chạy engines + paper trading "
                            f"(corr={corr_id})")
                engine_results = run_post_update_engines()
                paper = PaperTradingEngine.run_daily(decision_date=as_of_date,
                                                     offline=True, conn=conn)
                summary = paper.get('summary', {})
                engine_results['paper_trading'] = {
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
                return result
            except Exception as e:
                last_error = str(e)
                logger.exception(f"[EOD] {as_of_date}: accounting lỗi → sẽ rollback: {e}")
                result.update({"status": "FAILED", "reason": "ACCOUNTING_ERROR",
                               "error": last_error})
                raise  # để global_transaction thực hiện ROLLBACK
    except ResourceLockedException:
        # Concurrency Guard: tiến trình EOD khác đang chiếm khóa CSDL.
        # Global Transaction chưa từng mở → không cần rollback.
        result.update({"status": "SKIPPED", "reason": "LOCK_HELD",
                       "note": "Tiến trình EOD khác đang chạy — bỏ qua an toàn."})
        logger.warning(f"[EOD] {as_of_date}: lock held — skipped (anti-race).")
        return result
    except TransactionTimeout:
        # Kill-switch: giao dịch vượt quá SLA.
        last_error = f"Transaction vượt quá {DEFAULT_TXN_TIMEOUT}s — kill-switch kích hoạt."
        logger.critical(f"[EOD] {as_of_date}: TRANSACTION TIMEOUT — {last_error}")
        result.update({"status": "FAILED", "reason": "TRANSACTION_TIMEOUT",
                       "error": last_error})
        # Ledger fallback sẽ ghi FAILED ở bên dưới

    # --- LEDGER FALLBACK: ghi trạng thái qua KẾT NỐI RIÊNG (sau rollback+unlock) ---
    # Correlation ID đối chiếu rollback (CSDL) ↔ telemetry .jsonl (log rời rạc).
    final_status = result.get("status")
    if final_status in ("SUCCESS", "SKIPPED"):
        if final_status == "SUCCESS":
            _record_ledger(as_of_date, portfolio_id, STATUS_SUCCESS)
        # SKIPPED (ALREADY_PROCESSED) đã ghi ở trên hoặc không cần
    else:
        _record_ledger(as_of_date, portfolio_id, STATUS_FAILED,
                       attempts=1, last_error=last_error)
        logger.error(f"[EOD] {as_of_date}: FAILED → ghi nợ ledger (corr={corr_id}).")
    if corr_id:
        result["correlation_id"] = corr_id
    return result


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
    parser.add_argument("--no-catchup", dest="catchup", action="store_false",
                        help="Tắt cơ chế Giao dịch bù (Catch-up)")
    parser.set_defaults(catchup=True)
    args = parser.parse_args()
    out = run_eod_pipeline(as_of_date=args.date, force=args.force,
                           retry_sleep=args.retry_sleep, catchup=args.catchup)
    # safe_json_dumps: chống np.* trong engine_results làm ném TypeError
    print(safe_json_dumps(out, indent=2))
