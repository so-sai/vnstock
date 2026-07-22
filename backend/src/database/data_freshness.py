"""data_freshness.py — Theo dõi độ tươi dữ liệu và trạng thái API ngoại vi.

Bảng `data_freshness` là single source of truth cho mọi consumer:
  - staleness_hours: tuổi dữ liệu (giờ kể từ last_updated)
  - api_status: OK | TIMEOUT | ERROR | NO_DATA
  - source: API | CACHE | BOOTSTRAP | SYNTHETIC

Mỗi consumer bắt buộc kiểm tra staleness_hours trước khi dùng.
Không có metadata → coi là STALE và từ chối xử lý.
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.database.db_core import get_connection

TABLE_NAME = "data_freshness"
MAX_STALE_HOURS = 48.0


def ensure_table():
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                symbol      TEXT NOT NULL,
                date        TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'API',
                staleness_hours REAL DEFAULT 0,
                api_status  TEXT DEFAULT 'OK',
                data_hash   TEXT,
                metadata_json TEXT,
                PRIMARY KEY (symbol, date)
            )
        """)


def upsert_freshness(
    symbol: str,
    date: str,
    source: str = "API",
    api_status: str = "OK",
    data_row: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Ghi/Cập nhật metadata độ tươi cho một (symbol, date).

    Args:
        symbol: Mã cổ phiếu
        date: Ngày giao dịch (YYYY-MM-DD)
        source: Nguồn dữ liệu (API | CACHE | BOOTSTRAP | SYNTHETIC)
        api_status: Trạng thái API (OK | TIMEOUT | ERROR | NO_DATA)
        data_row: Dict OHLCV row để tính data_hash
        metadata: Dict bổ sung (ví dụ: reason, fallback_chain)
    """
    ensure_table()
    now = datetime.now().isoformat()
    data_hash = _compute_hash(data_row) if data_row else None
    meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
    with get_connection() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {TABLE_NAME} "
            f"(symbol, date, last_updated, source, staleness_hours, "
            f" api_status, data_hash, metadata_json) "
            f"VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (symbol, date, now, source, api_status, data_hash, meta_str),
        )


def check_staleness(symbol: str, date: str) -> Tuple[str, float, str]:
    """Kiểm tra độ tươi của một (symbol, date).

    Returns:
        Tuple (source, staleness_hours, api_status)
        Nếu không có metadata → source='UNKNOWN', staleness=MAX_STALE*2, status='NO_METADATA'
    """
    ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT source, staleness_hours, last_updated, api_status "
            f"FROM {TABLE_NAME} WHERE symbol=? AND date=?",
            (symbol, date),
        ).fetchone()
    if not row:
        return ("UNKNOWN", MAX_STALE_HOURS * 2, "NO_METADATA")

    source = row[0]
    last_updated = row[2]
    api_status = row[3]

    try:
        updated_dt = datetime.fromisoformat(last_updated)
        staleness = (datetime.now() - updated_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        staleness = MAX_STALE_HOURS

    return (source, staleness, api_status)


def get_stale_symbols(
    max_stale: float = MAX_STALE_HOURS,
    date: Optional[str] = None,
) -> List[str]:
    """Trả về danh sách symbol có staleness > max_stale cho một ngày."""
    ensure_table()
    today = date or datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT symbol FROM {TABLE_NAME} "
            f"WHERE date=? AND (staleness_hours > ? OR api_status != 'OK')",
            (today, max_stale),
        ).fetchall()
    return [r[0] for r in rows]


def mark_api_status(symbol: str, date: str, status: str, reason: Optional[str] = None):
    """Đánh dấu trạng thái API cho một (symbol, date)."""
    ensure_table()
    with get_connection() as conn:
        existing = conn.execute(
            f"SELECT metadata_json FROM {TABLE_NAME} WHERE symbol=? AND date=?",
            (symbol, date),
        ).fetchone()
    meta = {}
    if existing and existing[0]:
        try:
            meta = json.loads(existing[0])
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if reason:
        meta["reason"] = reason
    upsert_freshness(symbol, date, source="API", api_status=status, metadata=meta)


def get_backfill_gap(symbol: str) -> Tuple[Optional[str], Optional[str]]:
    """Xác định gap dữ liệu: (last_date_in_db, expected_date).

    Returns:
        Tuple (last_date_in_db, expected_date) hoặc (None, None) nếu đầy đủ.
    """
    ensure_table()
    with get_connection() as conn:
        last = conn.execute(
            "SELECT MAX(date) FROM daily_ohlcv WHERE symbol=?",
            (symbol,),
        ).fetchone()[0]
        # Expected = last trading day before today
        expected = datetime.now().strftime("%Y-%m-%d")
    if last and last >= expected:
        return (None, None)
    return (last, expected)


def resolve_staleness_for_consumer(
    symbol: str,
    date: str,
    max_hours: float = MAX_STALE_HOURS,
) -> Dict[str, Any]:
    """Consumer-facing check: trả về metadata để consumer quyết định.

    Returns:
        Dict {is_stale, staleness_hours, api_status, source, ttl_remaining}
        is_stale=True nếu staleness > max_hours hoặc không có metadata.
    """
    source, staleness, api_status = check_staleness(symbol, date)
    is_stale = staleness > max_hours or source == "UNKNOWN"
    ttl = max(0.0, max_hours - staleness) if not is_stale else 0.0
    return {
        "is_stale": is_stale,
        "staleness_hours": round(staleness, 2),
        "api_status": api_status,
        "source": source,
        "ttl_remaining": round(ttl, 2),
    }


def _compute_hash(data_row: Dict[str, Any]) -> str:
    """SHA256 hash của OHLCV row để phát hiện data drift."""
    canonical = json.dumps(data_row, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
