"""create_foreign_flow_view.py — tạo VIEW sạch `v_market_foreign_daily` (idempotent).

[WHY]
  `market_foreign_history` chứa **hai thế hệ dữ liệu** trong cùng bảng:
    - `flow_kind='daily'`  (03/08/2026 → nay): net_value ĐÚNG đơn vị tỷ đồng, tổng cả kỳ ≈ −13.435 tỷ.
    - `flow_kind='legacy_level'` (05/04 → 31/07/2026, `source='unknown'`, `is_verified=0`): scale cũ bị
      nhân sai — đo 21/10/2026: 50.675 dòng cộng ra **−106.613.841 tỷ** (VIC 26/06 một phiên
      `net_value = −790.348 tỷ` ≈ 30 tỷ USD; 6.472 dòng |net_value| > 1.000 tỷ).
  Một `SELECT SUM(net_value)` trần trả về −106 triệu tỷ mà KHÔNG ném lỗi ⇒ sai 4 bậc độ lượng, im lặng.

[ROOT CAUSE]
  Cột `flow_kind` được thêm sau (migrate_foreign_flow_kind) để phân biệt thế hệ, nhưng không có cửa đọc
  duy nhất nào buộc consumer phải lọc ⇒ bảng gốc vẫn là "cửa mở" cho raw query.

[INVARIANT]
  1. Consumer đọc tổng dòng ngoại PHẢI qua `v_market_foreign_daily` (hoặc `src.database.foreign_flow`),
     không đọc bảng gốc bằng `SUM(net_value)` trần.
  2. View KHÔNG chứa bất kỳ dòng `flow_kind != 'daily'`.
  3. Script idempotent: chạy lại chỉ `CREATE VIEW IF NOT EXISTS`, không đụng dữ liệu.

Chạy: python backend/scripts/create_foreign_flow_view.py
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "screener_cache.db"
TABLE = "market_foreign_history"
VIEW = "v_market_foreign_daily"
DDL = f"CREATE VIEW IF NOT EXISTS {VIEW} AS SELECT * FROM {TABLE} WHERE flow_kind = 'daily'"


def ensure_view(conn: sqlite3.Connection) -> bool:
    """Tạo view nếu chưa có. Trả True nếu vừa tạo mới."""
    existed = (
        conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name=?", (VIEW,)).fetchone()[0] == 1
    )
    conn.execute(DDL)
    conn.commit()
    return not existed


def verify(conn: sqlite3.Connection) -> list[str]:
    """Kiểm sau tạo. Rỗng = đạt."""
    errs: list[str] = []
    n_view = conn.execute(f"SELECT COUNT(*) FROM {VIEW}").fetchone()[0]
    n_daily = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE flow_kind = 'daily'").fetchone()[0]
    if n_view != n_daily:
        errs.append(f"view có {n_view} dòng ≠ {n_daily} dòng flow_kind='daily'")
    leaked = conn.execute(f"SELECT COUNT(*) FROM {VIEW} WHERE flow_kind <> 'daily'").fetchone()[0]
    if leaked:
        errs.append(f"{leaked} dòng không phải 'daily' lọt vào view")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="Tạo VIEW v_market_foreign_daily (idempotent)")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()

    con = sqlite3.connect(str(a.db))
    created = ensure_view(con)
    print(f"DB: {a.db}")
    print(f"  [{'created' if created else 'already exists'}] view {VIEW}")

    raw = con.execute(f"SELECT COALESCE(SUM(net_value), 0) FROM {TABLE}").fetchone()[0]
    clean = con.execute(f"SELECT COALESCE(SUM(net_value), 0) FROM {VIEW}").fetchone()[0]
    print(f"  tổng TRẦN trên bảng gốc : {raw:>20,.1f} tỷ   <- có thế hệ legacy, KHÔNG dùng")
    print(f"  tổng qua VIEW sạch      : {clean:>20,.1f} tỷ")
    n_raw = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    n_view = con.execute(f"SELECT COUNT(*) FROM {VIEW}").fetchone()[0]
    print(f"  dòng loại khỏi view     : {n_raw - n_view:,}")

    checks = verify(con)
    if checks:
        print("  LỖI:")
        for c in checks:
            print(f"    [ERR] {c}")
        con.close()
        return 1
    print("  KẾT LUẬN: ĐẠT — view chỉ chứa flow_kind='daily'.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
