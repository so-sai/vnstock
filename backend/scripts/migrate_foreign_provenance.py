"""migrate_foreign_provenance.py — B2: thêm provenance cho market_foreign_history.

Thêm 3 cột (idempotent — chạy lại vô hại):
    source      TEXT DEFAULT 'unknown'   : nguồn dữ liệu (vd 'vci_price_board')
    fetched_at  TEXT                     : thời điểm chụp (ISO)
    is_verified INTEGER DEFAULT 0        : 0 = chưa kiểm định độc lập, 1 = đã

WHY: `flow_kind` mới chỉ nói NGỮ NGHĨA (daily vs legacy_level), chưa nói XUẤT XỨ.
Không có provenance thì 3 tháng sau không biết số liệu đến từ đâu → không thể
truy vết khi guard nghi ngờ. Nguyên tắc: no provenance = no trust.

Dữ liệu cũ KHÔNG bị đổi giá trị: chỉ nhận default. Bảng 103.965 dòng giữ nguyên
số, nên mọi query đọc hiện tại của CLI không bị ảnh hưởng.

Usage:
    python backend/scripts/migrate_foreign_provenance.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

NEW_COLUMNS = [
    ("source", "TEXT DEFAULT 'unknown'"),
    ("fetched_at", "TEXT"),
    ("is_verified", "INTEGER DEFAULT 0"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM market_foreign_history").fetchone()[0]
        have = existing_columns(conn, "market_foreign_history")
        print(f"=== MIGRATE provenance market_foreign_history ({before:,} dòng) ===")
        print(f"    cột hiện có: {sorted(have)}")

        todo = [(name, ddl) for name, ddl in NEW_COLUMNS if name not in have]
        if not todo:
            print("    → đã đủ cột, không cần migrate (idempotent OK)")
            return 0

        for name, ddl in todo:
            sql = f"ALTER TABLE market_foreign_history ADD COLUMN {name} {ddl}"
            if args.dry_run:
                print(f"    [dry-run] {sql}")
                continue
            conn.execute(sql)
            print(f"    ✅ thêm cột {name} {ddl}")

        if not args.dry_run:
            conn.commit()

        after_cols = existing_columns(conn, "market_foreign_history")
        print(f"    cột sau : {sorted(after_cols)}")

        for name, _ in NEW_COLUMNS:
            if name in after_cols:
                vals = conn.execute(
                    f"SELECT COALESCE({name}, '<NULL>'), COUNT(*) FROM market_foreign_history GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
                ).fetchall()
                print(f"    phân bố {name}: {vals}")

        after = conn.execute("SELECT COUNT(*) FROM market_foreign_history").fetchone()[0]
        print(f"    số dòng sau: {after:,} (trước {before:,}) " + ("✅ khớp" if after == before else "❌ LỆCH"))
        if after != before:
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
