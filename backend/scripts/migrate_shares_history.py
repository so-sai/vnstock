"""Hạng mục C — dựng `shares_history` (số cổ phiếu lưu hành theo thời điểm, PIT).

NGỮ CẢNH (audit 2026-10-15/16 → `backend/src/research/financial_facts_ratio_audit.md`):
  - Kho đã có `CHARTER_CAPITAL` theo kỳ (VND, mệnh giá 10.000đ/cp), độ phủ **687→744 mã/kỳ từ
    2018Q1 đến 2025Q2** ⇒ số cổ phiếu = `CHARTER_CAPITAL / 10.000` (đúng công thức writer đang dùng).
  - `effective_date` **kế thừa Hạng mục A** (+30/+45/+90 ngày theo TT96/2020) — KHÔNG bịa lịch mới.
  - Cột `basis` khai báo tường minh: `'CHARTER_PIT'` (số cp TẠI KỲ). Việc chọn basis khi chia per-share
    phải khớp basis giá (`PRICE_BASIS` trong `valuation_engine.py`): giá `ADJUSTED_TODAY` ⇒ BVPS/PS
    phải dùng shares HIỆN TẠI; dùng shares TẠI KỲ với giá adjusted ⇒ cờ `BASIS_MISMATCH`.

⚠️ ĐIỀU KIỆN CHẠY: SQLite chỉ cho 1 tiến trình GHI tại một thời điểm. **KHÔNG chạy `--apply` khi
tiến trình khác đang nạp dữ liệu vào cùng DB** (vd `scripts/ingest_tcbs_to_db.py` của luồng
ingestion) — sẽ dính `database is locked` hoặc rollback lẫn nhau. Mặc định là `--dry-run`.

Chạy: backend/.venv/Scripts/python.exe backend/scripts/migrate_shares_history.py [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "financial_facts.db"
PAR_VALUE = 10_000.0  # mệnh giá chuẩn TTCK VN (đồng/cổ phiếu)
BASIS = "CHARTER_PIT"

DDL = """
CREATE TABLE IF NOT EXISTS shares_history (
    symbol             TEXT NOT NULL,
    period             TEXT NOT NULL,
    effective_date     TEXT NOT NULL,
    shares_outstanding REAL NOT NULL,
    source_metric      TEXT NOT NULL,
    basis              TEXT NOT NULL,
    revision           INTEGER DEFAULT 0,
    ingested_at        TEXT,
    PRIMARY KEY (symbol, period, revision)
)
"""

CANARY = {  # (symbol, period) → số cổ phiếu kỳ vọng (đã đo 2026-10-16)
    ("HPG", "2018Q1"): 1_517_079_000.0,
    ("HPG", "2025Q2"): 6_396_250_200.0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định: dry-run)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"ERR: không thấy {db}")
        return 1

    mode = "rw" if args.apply else "ro"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode={mode}", uri=True, timeout=60.0)
    con.execute("PRAGMA busy_timeout=60000")

    # ── Nguồn: CHARTER_CAPITAL + effective_date (kế thừa Hạng mục A) ──────────────
    rows = con.execute(
        """
        SELECT symbol, period, effective_date, value
        FROM financial_facts
        WHERE metric = 'CHARTER_CAPITAL' AND value > 0
        ORDER BY symbol, period
        """
    ).fetchall()

    print("=" * 78)
    print(f"  HẠNG MỤC C — shares_history | {'APPLY' if args.apply else 'DRY-RUN (chỉ đọc)'}")
    print(f"  DB: {db} | nguồn: CHARTER_CAPITAL/{PAR_VALUE:.0f} | basis={BASIS}")
    print("=" * 78)

    # ── Kiểm tra (fail-closed) ───────────────────────────────────────────────────
    errs: list[str] = []
    seen: set = set()
    out_hold: list[tuple] = []
    no_eff = 0
    for sym, per, eff, val in rows:
        if (sym, per) in seen:
            errs.append(f"trùng (symbol, period): {sym} {per}")
        seen.add((sym, per))
        if len(per) != 6 or per[4] != "Q" or per[5] not in "1234":
            errs.append(f"period sai định dạng: {sym} {per!r}")
            continue
        if not eff or len(str(eff)) != 10:
            no_eff += 1
            continue
        shares = float(val) / PAR_VALUE
        if shares <= 0:
            errs.append(f"shares <= 0: {sym} {per}")
            continue
        out_hold.append((sym, per, eff, shares, "CHARTER_CAPITAL", BASIS))
    if no_eff:
        print(f"  [cảnh báo] {no_eff} dòng thiếu effective_date ⇒ BỎ QUA (phải chạy Hạng mục A trước)")

    # ── Bảng coverage + canary ───────────────────────────────────────────────────
    per_counts: dict[str, int] = {}
    for r in out_hold:
        per_counts[r[1]] = per_counts.get(r[1], 0) + 1
    print(f"  kỳ: {len(per_counts)} | dòng hợp lệ: {len(out_hold):,}")
    print("  8 kỳ gần nhất:")
    for p in sorted(per_counts)[-8:]:
        print(f"    {p}: {per_counts[p]:4d} mã")
    key = {(r[0], r[1]): r[3] for r in out_hold}
    print("  canary:")
    for (sym, per), exp in CANARY.items():
        got = key.get((sym, per))
        ok = "OK" if got is not None and abs(got - exp) < 1 else f"LỆCH (kỳ vọng {exp:,.0f})"
        print(f"    {sym} {per}: {got if got is None else format(got, ',.0f')}  [{ok}]")
        if ok != "OK":
            errs.append(f"canary sai: {sym} {per}")
    print(f"  lỗi kiểm tra: {len(errs)}")
    for e in errs[:10]:
        print(f"    [ERR] {e}")
    if errs:
        print("\n  KẾT LUẬN: FAIL — không ghi gì (fail-closed).")
        con.close()
        return 2
    if not args.apply:
        print("\n  KẾT LUẬN: ĐẠT. Chạy lại với --apply (chỉ khi KHÔNG có tiến trình nào đang ghi DB).")
        con.close()
        return 0

    # ── APPLY ───────────────────────────────────────────────────────────────────
    bak = db.with_suffix(f".db.bak_shares_history_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(db, bak)
    print(f"\n  [backup] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")
    con.execute(DDL)
    con.executemany(
        "INSERT OR REPLACE INTO shares_history "
        "(symbol, period, effective_date, shares_outstanding, source_metric, basis, revision, ingested_at) "
        "VALUES (?,?,?,?,?,?,0,?)",
        [(*r, datetime.now().isoformat(timespec="seconds")) for r in out_hold],
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM shares_history").fetchone()[0]
    bad = con.execute("SELECT COUNT(*) FROM shares_history WHERE shares_outstanding <= 0").fetchone()[0]
    print(f"  [verify] shares_history: {n:,} dòng | shares<=0: {bad}")
    hpg = con.execute(
        "SELECT shares_outstanding FROM shares_history WHERE symbol=? AND period=?",
        ("HPG", "2018Q1"),
    ).fetchone()
    print(f"  [verify] HPG 2018Q1: {hpg}")
    con.close()
    return 0 if (n == len(out_hold) and bad == 0) else 3


if __name__ == "__main__":
    sys.exit(main())
