"""Cổng chặn "ngày tương lai" cho sổ kế toán paper (Mục 23, NQ-16 04/11/2026).

[WHY]
    Ngày 28/09/2026 một lần chạy test đã ghi 8 dòng mang niên độ `2099` vào CSDL
    thật (`paper_equity_curve`: 7 dòng `2099-01-04`..`2099-01-12`, `paper_cash_ledger`:
    1 dòng) với `portfolio_id = SEL_PAPER_V1` và `created_at = 2026-09-28T15:42`.
    Mọi consumer dùng `SELECT MAX(date)` / `ORDER BY date DESC` (Sentinel freshness,
    `daily-update`, `eod-run`, vẽ equity curve) sẽ đọc ra `2099-01-12` ⇒ tưởng hệ
    thống đã có dữ liệu tới năm 2099 ⇒ mọi chốt chặn độ trễ im lặng vô hiệu.

[ROOT CAUSE]
    Không tồn tại cổng nào khẳng định "ngày ghi vào sổ kế toán phải <= hôm nay".
    Sentinel 2099 là thói quen của test (để không đụng dữ liệu thật) nhưng lại trỏ
    vào CSDL thật. Thiếu vị từ ở tầng ghi ⇒ rác lọt vào và không ai phát hiện trong
    5 tuần. Bài học: bất biến phải nằm ở tầng ĐỌC/CHECK, không chỉ ở tầng ghi.

[INVARIANT]
    (a) Sau purge, 0 dòng có `date > as_of` trong bảng được chỉ định.
    (b) Purge CHỈ xoá phần tương lai: mọi dòng `date <= as_of` phải còn nguyên
        (kể cả dòng đúng bằng `as_of` — phiên hôm nay là dữ liệu thật).
    (c) Đếm-trước-xoá-sau: nếu số dòng sắp xoá khác `expected` ⇒ `RuntimeError`
        ("lệch"), không xoá gì. Sai ở đây là mất dữ liệu không hồi phục.
    (d) Không có `as_of` mặc định mờ nghĩa: caller phải truyền ngày tường minh
        (hoặc dùng `today_str()`), để tránh test vô tình so với đồng hồ hệ thống.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

# [WHY]: Chỉ hai bảng kế toán paper được phép purge tự động. Danh sách đóng (không
# nhận tên bảng từ tham số tự do) để một lệnh gọi sai không thể `DELETE` nhầm
# `daily_ohlcv`/`market_wide_breadth_daily` — nơi purge sẽ phá provenance thị trường
# và không có đường backfill nào bù lại được. — xoá/sửa danh sách này sẽ mở đường
# cho xoá mù dữ liệu thị trường.
PURGEABLE_LEDGER_TABLES: tuple[str, ...] = ("paper_equity_curve", "paper_cash_ledger")

# [WHY]: Chỉ chấp nhận tên cột định danh đơn giản (`[A-Za-z_][A-Za-z0-9_]*`) vì tên
# bảng/cột phải nội suy vào SQL (SQLite không bind được identifier). Mọi chuỗi khác
# ⇒ từ chối ngay, fail-closed, thay vì tin tưởng caller. — nới lỏng kiểm tra này sẽ
# biến module thành lỗ SQL injection có quyền DELETE.
_ALLOWED_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def today_str() -> str:
    """Ngày hôm nay theo `YYYY-MM-DD` (cùng định dạng cột `date` trong CSDL)."""
    return datetime.now().strftime("%Y-%m-%d")


def _check_ident(name: str, *, what: str) -> str:
    """Fail-closed trên identifier: rỗng/ký tự lạ/số đầu ⇒ raise."""
    if not name or not isinstance(name, str):
        raise ValueError(f"{what} phải là chuỗi không rỗng, nhận {name!r}")
    if not (name[0].isalpha() or name[0] == "_") or not set(name) <= _ALLOWED_IDENT_CHARS:
        raise ValueError(f"{what} không hợp lệ (chỉ [A-Za-z0-9_]): {name!r}")
    return name


def _check_ledger_table(table: str) -> str:
    """Chỉ cho phép bảng kế toán paper trong danh sách đóng."""
    _check_ident(table, what="tên bảng")
    # [WHY]: Danh sách đóng thay vì "bảng nào cũng được" — purge là thao tác không
    # hoàn tác; giới hạn bề mặt là cách duy nhất để một lệnh gọi sai không phá dữ
    # liệu thị trường. — mở rộng tuỳ tiện sẽ vô hiệu hoá bất biến (b) của module.
    if table not in PURGEABLE_LEDGER_TABLES:
        raise ValueError(f"bảng {table!r} không nằm trong danh sách được phép purge {PURGEABLE_LEDGER_TABLES}")
    return table


def _check_as_of(as_of: str) -> str:
    """`as_of` phải đúng `YYYY-MM-DD` — so sánh chuỗi trong SQLite cần định dạng chuẩn."""
    if not isinstance(as_of, str) or len(as_of) != 10:
        raise ValueError(f"as_of phải là chuỗi 'YYYY-MM-DD', nhận {as_of!r}")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"as_of không phải ngày hợp lệ: {as_of!r}") from exc
    return as_of


def count_future_rows(
    conn: sqlite3.Connection,
    table: str,
    date_col: str = "date",
    as_of: str | None = None,
) -> int:
    """Đếm số dòng có `date_col > as_of` (mặc định: hôm nay).

    Trả về 0 khi sạch. Không bao giờ "đoán" — bảng/cột không tồn tại thì để
    `sqlite3.OperationalError` nổi lên (fail-closed) thay vì trả 0.
    """
    _check_ledger_table(table)
    _check_ident(date_col, what="tên cột ngày")
    as_of = _check_as_of(as_of if as_of is not None else today_str())
    # [WHY]: `>` chứ không `>=` — dòng đúng bằng hôm nay là phiên thật vừa chốt sổ,
    # xoá nó là đứt chuỗi PnL. — đổi thành `>=` sẽ xoá mất chính phiên hôm nay.
    row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {date_col} > ?", (as_of,)).fetchone()
    return int(row[0]) if row else 0


def fetch_future_rows(
    conn: sqlite3.Connection,
    table: str,
    date_col: str = "date",
    as_of: str | None = None,
) -> list[dict]:
    """Trả về TOÀN BỘ nội dung các dòng tương lai dưới dạng `list[dict]`.

    [WHY]: Purge mà không lưu vết là phá provenance — đúng thứ hệ thống này cấm.
    Payload này được caller dump ra JSON cạnh file backup trước khi xoá, để luôn
    tái dựng được "đã xoá cái gì, vì sao". — bỏ bước này sẽ biến purge thành xoá mù.
    """
    _check_ledger_table(table)
    _check_ident(date_col, what="tên cột ngày")
    as_of = _check_as_of(as_of if as_of is not None else today_str())
    cursor = conn.execute(f"SELECT * FROM {table} WHERE {date_col} > ? ORDER BY {date_col}", (as_of,))
    # Lấy tên cột từ cursor để không phụ thuộc `conn.row_factory` của caller.
    names = [d[0] for d in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def purge_future_rows(
    conn: sqlite3.Connection,
    table: str,
    date_col: str = "date",
    as_of: str | None = None,
    expected: int | None = None,
) -> int:
    """Xoá các dòng `date_col > as_of`, trả về số dòng đã xoá.

    `expected`: nếu được truyền, phải khớp CHÍNH XÁC số dòng đếm được tại thời
    điểm xoá; lệch ⇒ `RuntimeError` chứa "lệch" và KHÔNG xoá gì (rollback).

    Không tự `commit()`: caller quyết định ranh giới transaction. Nhờ vậy lệnh gọi
    trong một transaction lớn (backup + purge + verify) vẫn nguyên tử.
    """
    _check_ledger_table(table)
    _check_ident(date_col, what="tên cột ngày")
    as_of = _check_as_of(as_of if as_of is not None else today_str())

    found = count_future_rows(conn, table, date_col, as_of)
    if expected is not None and found != expected:
        # [WHY]: Dừng trước khi xoá. Kịch bản thật: một test/script khác vừa ghi thêm
        # dòng 2099 giữa lúc kiểm đếm và lúc xoá — nếu xoá mù theo `expected` cũ thì
        # hoặc xoá thiếu (rác còn lại) hoặc xoá lố (mất dòng thật vừa ghi). — bỏ cổng
        # này là mất dữ liệu không hồi phục.
        raise RuntimeError(
            f"Số dòng tương lai của {table} bị lệch: đếm được {found}, kỳ vọng {expected} ⇒ TỪ CHỐI xoá (as_of={as_of})."
        )
    if found == 0:
        return 0

    # [WHY]: Xoá đúng tập đã đếm bằng cùng vị từ `>` — không dùng `rowid IN (...)` để
    # tránh trường hợp bảng không có rowid (WITHOUT ROWID) và để hành vi giống hệt
    # `count_future_rows`, dễ kiểm toán bằng mắt. — đổi vị từ ở đây sẽ phá bất biến (b).
    conn.execute(f"DELETE FROM {table} WHERE {date_col} > ?", (as_of,))
    return found
