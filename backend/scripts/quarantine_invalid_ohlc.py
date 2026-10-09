"""Cách ly các dòng OHLC KHÔNG HỢP LỆ khỏi tập dữ liệu "sạch" (is_stale = 0).

Bối cảnh (2026-10-09): một lần backfill lỗi ghi 34.157 dòng `close = 0` (nguồn kbs,
năm 2026) nhưng vẫn để `is_stale = 0`. Hệ quả không phải "thiếu dữ liệu" mà là dữ
liệu SAI được dùng như thật: momentum chia cho giá ~0 tạo ra lợi suất khổng lồ, đẩy
HKB/ACM/DVG lên RS 99 — đầu bảng xếp hạng toàn thị trường — và làm ô nhiễm
`market_rs.json` của hệ thống thật.

Cùng định nghĩa hợp lệ với `src/database/data_validity.py`:
    (close > 0) ∧ (low ≤ close ≤ high) ∧ (volume ≥ 0)

NGUYÊN TẮC: Quarantine > Delete. Không xoá dòng nào — chỉ đặt `is_stale = 1` và gắn
nhãn vào `source` để truy vết được nguồn gốc sai.

Mặc định DRY-RUN. Phải truyền `--apply` mới thực sự ghi (fail-closed).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.database.data_validity import (  # noqa: E402
    INVALID_ROW_SQL,
    TAG_EMPTY_ROW,
    TAG_INVALID_OHLC,
    TAG_ZERO_PRICE,
    quarantine_condition,
)

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# Ba đợt cách ly, chạy theo thứ tự. Các đợt KHÔNG chồng lấn vì mỗi đợt chỉ nhắm dòng
# còn is_stale=0, và vì phép so sánh với NULL cho ra NULL (không khớp) ở đợt 1–2.
# Ba nhóm này đến từ CÙNG một lần backfill lỗi (nguồn kbs, 2026-04-17 → 2026-09-25).
BATCHES: list[tuple[str, str, str]] = [
    (
        "dòng rỗng hoàn toàn (mọi cột NULL)",
        "close IS NULL OR low IS NULL OR high IS NULL OR volume IS NULL",
        TAG_EMPTY_ROW,
    ),
    ("giá ≤ 0 (nến chia cho 0)", "close <= 0", TAG_ZERO_PRICE),
    ("OHLC tự mâu thuẫn", "low > close OR close > high OR low > high", TAG_INVALID_OHLC),
]


def _count(con: sqlite3.Connection, where: str) -> tuple[int, int]:
    row = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_ohlcv WHERE {where}"
    ).fetchone()
    return int(row[0]), int(row[1])


def run(db: Path, apply: bool) -> int:
    con = sqlite3.connect(str(db))
    print(f"DB: {db}")
    print(f"CHẾ ĐỘ: {'APPLY (ghi thật)' if apply else 'DRY-RUN (không ghi)'}\n")

    before_invalid = _count(con, f"{INVALID_ROW_SQL} AND is_stale = 0")
    print(f"Trước: {before_invalid[0]:,} dòng KHÔNG hợp lệ đang mang is_stale = 0 ({before_invalid[1]} mã)\n")

    total = 0
    for label, expr, tag in BATCHES:
        where = quarantine_condition(expr)
        n, syms = _count(con, where)
        print(f"  · {label:<32} {n:>8,} dòng ({syms:>4} mã)  → nhãn '{tag}'")
        if apply and n:
            cur = con.execute(
                f"UPDATE daily_ohlcv SET is_stale = 1, source = source || '|{tag}' WHERE {where}"
            )
            if cur.rowcount != n:
                con.rollback()
                print(f"\n❌ DỪNG: rowcount {cur.rowcount} ≠ số đếm trước {n}. Đã rollback.")
                return 2
        total += n
    print(f"\nTổng sẽ cách ly: {total:,} dòng")

    if apply:
        con.commit()
        after = _count(con, f"{INVALID_ROW_SQL} AND is_stale = 0")
        print(f"\nSau: {after[0]:,} dòng KHÔNG hợp lệ còn mang is_stale = 0 ({after[1]} mã)")
        if after[0] != 0:
            print("❌ THẤT BẠI: vẫn còn dòng không hợp lệ được coi là sạch.")
            return 1
        print("✅ Sạch: mọi dòng is_stale = 0 đều hợp lệ.")
    else:
        print("\n(dry-run — chưa ghi gì. Thêm --apply để thực thi.)")
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Cách ly dòng OHLC không hợp lệ")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định chỉ dry-run)")
    a = ap.parse_args()
    return run(a.db, a.apply)


if __name__ == "__main__":
    sys.exit(main())
