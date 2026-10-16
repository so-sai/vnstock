#!/usr/bin/env python
"""Migration: `regime_history.is_degraded` (Mục 1/5 backlog — GO 2026-10-16).

[WHY]
Phiên mà bảng độ rộng KHÔNG đủ phủ (`market_wide_breadth_daily.degraded = 1`) vẫn được ghi vào
`regime_history` như một phiên bình thường, không kèm cờ ⇒ tầng TÍNH TÍN HIỆU đọc chéo sẽ ăn cả
phiên degraded như phiên thật. Hợp đồng 2 tầng đã chốt: tầng TÍNH TOÁN lọc cứng `degraded=0`,
tầng HIỂN THỊ giữ dòng + phơi cờ.

[ROOT CAUSE]
Writer ghi từ board mà không truyền cờ suy giảm ⇒ không có cột nào để tầng đọc lọc. Giải pháp
"lọc ở tầng đọc bằng cách join breadth" (đã dùng tạm) phụ thuộc vào việc JOIN đúng ngày; đưa cờ
vào CHÍNH BẢNG thì mọi reader có một vị từ duy nhất, kiểm chứng được.

[INVARIANT]
1. ADDITIVE + IDEMPOTENT; không đổi số dòng, không đổi khoảng ngày.
2. Giá trị cờ được SUY RA TỪ NGUỒN ĐO ĐƯỢC (`market_wide_breadth_daily.degraded` cùng ngày), KHÔNG
   suy đoán: dòng không khớp được phiên nào trong bảng độ rộng ⇒ để **NULL** (không biết ≠ 0).
3. Reader tầng tín hiệu phải lọc `is_degraded = 0`; gặp NULL hoặc thiếu cột ⇒ fail-closed.

Chạy:  python backend/scripts/migrate_regime_history_is_degraded.py [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "backend" / "data" / "screener_cache.db"

DERIVE_SQL = """
UPDATE regime_history
   SET is_degraded = (
       SELECT b.degraded FROM market_wide_breadth_daily b WHERE b.date = regime_history.date
   )
 WHERE EXISTS (SELECT 1 FROM market_wide_breadth_daily b WHERE b.date = regime_history.date)
   AND (is_degraded IS NOT (SELECT b.degraded FROM market_wide_breadth_daily b WHERE b.date = regime_history.date))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    p = Path(a.db)
    if not p.exists():
        print(f"ERR: không thấy {p}")
        return 2

    conn = sqlite3.connect(str(p), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        cols = {c[1] for c in conn.execute("PRAGMA table_info(regime_history)")}
        before_n = conn.execute("SELECT COUNT(*) FROM regime_history").fetchone()[0]
        before_span = conn.execute("SELECT MIN(date), MAX(date) FROM regime_history").fetchone()
        matched = conn.execute(
            "SELECT COUNT(*) FROM regime_history r WHERE EXISTS "
            "(SELECT 1 FROM market_wide_breadth_daily b WHERE b.date = r.date)"
        ).fetchone()[0]
        derived_deg = conn.execute(
            "SELECT COUNT(*) FROM regime_history r JOIN market_wide_breadth_daily b ON b.date = r.date "
            "WHERE b.degraded = 1"
        ).fetchone()[0]

        if not a.apply:
            print("=== DRY-RUN ===")
            print(f"   regime_history: {before_n:,} dòng | {before_span[0]} → {before_span[1]}")
            print(f"   suy ra được cờ: {matched:,}/{before_n:,} dòng (khớp ngày với bảng độ rộng)")
            print(f"   trong đó degraded=1: {derived_deg}")
            print(f"   cột is_degraded: {'đã có' if 'is_degraded' in cols else 'sẽ THÊM'}")
            if before_n - matched:
                print(f"   ⚠ {before_n - matched} dòng KHÔNG suy ra được ⇒ để NULL (không biết ≠ 0)")
            print("\nChạy lại với --apply để ghi thật.")
            return 0

        if "is_degraded" not in cols:
            conn.execute("ALTER TABLE regime_history ADD COLUMN is_degraded INTEGER")
            print("   + regime_history.is_degraded (INTEGER, KHÔNG default 0 — 'không biết' phải là NULL)")
        n = conn.execute(DERIVE_SQL)
        changed = getattr(conn, "total_changes", 0)
        conn.commit()
        print(f"   UPDATE suy ra cờ: {changed:,} dòng đã đặt giá trị")

        after_n = conn.execute("SELECT COUNT(*) FROM regime_history").fetchone()[0]
        after_span = conn.execute("SELECT MIN(date), MAX(date) FROM regime_history").fetchone()
        dist = dict(
            conn.execute("SELECT COALESCE(CAST(is_degraded AS TEXT),'NULL'), COUNT(*) FROM regime_history GROUP BY 1")
        )
        print("=== ĐÃ GHI ===")
        print(f"   dòng: {before_n:,} → {after_n:,} | khoảng ngày: {before_span} → {after_span}")
        print(f"   phân bố is_degraded: {dist}")
        print(f"   phiên degraded gần nhất: {[r[0] for r in conn.execute('SELECT date FROM regime_history WHERE is_degraded = 1 ORDER BY date DESC LIMIT 8')]}")
        ok = before_n == after_n and before_span == after_span
        if dist.get("1") != derived_deg:
            print(f"ERR: số phiên degraded ({dist.get('1')}) != suy ra được ({derived_deg})")
            ok = False
        return 0 if ok else 1
    except sqlite3.OperationalError as exc:
        print(f"ERR: SQLite từ chối ({exc}) — tiến trình khác đang giữ DB? KHÔNG tự retry mù.")
        return 3
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
