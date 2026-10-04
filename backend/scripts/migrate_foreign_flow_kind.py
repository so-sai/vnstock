"""migrate_foreign_flow_kind.py — F2: gắn nhãn ngữ nghĩa cho market_foreign_history.

BỐI CẢNH (audit 2026-10-04):
  Bảng trộn 2 ngữ nghĩa dù CÙNG đơn vị (tỷ đồng):
   - date <= 2026-07-31 : MỨC TÍCH LŨY (level) — tổng toàn thị trường gần hằng số (CV 13.8%)
   - date >= 2026-08-03 : DÒNG CHẢY RÒNG THEO NGÀY (flow) — tổng dao động quanh 0 (CV 350%)

  KHÔNG rescale (đơn vị đã đồng nhất). Chỉ GẮN NHÃN để consumer lọc đúng.

Idempotent: chạy lại an toàn.

Usage:
    python backend/scripts/migrate_foreign_flow_kind.py [--db <path>] [--cutoff 2026-07-31]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
DEFAULT_CUTOFF = "2026-07-31"
LEGACY_LABEL = "legacy_level"
DAILY_LABEL = "daily"


def migrate(db_path: Path, cutoff: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(market_foreign_history)")]
        if not cols:
            raise RuntimeError("market_foreign_history không tồn tại")
        added = False
        if "flow_kind" not in cols:
            conn.execute(
                "ALTER TABLE market_foreign_history ADD COLUMN flow_kind TEXT DEFAULT 'daily'"
            )
            added = True
        # Gắn nhãn (idempotent — ghi đè cả 2 nhánh để tự sửa nếu lần trước sai)
        cur_legacy = conn.execute(
            "UPDATE market_foreign_history SET flow_kind = ? WHERE date <= ?",
            (LEGACY_LABEL, cutoff),
        ).rowcount
        cur_daily = conn.execute(
            "UPDATE market_foreign_history SET flow_kind = ? WHERE date > ?",
            (DAILY_LABEL, cutoff),
        ).rowcount
        conn.commit()

        counts = dict(
            conn.execute(
                "SELECT flow_kind, COUNT(*) FROM market_foreign_history GROUP BY flow_kind"
            ).fetchall()
        )
        return {
            "added_column": added,
            "marked_legacy": cur_legacy,
            "marked_daily": cur_daily,
            "counts": counts,
            "cutoff": cutoff,
        }
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    args = ap.parse_args()
    res = migrate(Path(args.db), args.cutoff)
    print(f"  DB: {args.db}")
    print(f"  Thêm cột flow_kind: {res['added_column']}")
    print(f"  Gắn legacy_level (<= {res['cutoff']}): {res['marked_legacy']} dòng")
    print(f"  Gắn daily         (>  {res['cutoff']}): {res['marked_daily']} dòng")
    print(f"  Tổng theo nhãn: {res['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
