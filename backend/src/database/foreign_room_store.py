"""Kho lưu trữ Room Ngoại Hàng ngày (`foreign_room_daily` trong `screener_cache.db`).

[WHY] Tách biệt dung lượng room ngoại còn lại (`current_room`, `total_room`) khỏi
`market_foreign_history` (dòng tiền mua/bán ròng) và `daily_ohlcv` để bảo toàn provenance.

[ROOT CAUSE] Dữ liệu `price_board` là ảnh chụp trạng thái tức thời, không mang ngày lịch sử;
nếu backfill ngày quá khứ hoặc suy ngược từ mua/bán ròng sẽ làm sai lệch số liệu khi có ESOP/phát hành thêm.

[INVARIANT]
1. Chỉ cho phép ghi `foreign_room_daily` cho đúng ngày hiện tại sau 15:00 (`BACKFILL_BLOCKED` cho ngày cũ).
2. Phân biệt rạch ròi `0` (kín room thực tế) và `None` (thiếu dữ liệu — cấm `fillna(0)`).
3. Đọc tại `as_of_date` chỉ khớp đúng `date = as_of_date`, cấm fallback sang ngày cũ hơn.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import pandas as pd

from src.database.db_core import save_data_upsert

# [WHY]: Chỉ cho phép chốt snapshot room ngoại từ price_board sau 15:00 (giờ đóng cửa ATC HOSE/HNX).
MARKET_CLOSE_HOUR = 15

# [WHY]: Hệ số nhân đổi tỷ lệ room ngoại sang phần trăm (= 100.0) thống nhất với valuation/MSCI.
PCT_SCALE = 100.0

# [WHY]: Làm tròn 2 chữ số thập phân cho % room ngoại còn lại để hiển thị nhất quán trong JSON.
DECIMALS_PCT = 2

# [WHY]: Room ngoại hợp lệ không thể âm (< 0 là nhiễu dữ liệu nguồn → chuyển None theo Missing > Fake).
MIN_VALID_ROOM = 0.0

FOREIGN_ROOM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS foreign_room_daily (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    current_room REAL,
    total_room REAL,
    foreign_ownership_pct REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""


def _is_finite_non_neg(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        num = float(value)
    except TypeError, ValueError:
        return False
    return math.isfinite(num) and num >= MIN_VALID_ROOM


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_foreign_room_table(conn: sqlite3.Connection) -> None:
    """Khởi tạo bảng `foreign_room_daily` nếu chưa tồn tại."""
    conn.execute(FOREIGN_ROOM_TABLE_SQL)
    conn.commit()


def validate_room_snapshot_window(
    target_date: str,
    *,
    now_dt: datetime | None = None,
    require_after_close: bool = True,
) -> datetime:
    """Chốt chặn Bất biến #1: chỉ cho phép ghi room từ `price_board` cho đúng ngày hôm nay (và sau 15:00)."""
    dt = now_dt if now_dt is not None else datetime.now()
    today_str = dt.strftime("%Y-%m-%d")
    if target_date != today_str:
        raise ValueError(f"BACKFILL_BLOCKED: foreign_room_daily(target={target_date}) chỉ hợp lệ với hôm nay ({today_str})")
    if require_after_close and dt.hour < MARKET_CLOSE_HOUR:
        raise ValueError(f"MARKET_NOT_CLOSED: chỉ chốt foreign_room_daily sau {MARKET_CLOSE_HOUR}:00 (hiện tại {dt:%H:%M})")
    return dt


def extract_foreign_room_frame(
    df_pb: pd.DataFrame,
    target_date: str,
    *,
    source: str = "kbs_price_board",
    now_dt: datetime | None = None,
    require_after_close: bool = True,
) -> pd.DataFrame:
    """Trích xuất DataFrame chuẩn hóa cho `foreign_room_daily` từ output `price_board` (KBS/VCI)."""
    dt = validate_room_snapshot_window(
        target_date,
        now_dt=now_dt,
        require_after_close=require_after_close,
    )
    if df_pb is None or df_pb.empty:
        return pd.DataFrame()

    df_norm = df_pb.copy()
    # Hỗ trợ cả MultiIndex column (VCI) lẫn flat column (KBS)
    flat_cols: list[str] = []
    for col in df_norm.columns:
        if isinstance(col, tuple):
            flat_cols.append(str(col[-1]).strip().lower())
        else:
            flat_cols.append(str(col).strip().lower())
    df_norm.columns = flat_cols

    if "symbol" not in df_norm.columns:
        return pd.DataFrame()

    room_col = next((c for c in ("foreign_room", "current_room") if c in df_norm.columns), None)
    if room_col is None:
        return pd.DataFrame()

    total_col = "total_room" if "total_room" in df_norm.columns else None
    pct_col = next(
        (c for c in ("foreign_ownership_pct", "foreign_percent", "current_holding_ratio") if c in df_norm.columns),
        None,
    )

    updated_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
    records: list[dict[str, Any]] = []
    for _, row in df_norm.iterrows():
        sym_raw = row.get("symbol")
        if not isinstance(sym_raw, str):
            continue
        sym = sym_raw.strip().upper()
        if not sym:
            continue

        cur_raw = row.get(room_col)
        cur_val = float(cur_raw) if _is_finite_non_neg(cur_raw) else None
        tot_raw = row.get(total_col) if total_col else None
        tot_val = float(tot_raw) if _is_finite_non_neg(tot_raw) else None
        pct_raw = row.get(pct_col) if pct_col else None
        pct_val = float(pct_raw) if _is_finite_non_neg(pct_raw) else None

        if cur_val is None and tot_val is None and pct_val is None:
            continue

        records.append(
            {
                "symbol": sym,
                "date": target_date,
                "current_room": cur_val,
                "total_room": tot_val,
                "foreign_ownership_pct": pct_val,
                "source": source,
                "updated_at": updated_at,
            }
        )

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).drop_duplicates(subset=["symbol", "date"], keep="last")


def upsert_foreign_room_rows(
    conn: sqlite3.Connection,
    df_room: pd.DataFrame,
    target_date: str,
    *,
    now_dt: datetime | None = None,
    require_after_close: bool = True,
) -> int:
    """Ghi batch room ngoại vào `foreign_room_daily` sau khi vượt qua chốt chặn ngày & giờ chốt phiên."""
    validate_room_snapshot_window(
        target_date,
        now_dt=now_dt,
        require_after_close=require_after_close,
    )
    if df_room is None or df_room.empty:
        return 0
    ensure_foreign_room_table(conn)
    save_data_upsert("foreign_room_daily", df_room, conn)
    return len(df_room)


def _lookup_shares_outstanding(financial_conn: sqlite3.Connection | None, symbol: str) -> float | None:
    if financial_conn is None:
        return None
    if _table_exists(financial_conn, "shares_history"):
        row = financial_conn.execute(
            """
            SELECT shares_outstanding
            FROM shares_history
            WHERE symbol = ? AND shares_outstanding IS NOT NULL AND shares_outstanding > 0
            ORDER BY period DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is not None and _is_finite_non_neg(row[0]) and float(row[0]) > 0.0:
            return float(row[0])
    if _table_exists(financial_conn, "financial_facts"):
        row = financial_conn.execute(
            """
            SELECT value
            FROM financial_facts
            WHERE symbol = ? AND metric = 'SHARES_OUT' AND value IS NOT NULL AND value > 0
            ORDER BY period DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is not None and _is_finite_non_neg(row[0]) and float(row[0]) > 0.0:
            return float(row[0])
    return None


def fetch_foreign_room_map(
    screener_conn: sqlite3.Connection,
    symbols: Iterable[str],
    as_of_date: str,
    financial_conn: sqlite3.Connection | None = None,
) -> dict[str, tuple[float | None, float | None]]:
    """Đọc `(current_room, available_room_pct)` khớp đúng `date = as_of_date` (cấm fallback ngày cũ)."""
    sym_list = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    result: dict[str, tuple[float | None, float | None]] = {s: (None, None) for s in sym_list}
    if not sym_list or not as_of_date or not _table_exists(screener_conn, "foreign_room_daily"):
        return result

    placeholders = ", ".join(["?"] * len(sym_list))
    rows = screener_conn.execute(
        f"""
        SELECT symbol, current_room
        FROM foreign_room_daily
        WHERE date = ? AND symbol IN ({placeholders})
        """,
        (as_of_date, *sym_list),
    ).fetchall()

    for sym_raw, cur_raw in rows:
        sym = str(sym_raw).strip().upper()
        if not _is_finite_non_neg(cur_raw):
            continue
        cur_val = float(cur_raw)
        shares_out = _lookup_shares_outstanding(financial_conn, sym)
        pct_val = round((cur_val / shares_out) * PCT_SCALE, DECIMALS_PCT) if shares_out else None
        result[sym] = (cur_val, pct_val)
    return result
