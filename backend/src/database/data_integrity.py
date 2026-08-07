"""Index Integrity Guard v1.0 — Self-healing data quality layer.

Auto-detects and corrects VNINDEX scale corruption at pipeline entry
so that RS, correlation, and leadership metrics are never computed on
garbage data.

Detection criteria
------------------
- scale_break: symbol = VNINDEX, close > 0, close <= 100  (corrupted)
- ideal:        symbol = VNINDEX, close > 100               (healthy)

When the daily_ohlcv table stores index points 1000x too small
(1.82 instead of 1820), the guard multiplies by 1000 and logs the fix.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("data_integrity")

# Biểu thức nhận diện dòng datetime-typed: 'YYYY-MM-DD 07:00:00'
_DATETIME_DATE_GLOB = "????-??-?? *"
_PLAIN_DATE_GLOB = "????-??-??"


def detect_datetime_rows(conn: sqlite3.Connection) -> dict:
    """Đếm dòng daily_ohlcv lưu date dạng 'YYYY-MM-DD HH:MM:SS'.

    Lỗi lịch sử 06/08/2026: 59,219 dòng datetime (nguồn `kbs`) — 56,201 trùng bản
    plain (~1000×) + 3,018 gapfill độc bản. Những dòng này vô hình với exact-match
    `_get_close` nhưng ô nhiễm range-query.

    Returns
    -------
    dict with keys: count, rows (sample list)
    """
    result: dict = {"count": 0, "rows": []}
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT GLOB ? AND date GLOB ?",
        (_PLAIN_DATE_GLOB, _DATETIME_DATE_GLOB),
    )
    result["count"] = cursor.fetchone()[0]
    if result["count"]:
        cursor.execute(
            "SELECT symbol, date FROM daily_ohlcv WHERE date NOT GLOB ? AND date GLOB ? ORDER BY date LIMIT 20",
            (_PLAIN_DATE_GLOB, _DATETIME_DATE_GLOB),
        )
        result["rows"] = cursor.fetchall()
    return result


def sanitize_datetime_rows(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """Sửa dòng datetime-typed trong daily_ohlcv theo 2 quy tắc bảo toàn:

    1. Dòng datetime TRÙNG bản plain (cùng (symbol, date) sau cắt giờ) → XÓA.
       Đây là 56,201 dòng giá trị ~1000× gây ô nhiễm range-query.
    2. Dòng datetime ĐỘC bản (gapfill, không có bản plain) → CẮT giờ giữ lại.
       Đây là 3,018 dòng AAH/AAM/AAS... chỉ tồn tại ở dạng datetime; xóa chúng
       sẽ mất dữ liệu ngày độc bản.

    KHÔNG rescale giá (lớp read-time `_get_close` đã xử lý <1000 → ×1000).
    """
    cursor = conn.cursor()

    # 1. Xóa dòng datetime có bản plain tồn tại
    cursor.execute(
        """
        DELETE FROM daily_ohlcv
        WHERE date NOT GLOB ? AND date GLOB ?
          AND EXISTS (
            SELECT 1 FROM daily_ohlcv AS plain
            WHERE plain.symbol = daily_ohlcv.symbol
              AND plain.date = substr(daily_ohlcv.date, 1, 10)
          )
        """,
        (_PLAIN_DATE_GLOB, _DATETIME_DATE_GLOB),
    )
    removed_duplicates = cursor.rowcount

    # 2. Cắt giờ dòng datetime độc bản (gapfill) -> giữ dữ liệu
    cursor.execute(
        """
        UPDATE daily_ohlcv
        SET date = substr(date, 1, 10)
        WHERE date NOT GLOB ? AND date GLOB ?
          AND NOT EXISTS (
            SELECT 1 FROM daily_ohlcv AS plain
            WHERE plain.symbol = daily_ohlcv.symbol
              AND plain.date = substr(daily_ohlcv.date, 1, 10)
          )
        """,
        (_PLAIN_DATE_GLOB, _DATETIME_DATE_GLOB),
    )
    preserved_gapfills = cursor.rowcount

    if not dry_run:
        conn.commit()
    else:
        conn.rollback()

    # 3. Verify không còn dòng datetime
    remaining = detect_datetime_rows(conn)["count"]

    result = {
        "dry_run": bool(dry_run),
        "removed_duplicates": removed_duplicates,
        "preserved_gapfills": preserved_gapfills,
        "remaining_datetime_rows": remaining,
    }
    logger.info(
        "SANITIZE datetime rows: dry_run=%s removed_duplicates=%d preserved_gapfills=%d remaining=%d",
        dry_run,
        removed_duplicates,
        preserved_gapfills,
        remaining,
    )
    return result


def ensure_vnindex_integrity(
    conn: sqlite3.Connection | None = None,
    auto_fix: bool = True,
    verbose: bool = True,
) -> dict:
    """Check VNINDEX close column for scale corruption and optionally fix.

    Parameters
    ----------
    conn : sqlite3.Connection, optional
        Reuse an existing connection. If None, a new one is opened.
    auto_fix : bool
        If True, corrupt rows are repaired (close *= 1000).
    verbose : bool
        If True, prints a one-line summary.

    Returns
    -------
    dict with keys:
        status       : 'OK' | 'FIXED' | 'CORRUPT'
        corrupt_rows : int
        rows_fixed   : int
        detail       : str
    """
    from src import config

    close_conn = conn is None
    if close_conn:
        conn = sqlite3.connect(str(config.DATA_DIR / "screener_cache.db"), timeout=10)

    result: dict = {
        "status": "OK",
        "corrupt_rows": 0,
        "rows_fixed": 0,
        "detail": "",
    }

    try:
        cursor = conn.cursor()

        # --- Step 1: Count corrupt rows ---
        cursor.execute(
            "SELECT COUNT(*) FROM daily_ohlcv "
            "WHERE symbol='VNINDEX' AND (close > 0 AND close <= 100 OR (close > 100 AND high > 0 AND high <= 100))"
        )
        corrupt = cursor.fetchone()[0]
        result["corrupt_rows"] = corrupt

        if corrupt == 0:
            result["detail"] = "VNINDEX scale is healthy"
            if verbose:
                logger.info("VNINDEX: OK — all close values in normal range")
            return result

        # --- Step 2: Sample corrupt rows ---
        cursor.execute(
            "SELECT date, close FROM daily_ohlcv "
            "WHERE symbol='VNINDEX' AND (close > 0 AND close <= 100 OR (close > 100 AND high > 0 AND high <= 100)) "
            "ORDER BY date"
        )
        samples = cursor.fetchall()
        dates_str = ", ".join(f"{r[0]}={r[1]}" for r in samples[:5])
        result["detail"] = f"{corrupt} corrupt rows detected: {dates_str} (expected >100, got <=100)"

        if not auto_fix:
            result["status"] = "CORRUPT"
            logger.warning(result["detail"])
            return result

        # --- Step 3: Fix ---
        cursor.execute(
            "UPDATE daily_ohlcv "
            "SET open = open * 1000, "
            "    high = high * 1000, "
            "    low = low * 1000, "
            "    close = close * 1000, "
            "    adj_close = adj_close * 1000 "
            "WHERE symbol='VNINDEX' AND close > 0 AND close <= 100"
        )
        cursor.execute(
            "UPDATE daily_ohlcv "
            "SET open = open * 1000, "
            "    high = high * 1000, "
            "    low = low * 1000, "
            "    adj_close = adj_close * 1000 "
            "WHERE symbol='VNINDEX' AND close > 100 AND high > 0 AND high <= 100"
        )
        conn.commit()

        # --- Step 4: Verify fix ---
        cursor.execute(
            "SELECT COUNT(*) FROM daily_ohlcv "
            "WHERE symbol='VNINDEX' AND (close > 0 AND close <= 100 OR (close > 100 AND high > 0 AND high <= 100))"
        )
        remaining = cursor.fetchone()[0]

        result["rows_fixed"] = corrupt
        if remaining == 0:
            result["status"] = "FIXED"
            if verbose:
                print(f"[INDEX INTEGRITY] Fixed {corrupt} VNINDEX rows: {dates_str}")
        else:
            result["status"] = "CORRUPT"
            result["detail"] += f" — {remaining} rows still corrupt after fix"
            logger.error(result["detail"])

        return result

    finally:
        if close_conn:
            conn.close()


def integrity_report(conn: sqlite3.Connection | None = None) -> dict:
    """Return a comprehensive data-integrity snapshot for monitoring.

    Checks:
        - VNINDEX scale health
        - VNINDEX row count
        - VNINDEX date range
        - Number of distinct symbols (non-zero close)
        - Pipeline-critical tables exist

    Returns a dict suitable for JSON serialisation.
    """
    from src import config

    close_conn = conn is None
    if close_conn:
        conn = sqlite3.connect(str(config.DATA_DIR / "screener_cache.db"), timeout=10)

    report: dict = {
        "vnindex": {},
        "symbols": {},
        "tables": {},
    }

    try:
        cursor = conn.cursor()

        # VNINDEX health
        cursor.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX'")
        report["vnindex"]["total_rows"] = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(date), MAX(date) FROM daily_ohlcv WHERE symbol='VNINDEX'")
        mn, mx = cursor.fetchone()
        report["vnindex"]["date_range"] = f"{mn} to {mx}"

        cursor.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX' AND close > 0 AND close <= 100")
        corrupt = cursor.fetchone()[0]
        report["vnindex"]["corrupt_rows"] = corrupt
        report["vnindex"]["healthy"] = corrupt == 0
        report["vnindex"]["scale"] = "OK" if corrupt == 0 else "CORRUPT"

        cursor.execute("SELECT MIN(close), MAX(close), AVG(close) FROM daily_ohlcv WHERE symbol='VNINDEX'")
        cmin, cmax, cavg = cursor.fetchone()
        report["vnindex"]["close_range"] = f"{cmin:.2f} to {cmax:.2f}"
        report["vnindex"]["close_avg"] = round(cavg, 2) if cavg else None

        # Symbols
        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv WHERE symbol != 'VNINDEX' AND close > 0")
        report["symbols"]["active_symbols"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv WHERE symbol != 'VNINDEX'")
        report["symbols"]["total_symbols"] = cursor.fetchone()[0]

        # Tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cursor.fetchall()]
        report["tables"]["count"] = len(tables)
        report["tables"]["names"] = tables

        return report

    finally:
        if close_conn:
            conn.close()
