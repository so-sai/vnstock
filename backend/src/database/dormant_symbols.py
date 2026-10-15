"""Sổ đăng ký mã TREO/HỦY — bỏ qua (skip) TRƯỚC khi gọi API nguồn.

ĐO 2026-10-15 (trên store sau khi quarantine phiên bóng ma):
  - 1.515 mã cổ phiếu trong universe; 187 mã (12,3%) KHÔNG có một dòng HỢP LỆ nào
    trong 30 phiên gần nhất — đã ngừng giao dịch/hủy niêm yết (CEE dừng 07/2022,
    CTA 07/2024, CDH 11/2025, CCI 29/09/2026). Nguồn vẫn trả frame CŨ cho các mã này
    (không rỗng!) nên chỉ kiểm "frame có tồn tại" là bỏ sót hoàn toàn.
  - Mỗi lượt quét vẫn gọi API cho nhóm này: 1 request nếu nguồn trả frame stale,
    3 request (retry mù) nếu nguồn quăng RetryError ⇒ ~190 request/lượt bị đốt vô ích.
  - Nhóm này còn bị đếm vào mẫu số coverage toàn sàn, làm coverage trông thấp giả tạo.

HỢP ĐỒNG:
  - `dormant_symbols()` tính ĐỘNG từ store, KHÔNG phải danh sách tĩnh đóng băng.
  - Chỉ là bộ lọc ĐƯỜNG ĐI (skip request). KHÔNG dùng để xoá dữ liệu lịch sử và
    KHÔNG dùng để thay đổi mẫu số đã công bố của coverage — mẫu số do caller quyết.
  - `PTCK_INCLUDE_DORMANT=1` buộc quét đầy đủ. PHẢI chạy chế độ này định kỳ (tuần 1 lần)
    để mã treo hồi sinh không bị bỏ sót vĩnh viễn: mã treo mà niêm yết lại sẽ chỉ lộ ra
    ở phiên probe đầy đủ.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

try:  # chạy với root = backend (python -m src....)
    from src.database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL
except ModuleNotFoundError:  # chạy với root = backend/src
    from database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL

# Số phiên gần nhất để xét "còn giao dịch". 30 phiên ≈ 6 tuần giao dịch.
DORMANT_WINDOW_SESSIONS = 30

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "screener_cache.db"


def _connect(db_path=None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else _DEFAULT_DB
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def recent_sessions(db_path=None, window: int = DORMANT_WINDOW_SESSIONS) -> list[str]:
    """Danh sách `window` phiên gần nhất có trong store (mới → cũ)."""
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date DESC LIMIT ?", (window,)).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def dormant_symbols(db_path=None, window: int = DORMANT_WINDOW_SESSIONS, as_of: str | None = None) -> list[str]:
    """Mã cổ phiếu không có MỘT dòng hợp lệ nào trong `window` phiên gần nhất.

    `as_of` (YYYY-MM-DD) để tái lập trạng thái tại một thời điểm (point-in-time):
    chỉ xét các phiên ≤ `as_of`. Mặc định None = dùng 30 phiên mới nhất.
    """
    con = _connect(db_path)
    try:
        if as_of:
            sessions = [
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT date FROM daily_ohlcv WHERE date<=? ORDER BY date DESC LIMIT ?",
                    (as_of, window),
                )
            ]
        else:
            sessions = [
                r[0] for r in con.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date DESC LIMIT ?", (window,))
            ]
        if not sessions:
            return []
        lo = min(sessions)
        universe = {r[0] for r in con.execute(f"SELECT DISTINCT symbol FROM daily_ohlcv WHERE {STOCK_ONLY_SQL}")}
        alive = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT symbol FROM daily_ohlcv WHERE date>=? AND {VALID_ROW_SQL}",
                (lo,),
            )
        }
    finally:
        con.close()
    return sorted(universe - alive)


def include_dormant() -> bool:
    """True khi caller muốn quét đầy đủ (kể cả mã treo) — dùng cho lượt probe định kỳ."""
    return os.environ.get("PTCK_INCLUDE_DORMANT") == "1"


def filter_sweep_symbols(symbols, db_path=None, as_of: str | None = None):
    """Bỏ các mã treo khỏi danh sách gọi API. Trả (còn_lại, danh_sách_đã_bỏ).

    PTCK_INCLUDE_DORMANT=1 ⇒ không bỏ gì (đường dò mã hồi sinh).
    """
    if include_dormant():
        return list(symbols), []
    dead = set(dormant_symbols(db_path, as_of=as_of))
    keep = [s for s in symbols if s not in dead]
    return keep, [s for s in symbols if s in dead]
