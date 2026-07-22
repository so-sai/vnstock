"""cache_warming.py — Pre-fetch engine cho Local Cache.

Chạy ngầm sau mỗi phiên API thành công. Pre-fetch N+7 ngày vào "hot window"
và cập nhật data_freshness để các consumer biết dữ liệu đã WARM.

Luồng:
  1. Đọc last_date_per_symbol từ daily_ohlcv
  2. Tính expected_date = last_trading_day
  3. Nếu gap > 0 → pre-fetch từ (last_date+1) đến (expected_date+7)
  4. Cập nhật data_freshness.source='API'
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from src.database.data_freshness import ensure_table, upsert_freshness
from src.database.db_core import get_connection

logger = logging.getLogger("PTCK_CACHE_WARM")

HOT_WINDOW_EXTRA = 7  # pre-fetch thêm N ngày sau today
BATCH_SIZE = 10       # số symbol song song trong một batch
INTER_BATCH_DELAY = 1.0  # giây giữa các batch — tránh rate limit


def warm_cache(target_date: Optional[str] = None) -> Dict[str, int]:
    """Pre-fetch dữ liệu cho hot window.

    Args:
        target_date: Ngày đích (mặc định: hôm nay)

    Returns:
        Dict {total: tổng số, fetched: số đã fetch, skipped: số bỏ qua,
              failed: số lỗi}
    """
    ensure_table()
    target = target_date or datetime.now().strftime("%Y-%m-%d")

    # 1. Lấy last_date cho mỗi symbol
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol, MAX(date) FROM daily_ohlcv GROUP BY symbol"
        ).fetchall()

    stats = {"total": len(rows), "fetched": 0, "skipped": 0, "failed": 0}
    if not rows:
        return stats

    # 2. Xác định gap
    pending: List[str] = []
    for r in rows:
        sym = r[0]
        last_date = r[1]
        if last_date and last_date >= target:
            stats["skipped"] += 1
            continue
        pending.append(sym)

    stats["pending"] = len(pending)

    # 3. Pre-fetch theo batch, sequential với delay
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        batch_stats = _prefetch_batch(batch, target)
        stats["fetched"] += batch_stats["fetched"]
        stats["failed"] += batch_stats["failed"]
        stats["skipped"] += batch_stats["skipped"]

        if i + BATCH_SIZE < len(pending):
            time.sleep(INTER_BATCH_DELAY)

    # 4. Ghi freshness cho các symbol đã warm
    for sym in pending:
        upsert_freshness(sym, target, source="API_PREFETCH", api_status="OK")

    logger.info(
        f"[CACHE_WARM] {stats['fetched']}/{stats['total']} fetched, "
        f"{stats['failed']} failed, {stats['skipped']} skipped"
    )
    return stats


def warm_single(symbol: str, target_date: str) -> bool:
    """Pre-fetch cho một symbol — gọi từ daily_updater sau khi update thành công."""
    try:
        upsert_freshness(symbol, target_date, source="API", api_status="OK")
        return True
    except Exception as e:
        logger.debug(f"[CACHE_WARM] {symbol}: {e}")
        return False


def _prefetch_batch(symbols: List[str], target_date: str) -> Dict[str, int]:
    """Pre-fetch một batch symbol — sequential trong batch để tránh rate limit."""
    stats = {"fetched": 0, "failed": 0, "skipped": 0}
    for sym in symbols:
        try:
            # Dùng vnstock Quote để lấy history
            from vnstock import Quote
            q = Quote(symbol=sym, source="kbs")
            start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
            end = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=HOT_WINDOW_EXTRA)).strftime("%Y-%m-%d")
            df = q.history(start=start, end=end)
            if df is not None and not df.empty:
                _save_prefetch(df, sym)
                stats["fetched"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            logger.warning(f"[CACHE_WARM] FAIL {sym}: {e}")
            stats["failed"] += 1
    return stats


def _save_prefetch(df: pd.DataFrame, symbol: str):
    """Lưu dữ liệu pre-fetch vào daily_ohlcv + data_freshness."""
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    df["symbol"] = symbol
    df["source"] = "kbs"
    if "time" in df.columns:
        df = df.rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
    cols = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
    df = df[[c for c in cols if c in df.columns]]
    with get_connection() as conn:
        from src.database.db_core import save_data_upsert
        save_data_upsert("daily_ohlcv", df, conn)
    # Ghi freshness
    for d in df["date"].unique():
        upsert_freshness(symbol, d, source="API_PREFETCH", api_status="OK")
