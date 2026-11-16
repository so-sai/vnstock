"""Tests cho `src.database.foreign_room_store` (Phase 1d Room Ngoại Hàng ngày).

[WHY] Bảo vệ 3 bất biến cốt lõi: phân biệt `0` (kín room) với `None` (thiếu dữ liệu),
cấm fallback ngày cũ khi lệch `as_of_date`, và chặn backfill quá khứ bằng `price_board`.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.database.foreign_room_store import (
    ensure_foreign_room_table,
    extract_foreign_room_frame,
    fetch_foreign_room_map,
    upsert_foreign_room_rows,
)


def test_foreign_room_blocks_past_date_and_before_close() -> None:
    """[WHY] Cấm ghi `price_board` cho ngày quá khứ (`BACKFILL_BLOCKED`) hoặc trước 15:00."""
    df_pb = pd.DataFrame([{"symbol": "PNJ", "foreign_room": 129_065_729}])
    now_after_close = datetime(2026, 11, 16, 15, 15, 0)
    with pytest.raises(ValueError, match="BACKFILL_BLOCKED"):
        extract_foreign_room_frame(df_pb, "2026-11-13", now_dt=now_after_close)

    now_intraday = datetime(2026, 11, 16, 14, 15, 0)
    with pytest.raises(ValueError, match="MARKET_NOT_CLOSED"):
        extract_foreign_room_frame(df_pb, "2026-11-16", now_dt=now_intraday)


def test_foreign_room_distinguishes_zero_vs_none_and_blocks_stale_date(tmp_path: Path) -> None:
    """[WHY] Phân biệt `0` (kín room) với `None` (thiếu) và trả `(None, None)` nếu lệch `as_of_date`."""
    scr_db = tmp_path / "screener.db"
    fin_db = tmp_path / "fin.db"
    now_after = datetime(2026, 11, 16, 15, 20, 0)

    df_pb = pd.DataFrame(
        [
            {"symbol": "FPT", "foreign_room": 0},
            {"symbol": "PNJ", "foreign_room": 129_065_729},
            {"symbol": "BAD", "foreign_room": float("nan")},
        ]
    )
    df_room = extract_foreign_room_frame(df_pb, "2026-11-16", now_dt=now_after)
    assert set(df_room["symbol"]) == {"FPT", "PNJ"}

    with contextlib.closing(sqlite3.connect(scr_db)) as sconn, contextlib.closing(sqlite3.connect(fin_db)) as fconn:
        ensure_foreign_room_table(sconn)
        written = upsert_foreign_room_rows(sconn, df_room, "2026-11-16", now_dt=now_after)
        assert written == 2

        fconn.execute(
            "CREATE TABLE shares_history (symbol TEXT, period TEXT, shares_outstanding REAL)"
        )
        fconn.execute("INSERT INTO shares_history VALUES ('FPT', '2026Q2', 1939904415.0)")
        fconn.execute("INSERT INTO shares_history VALUES ('PNJ', '2026Q2', 512413373.0)")
        fconn.commit()

        # Đúng ngày 2026-11-16: FPT = 0.0 (0.0%), PNJ = 129,065,729 (25.19%), MISSING = (None, None)
        res_today = fetch_foreign_room_map(sconn, ["FPT", "PNJ", "MISSING"], "2026-11-16", fconn)
        assert res_today["FPT"] == (0.0, 0.0)
        assert res_today["PNJ"][0] == 129_065_729.0
        assert res_today["PNJ"][1] == 25.19
        assert res_today["MISSING"] == (None, None)

        # Lệch ngày (chỉ có 2026-11-16 nhưng hỏi 2026-11-17) -> bắt buộc (None, None), cấm mượn ngày cũ
        res_stale = fetch_foreign_room_map(sconn, ["FPT", "PNJ"], "2026-11-17", fconn)
        assert res_stale["FPT"] == (None, None)
        assert res_stale["PNJ"] == (None, None)
