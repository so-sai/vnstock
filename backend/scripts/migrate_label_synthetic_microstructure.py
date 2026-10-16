#!/usr/bin/env python
"""Migration: ghi NHÃN SYNTHETIC cho 2 bảng vi cấu trúc (`volume_profile`, `active_demand`).

[WHY]
Bất biến #1: không provenance ⇒ đứng ngoài. Truy vết 2026-10-16 tìm ra writer
(`src/financial/market_behavior_engine.py::compute_volume_profile` + `src/backtest/backfill_behavior_pit.py`):
nguồn vào là `screener_cache.daily_ohlcv` (EOD) và khối lượng của phiên được **CHIA ĐỀU** lên dải
`[low, high]` (`vol_per_unit = vol / span`) ⇒ POC/VAH/VAL và `hvns` là **ƯỚC LƯỢNG MÔ HÌNH**, KHÔNG phải
khối lượng khớp thật tại từng mức giá. Bảng không có cột nào nói điều đó ⇒ consumer có thể đọc POC như
số liệu quan sát được. Đây là dạng nguy hiểm hơn "thiếu provenance": số ĐÚNG định dạng nhưng SAI bản chất.

[ROOT CAUSE]
Tác giả bảng không thấy cần cột nguồn vì dữ liệu suy ra tại chỗ ⇒ không có `source`/`method`/
`is_synthetic`. Không phải lỗi ingestion, không phải thiếu nguồn intraday.

[INVARIANT]
1. Migration là ADDITIVE + IDEMPOTENT, KHÔNG sửa/xoá dòng nào (đếm trước == đếm sau).
2. Mọi dòng hiện có được ghi `is_synthetic=1` + `method='UNIFORM_WITHIN_DAILY_RANGE'` +
   `derived_from='screener_cache.daily_ohlcv'` (không suy đoán giá trị khác).
3. Consumer BẮT BUỘC lọc `is_synthetic = 0` (xem `src/database/synthetic_guard.py`); thiếu cột nhãn
   ⇒ fail-closed (raise), không được coi là "sạch".

Chạy:  python backend/scripts/migrate_label_synthetic_microstructure.py            # dry-run
       python backend/scripts/migrate_label_synthetic_microstructure.py --apply    # ghi thật
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "backend" / "data" / "financial_facts.db"

TABLES = ("volume_profile", "active_demand")
NEW_COLUMNS = (
    ("is_synthetic", "INTEGER DEFAULT 1"),
    ("method", "TEXT DEFAULT 'UNIFORM_WITHIN_DAILY_RANGE'"),
    ("derived_from", "TEXT DEFAULT 'screener_cache.daily_ohlcv'"),
)
LABEL_VALUES = {
    "is_synthetic": 1,
    "method": "UNIFORM_WITHIN_DAILY_RANGE",
    "derived_from": "screener_cache.daily_ohlcv",
}


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định: dry-run)")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    db_path = Path(a.db)
    if not db_path.exists():
        print(f"ERR: không thấy {db_path}")
        return 2

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    report: list[str] = []
    try:
        # ── TRƯỚC: đo hiện trạng (để chứng minh không mất dòng, không đổi dữ liệu gốc) ──
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        span = {t: conn.execute(f"SELECT MIN(date), MAX(date) FROM {t}").fetchone() for t in TABLES}
        for t in TABLES:
            report.append(f"   {t}: {before[t]:,} dòng | {span[t][0]} → {span[t][1]}")

        todo = {t: [c for c in NEW_COLUMNS if c[0] not in _cols(conn, t)] for t in TABLES}
        if not any(todo.values()):
            print("Không có gì để làm (cả 3 cột đã tồn tại ở cả 2 bảng) — migration idempotent, thoát.")
            return 0

        if not a.apply:
            print("=== DRY-RUN ===")
            print(*report, sep="\n")
            for t in TABLES:
                print(f"   {t}: sẽ THÊM {[c[0] for c in todo[t]] or '—'}")
            print("\nChạy lại với --apply để ghi thật.")
            return 0

        # ── GHI ──
        for t in TABLES:
            for name, ddl in todo[t]:
                conn.execute(f"ALTER TABLE {t} ADD COLUMN {name} {ddl}")
                report.append(f"   + {t}.{name}")
            # Vật chất hoá giá trị cho dòng hiện có (ALTER chỉ đặt DEFAULT cho lần đọc/dòng mới)
            sets = ", ".join(f"{k} = ?" for k in LABEL_VALUES if k in _cols(conn, t))
            conn.execute(
                f"UPDATE {t} SET {sets} WHERE is_synthetic IS NULL OR method IS NULL OR derived_from IS NULL",
                tuple(v for k, v in LABEL_VALUES.items() if k in _cols(conn, t)),
            )
        conn.commit()

        # ── SAU: chứng minh bất biến "không mất dòng, không đổi dữ liệu gốc" ──
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        span2 = {t: conn.execute(f"SELECT MIN(date), MAX(date) FROM {t}").fetchone() for t in TABLES}
        ok = True
        for t in TABLES:
            if before[t] != after[t]:
                print(f"ERR: {t} đổi số dòng {before[t]} → {after[t]} — migration PHẢI chỉ thêm cột")
                ok = False
            if span[t] != span2[t]:
                print(f"ERR: {t} đổi khoảng ngày {span[t]} → {span2[t]}")
                ok = False
            nulls = conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE is_synthetic IS NULL OR method IS NULL OR derived_from IS NULL"
            ).fetchone()[0]
            labelled = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE is_synthetic = 1").fetchone()[0]
            report.append(f"   {t}: {after[t]:,} dòng | is_synthetic=1: {labelled:,} | NULL nhãn: {nulls}")
            if nulls:
                print(f"ERR: {t} còn {nulls} dòng thiếu nhãn")
                ok = False
        print("=== ĐÃ GHI ===")
        print(*report, sep="\n")
        return 0 if ok else 1
    except sqlite3.OperationalError as exc:
        print(f"ERR: SQLite từ chối ({exc}) — có tiến trình khác đang giữ DB? KHÔNG tự retry mù.")
        return 3
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
