"""Hạng mục C — dựng `shares_history` (số cổ phiếu lưu hành theo thời điểm, PIT).

NGỮ CẢNH (audit 2026-10-15/16 → `backend/src/research/financial_facts_ratio_audit.md`):
  - `CHARTER_CAPITAL` (VND, mệnh giá 10.000đ/cp) → shares = `/10.000`: chuỗi **PIT thật** vì neo vào
    mệnh giá luật định; độ phủ ~740 mã/kỳ từ 2018Q1→2025Q2 (2025Q3+ chỉ 69–88 mã).
  - `SHARES_OUT` (nguồn `tcbs`, mới nạp 2026-10-16) → số cổ phiếu **nhà cung cấp báo cáo**.
  - **HAI NGUỒN NÀY LỆCH KHÔNG ĐỀU THEO THỜI GIAN** (đo 2026-10-16, SHB):
       2008Q4: +0,0%  ·  2010Q4: +662,5%  ·  2015Q4: +181,2%  ·  2020Q4: +161,6%
       2024Q4: +45,9% ·  2025Q2: +31,4%   ·  2026Q2: +16,3%
    ⇒ SHARES_OUT nằm ở **basis khác** (trông như đã điều chỉnh hồi tố), KHÔNG phải số cp tại kỳ.
    **CẤM nối hai nguồn trong một chuỗi** (sẽ tạo bậc thang giả trong BVPS/EPS). Vì vậy bảng lưu
    **CẢ HAI basis riêng biệt**, mỗi dòng khai báo `basis` + `source_metric` + `divergence_pct`.

HỢP ĐỒNG AN TOÀN:
  - Mặc định `--dry-run` (chỉ đọc); `--apply` mới ghi (có backup file DB, 1 transaction, bảng MỚI).
  - `effective_date` **kế thừa Hạng mục A** (đã phủ 617.699/617.699 dòng, basis `STATUTORY_LAG`).
  - Fail-closed: kỳ sai định dạng / shares ≤ 0 / thiếu effective_date ⇒ **lỗi, không ghi gì**.
    Lệch giữa 2 basis **không** phải lỗi — nó là DỮ KIỆN, được ghi vào `divergence_pct`.
  - ⚠️ SQLite chỉ cho 1 writer: KHÔNG chạy `--apply` khi tiến trình khác đang nạp cùng DB.

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
BASIS_CHARTER = "CHARTER_PIT"  # suy từ mệnh giá luật định — PIT thật
BASIS_TCBS = "TCBS_REPORTED"  # nhà cung cấp báo cáo — basis khác, cấm nối chuỗi

DDL = """
CREATE TABLE IF NOT EXISTS shares_history (
    symbol             TEXT NOT NULL,
    period             TEXT NOT NULL,
    basis              TEXT NOT NULL,
    effective_date     TEXT NOT NULL,
    shares_outstanding REAL NOT NULL,
    source_metric      TEXT NOT NULL,
    divergence_pct     REAL,
    revision           INTEGER DEFAULT 0,
    ingested_at        TEXT,
    PRIMARY KEY (symbol, period, basis)
)
"""

CANARY = {  # (symbol, period, basis) → số cổ phiếu kỳ vọng (đo 2026-10-16)
    ("HPG", "2018Q1", BASIS_CHARTER): 1_517_079_000.0,
    ("HPG", "2025Q2", BASIS_CHARTER): 6_396_250_200.0,
    ("SHB", "2026Q2", BASIS_TCBS): 5_343_748_241.0,
    ("SHB", "2026Q2", BASIS_CHARTER): 4_594_200_000.0,
    ("SHB", "2010Q4", BASIS_TCBS): 2_667_090_216.0,
    ("SHB", "2010Q4", BASIS_CHARTER): 349_800_000.0,
}


def _ok_period(per: str | None) -> bool:
    return bool(per) and len(per) == 6 and per[4] == "Q" and per[5] in "1234"


def collect(con: sqlite3.Connection) -> tuple[list[tuple], list[str], list[str]]:
    """→ (rows, errors, warnings). rows = (symbol, period, basis, eff, shares, metric, div%)."""
    errs: list[str] = []
    warns: list[str] = []

    charter: dict[tuple[str, str], tuple[str, float]] = {}
    for sym, per, eff, val in con.execute(
        "SELECT symbol, period, effective_date, value FROM financial_facts "
        "WHERE metric='CHARTER_CAPITAL' AND value > 0"
    ):
        if not _ok_period(per):
            errs.append(f"period sai định dạng: {sym} {per!r}")
            continue
        if not eff or len(str(eff)) != 10:
            errs.append(f"thiếu effective_date: {sym} {per}")
            continue
        charter[(sym, per)] = (str(eff), float(val) / PAR_VALUE)

    tcbs: dict[tuple[str, str], tuple[str, float]] = {}
    for sym, per, eff, val in con.execute(
        "SELECT symbol, period, effective_date, value FROM financial_facts "
        "WHERE metric='SHARES_OUT' AND source='tcbs' AND value > 0"
    ):
        if not _ok_period(per):
            errs.append(f"period sai định dạng: {sym} {per!r}")
            continue
        if not eff or len(str(eff)) != 10:
            errs.append(f"thiếu effective_date: {sym} {per}")
            continue
        tcbs[(sym, per)] = (str(eff), float(val))

    rows: list[tuple] = []
    for key, (eff, sh) in charter.items():
        other = tcbs.get(key)
        div = (other[1] - sh) / sh * 100.0 if other and sh else None
        rows.append((key[0], key[1], BASIS_CHARTER, eff, sh, "CHARTER_CAPITAL/10000", div))
    for key, (eff, sh) in tcbs.items():
        own = charter.get(key)
        div = (sh - own[1]) / own[1] * 100.0 if own and own[1] else None
        rows.append((key[0], key[1], BASIS_TCBS, eff, sh, "SHARES_OUT", div))

    ndiv = [r for r in rows if r[6] is not None and abs(r[6]) > 1.0]
    if ndiv:
        syms = sorted({r[0] for r in ndiv})
        warns.append(
            f"{len(ndiv):,} dòng lệch >1% giữa 2 basis (= {len(ndiv) // 2:,} cặp, {len(syms)} mã) "
            f"— vd {syms[:8]}"
        )
    return rows, errs, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định: chỉ đọc)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"ERR: không thấy {db}")
        return 1

    con = sqlite3.connect(f"file:{db.as_posix()}?mode={'rw' if args.apply else 'ro'}", uri=True, timeout=60.0)
    con.execute("PRAGMA busy_timeout=60000")

    print("=" * 78)
    print(f"  HẠNG MỤC C — shares_history | {'APPLY' if args.apply else 'DRY-RUN (chỉ đọc)'}")
    print(f"  DB: {db} | 2 basis: {BASIS_CHARTER} · {BASIS_TCBS}")
    print("=" * 78)
    rows, errs, warns = collect(con)

    for b in (BASIS_CHARTER, BASIS_TCBS):
        sel = [r for r in rows if r[2] == b]
        print(
            f"   {b:16s}: {len(sel):6,d} dòng | {len({r[1] for r in sel}):2d} kỳ | "
            f"{len({r[0] for r in sel}):4d} mã"
        )

    print("\n   độ phủ theo kỳ (basis CHARTER, 6 kỳ gần nhất):")
    cnt: dict[str, int] = {}
    for r in rows:
        if r[2] == BASIS_CHARTER:
            cnt[r[1]] = cnt.get(r[1], 0) + 1
    for p in sorted(cnt)[-6:]:
        print(f"     {p}: {cnt[p]:4d} mã")

    print("\n   canary:")
    key = {(r[0], r[1], r[2]): r[4] for r in rows}
    for k, exp in CANARY.items():
        got = key.get(k)
        ok = got is not None and abs(got - exp) < 1
        shown = "None" if got is None else format(got, ",.0f")
        print(f"     {k[0]} {k[1]} {k[2]:15s}: {shown}  [{'OK' if ok else f'LỆCH (kỳ vọng {exp:,.0f})'}]")
        if not ok:
            errs.append(f"canary sai: {k}")

    print("\n   lệch giữa 2 basis (DỮ KIỆN, không phải lỗi):")
    for w in warns:
        print(f"     [flag] {w}")
    divs = sorted((r for r in rows if r[6] is not None and abs(r[6]) > 1.0), key=lambda r: -abs(r[6]))
    for r in divs[:5]:
        print(f"     {r[0]:5s} {r[1]}: charter={r[4]:16,.0f} | lệch={r[6]:+8.1f}%")

    print(f"\n   lỗi kiểm tra: {len(errs)}")
    for e in errs[:10]:
        print(f"     [ERR] {e}")
    if errs:
        print("\n   KẾT LUẬN: FAIL — không ghi gì (fail-closed).")
        con.close()
        return 2
    if not args.apply:
        print("\n   KẾT LUẬN: ĐẠT. Chạy `--apply` (chỉ khi không có tiến trình nào đang ghi DB).")
        con.close()
        return 0

    # ── APPLY ────────────────────────────────────────────────────────────────
    bak = db.with_suffix(f".db.bak_shares_hist_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(db, bak)
    print(f"\n   [backup] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")
    con.execute(DDL)
    now = datetime.now().isoformat(timespec="seconds")
    con.executemany(
        "INSERT OR REPLACE INTO shares_history (symbol, period, basis, effective_date, "
        "shares_outstanding, source_metric, divergence_pct, revision, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,0,?)",
        [(*r, now) for r in rows],
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM shares_history").fetchone()[0]
    bad = con.execute("SELECT COUNT(*) FROM shares_history WHERE shares_outstanding <= 0").fetchone()[0]
    print(f"   [verify] shares_history: {n:,} dòng (kỳ vọng {len(rows):,}) | shares<=0: {bad}")
    for k in (("SHB", "2026Q2"), ("HPG", "2018Q1")):
        got = con.execute(
            "SELECT basis, shares_outstanding FROM shares_history WHERE symbol=? AND period=? ORDER BY basis",
            k,
        ).fetchall()
        print(f"   [verify] {k[0]} {k[1]}: {got}")
    con.close()
    return 0 if (n == len(rows) and bad == 0) else 3


if __name__ == "__main__":
    sys.exit(main())
