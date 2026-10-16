"""Hàng rào cho dữ liệu vi cấu trúc TỔNG HỢP (synthetic).

[WHY]
`volume_profile` (54.765 dòng) và `active_demand` (2.835 dòng) trông rất "hợp mắt" (POC/VAH/VAL,
active demand, strength) nhưng theo truy vết 2026-10-16 chúng là **ước lượng mô hình**: writer
`src/financial/market_behavior_engine.py::compute_volume_profile` lấy `daily_ohlcv` (EOD) rồi CHIA ĐỀU
khối lượng của phiên lên dải `[low, high]` (`vol_per_unit = vol / span`). Không có khối lượng khớp thật
tại từng mức giá. Đọc POC như số liệu quan sát được là **tự lừa mình bằng số đúng định dạng**.

[ROOT CAUSE]
Bảng không có cột nào khai báo bản chất dữ liệu ⇒ consumer không có cách nào biết. Đã vá bằng migration
`backend/scripts/migrate_label_synthetic_microstructure.py` (thêm `is_synthetic`, `method`, `derived_from`).

[INVARIANT]
1. Mọi đường TẠO TÍN HIỆU/ĐIỂM (signal/scoring/position sizing) **CẤM** tiêu thụ dòng `is_synthetic = 1`
   ⇒ phải lọc `SYNTHETIC_SAFE_SQL`, hoặc dừng lại nếu không kiểm chứng được nhãn.
2. Không đọc được nhãn (thiếu cột / NULL) ⇒ **fail-closed**: coi như synthetic và `raise`, KHÔNG mặc định
   là sạch. "Không biết" ≠ 0.
3. Tầng HIỂN THỊ (báo cáo/notebook) được phép đọc, nhưng bắt buộc kèm cờ `is_synthetic`.
"""

from __future__ import annotations

import sqlite3

#: Hai bảng vi cấu trúc đã được dán nhãn tổng hợp.
SYNTHETIC_TABLES: tuple[str, ...] = ("volume_profile", "active_demand")

#: Vị từ BẮT BUỘC cho mọi consumer thuộc đường tín hiệu.
SYNTHETIC_SAFE_SQL = "is_synthetic = 0"

#: Nhãn nhân quả cho thông điệp lỗi (audit trail khi cổng chặn).
LABEL_COLUMNS: tuple[str, ...] = ("is_synthetic", "method", "derived_from")


class SyntheticDataError(RuntimeError):
    """Consumer đường tín hiệu cố tiêu thụ dòng dữ liệu TỔNG HỢP (hoặc không kiểm chứng được nhãn)."""


def has_label(conn: sqlite3.Connection, table: str) -> bool:
    """Bảng đã có cột `is_synthetic` chưa? (chưa có ⇒ không được coi là dùng được)."""
    if table not in SYNTHETIC_TABLES:
        return True  # bảng ngoài danh sách nhãn: không phải việc của hàng rào này
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
    return "is_synthetic" in cols


def synthetic_safe_predicate(conn: sqlite3.Connection, table: str) -> str:
    """Trả về vị từ để ghép vào WHERE. Thiếu cột nhãn ⇒ `raise` (fail-closed)."""
    if not has_label(conn, table):
        raise SyntheticDataError(
            f"{table}: thiếu cột nhãn `is_synthetic` — KHÔNG thể khẳng định dòng nào là số đo thật. "
            "Chạy backend/scripts/migrate_label_synthetic_microstructure.py trước."
        )
    return SYNTHETIC_SAFE_SQL


def assert_consumable(rows: list[dict], *, table: str, consumer: str) -> None:
    """Chặn tiêu thụ dòng tổng hợp. `rows` = list[dict] đã đọc từ `table`.

    Dòng bị chặn nếu `is_synthetic == 1` HOẶC thiếu/None nhãn (fail-closed).
    """
    for i, row in enumerate(rows):
        label = row.get("is_synthetic")
        if label is None or int(label) == 1:
            method = row.get("method") or "KHÔNG RÕ"
            src = row.get("derived_from") or "KHÔNG RÕ"
            raise SyntheticDataError(
                f"{consumer}: dòng {i} của {table} là dữ liệu TỔNG HỢP "
                f"(is_synthetic={label}, method={method}, derived_from={src}) — "
                "cấm dùng cho tín hiệu/điểm; lọc `is_synthetic = 0` hoặc bỏ bảng này khỏi đường tín hiệu."
            )


def describe_labels(conn: sqlite3.Connection) -> dict[str, dict]:
    """Đo hiện trạng nhãn (dùng cho test canary trên store thật + báo cáo)."""
    out: dict[str, dict] = {}
    for t in SYNTHETIC_TABLES:
        cols = {c[1] for c in conn.execute(f"PRAGMA table_info({t})")}
        if "is_synthetic" not in cols:
            out[t] = {"labelled": False}
            continue
        total = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        synth = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE is_synthetic = 1").fetchone()[0]
        nulls = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE is_synthetic IS NULL").fetchone()[0]
        methods = [r[0] for r in conn.execute(f"SELECT DISTINCT method FROM {t}")]
        out[t] = {"labelled": True, "total": total, "synthetic": synth, "nulls": nulls, "methods": methods}
    return out
