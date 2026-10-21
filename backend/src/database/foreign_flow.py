"""foreign_flow.py — CỬA ĐỌC DUY NHẤT cho tổng dòng ngoại đã kiểm chứng.

[WHY]
  `market_foreign_history` trộn hai thế hệ (xem `scripts/create_foreign_flow_view.py`): `daily` đúng
  đơn vị tỷ đồng, `legacy_level` sai scale (50.675 dòng cộng ra −106 triệu tỷ). Tổng trần trên bảng gốc
  vẫn "chạy" mà không báo lỗi ⇒ phải có một cửa duy nhất, đọc qua view `v_market_foreign_daily`.

[ROOT CAUSE]
  Không có API đọc nào bắt buộc lọc `flow_kind`; mỗi consumer tự viết SQL nên consumer mới rất dễ quên.

[INVARIANT]
  1. Chỉ đọc qua view. View thiếu ⇒ **raise** (fail-closed), tuyệt đối không fallback về bảng gốc.
  2. Tripwire độ lớn: |tổng| > `MAX_PLAUSIBLE_ABS_TY` (1 triệu tỷ đồng) ⇒ **raise**. Đây là mức bất khả
     thi về mặt kinh tế cho cửa sổ vài năm; chạm ngưỡng nghĩa là đã trộn đơn vị ⇒ phải dừng, không trả số.
"""

from __future__ import annotations

import sqlite3

VIEW = "v_market_foreign_daily"
MAX_PLAUSIBLE_ABS_TY = 1_000_000.0  # tỷ đồng


def daily_net_value(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> float:
    """Tổng net_value (tỷ đồng) của RIÊNG thế hệ `daily`, trong khoảng [start, end] (bao gồm)."""
    where: list[str] = []
    params: list[str] = []
    if start:
        where.append("date >= ?")
        params.append(start)
    if end:
        where.append("date <= ?")
        params.append(end)
    sql = f"SELECT COALESCE(SUM(net_value), 0) FROM {VIEW}"
    if where:
        sql += " WHERE " + " AND ".join(where)

    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"View {VIEW} chưa tồn tại — chạy backend/scripts/create_foreign_flow_view.py ({exc})") from exc

    total = float(row[0] or 0.0) if row else 0.0
    if abs(total) > MAX_PLAUSIBLE_ABS_TY:
        raise ValueError(f"Tổng net_value bất khả thi ({total:,.0f} tỷ đồng) — nghi trộn đơn vị/legacy lọt vào view; DỪNG")
    return total
