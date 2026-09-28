"""Dedupe macro_history v1 + backfill source từ macro_history_v2 (E9, 2026-09-28).

Vấn đề (đo được trên screener_cache.db):
  - macro_history v1: 199.643 dòng nhưng chỉ 27.897 khoá (date, variable)
    → 171.746 dòng trùng (19.832 khoá bị >1 dòng). GOLD_XAU: 17.926 dòng / 1.374 ngày.
  - source NULL ở 199.585/199.643 dòng (macro_history_v2 thì 100% có source).

Nguyên tắc KHÔNG đổi quyết định:
  - Giữ MAX(rowid) mỗi khoá = đúng dòng mà classifier đọc
    (macro_state_classifier dùng ROW_NUMBER() ... ORDER BY rowid DESC).
  - backfill source chỉ UPDATE các ô đang NULL, không đụng giá trị.

Chạy:
    python backend/scripts/dedupe_macro_history_v1.py            # dry-run (mặc định)
    python backend/scripts/dedupe_macro_history_v1.py --apply    # có thao tác
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "screener_cache.db"
BACKUP_TABLE = "macro_history_bak_e9"
REPORT_SAMPLE = 15


def analyze(con: sqlite3.Connection) -> dict:
    """Thống kê trước/sau — chỉ đọc."""
    total = con.execute("SELECT COUNT(*) FROM macro_history").fetchone()[0]
    keys = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT date, variable FROM macro_history)").fetchone()[0]
    dup_keys = con.execute(
        "SELECT COUNT(*) FROM (SELECT date, variable FROM macro_history GROUP BY date, variable HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    dup_rows = total - keys
    src_null = con.execute("SELECT COUNT(*) FROM macro_history WHERE source IS NULL").fetchone()[0]

    # Các khoá trùng nhưng GIÁ TRỊ KHÁC NHAU → rủi ro reader khác chọn sai dòng
    differing = con.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT date, variable, COUNT(DISTINCT value) c FROM macro_history "
        "  GROUP BY date, variable HAVING c > 1)"
    ).fetchone()[0]
    sample = con.execute(
        "SELECT date, variable, COUNT(*), GROUP_CONCAT(value) FROM macro_history "
        "GROUP BY date, variable HAVING COUNT(*) > 1 ORDER BY date DESC LIMIT ?",
        (REPORT_SAMPLE,),
    ).fetchall()

    v2_match = con.execute(
        "SELECT COUNT(*) FROM macro_history m WHERE m.source IS NULL AND EXISTS ("
        "  SELECT 1 FROM macro_history_v2 v "
        "  WHERE v.variable = m.variable AND v.date = m.date)"
    ).fetchone()[0]

    return {
        "rows": total,
        "distinct_keys": keys,
        "duplicate_keys": dup_keys,
        "duplicate_rows": dup_rows,
        "source_null": src_null,
        "keys_with_differing_values": differing,
        "rows_backfillable_from_v2": v2_match,
        "dup_sample": sample,
    }


def dedupe(con: sqlite3.Connection) -> dict:
    """Xoá dòng trùng, giữ MAX(rowid) mỗi (date, variable). Idempotent."""
    before = analyze(con)
    cur = con.execute(
        "DELETE FROM macro_history WHERE rowid NOT IN (  SELECT MAX(rowid) FROM macro_history GROUP BY date, variable)"
    )
    removed = cur.rowcount
    con.commit()
    after = analyze(con)
    return {"removed_rows": removed, "before": before, "after": after}


def backfill_source(con: sqlite3.Connection) -> dict:
    """Điền source (đang NULL) từ macro_history_v2 theo (variable, date)."""
    before = con.execute("SELECT COUNT(*) FROM macro_history WHERE source IS NOT NULL").fetchone()[0]
    cur = con.execute(
        "UPDATE macro_history SET source = ("
        "  SELECT v.source FROM macro_history_v2 v "
        "  WHERE v.variable = macro_history.variable AND v.date = macro_history.date LIMIT 1"
        ") WHERE source IS NULL AND EXISTS ("
        "  SELECT 1 FROM macro_history_v2 v "
        "  WHERE v.variable = macro_history.variable AND v.date = macro_history.date)"
    )
    updated = cur.rowcount
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM macro_history WHERE source IS NOT NULL").fetchone()[0]
    return {"rows_updated": updated, "source_before": before, "source_after": after}


def backup(con: sqlite3.Connection) -> bool:
    """Snapshot bảng gốc trước khi --apply (bỏ qua nếu đã tồn tại)."""
    exists = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (BACKUP_TABLE,),
    ).fetchone()[0]
    if exists:
        return False
    con.execute(f"CREATE TABLE {BACKUP_TABLE} AS SELECT * FROM macro_history")
    con.commit()
    return True


def print_report(title: str, stats: dict) -> None:
    print(f"\n== {title} ==")
    print(f"  rows                : {stats['rows']:,}")
    print(f"  distinct (date,var) : {stats['distinct_keys']:,}")
    print(f"  duplicate keys      : {stats['duplicate_keys']:,}  -> {stats['duplicate_rows']:,} rows thua")
    print(f"  source NULL         : {stats['source_null']:,}")
    print(f"  keys gia tri khac nhau: {stats['keys_with_differing_values']:,}")
    print(f"  backfill duoc tu v2 : {stats['rows_backfillable_from_v2']:,}")
    if stats["dup_sample"]:
        print("  mau dong trung (ngay gan nhat):")
        for d, v, c, vals in stats["dup_sample"]:
            print(f"    {d} {v:22} x{c} = {vals}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="thực thi (mặc định là dry-run)")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"khong tim thay DB: {args.db}")
        return 2

    con = sqlite3.connect(f"file:{args.db}?mode={'rw' if args.apply else 'ro'}", uri=True)
    try:
        stats = analyze(con)
        print_report("TRUOC (dry-run)", stats)
        if not args.apply:
            print("\nDRY-RUN — khong thay doi gi. Chay lai voi --apply de thuc thi.")
            return 0

        made_backup = backup(con)
        print(f"\nbackup: {'tao ' + BACKUP_TABLE if made_backup else 'da co (bo qua)'}")

        ded = dedupe(con)
        print(f"\n== DEDUPE ==\n  removed_rows: {ded['removed_rows']:,}")
        print_report("SAU dedupe", ded["after"])

        bf = backfill_source(con)
        print(f"\n== BACKFILL SOURCE (tu macro_history_v2) ==\n  rows_updated: {bf['rows_updated']:,}")
        print(f"  source khong NULL: {bf['source_before']:,} -> {bf['source_after']:,}")

        final = analyze(con)
        print_report("SAU TAT CA", final)
        assert final["duplicate_rows"] == 0, "van con dong trung"
        print("\nOK: 0 dong trung.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
