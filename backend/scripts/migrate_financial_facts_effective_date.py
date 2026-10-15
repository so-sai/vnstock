"""Hạng mục A — vá `effective_date` (Point-in-Time) cho kho `financial_facts.db`.

BỐI CẢNH (audit 2026-10-15, `backend/src/research/financial_facts_audit.md`):
  - 615.528 dòng có `period` hợp lệ `YYYYQn` = 100% ⇒ suy được ngày hiệu lực cho TOÀN BỘ kho.
  - `reported_at` dùng được chỉ 4 dòng (và là chuỗi kỳ "2025Q3", KHÔNG phải ngày) ⇒ nguồn
    không cung cấp `issueDate` ⇒ **quy tắc luật định là CƠ CHẾ CHÍNH**, không phải fallback.

QUY TẮC (Thông tư 96/2020/TT-BTC, theo chỉ đạo):
  Q1 → period_end + 30 ngày | Q2 (bán niên soát xét) → +45 | Q3 → +30 | Q4/năm (kiểm toán) → +90
  `effective_date_basis = 'STATUTORY_LAG'` cho mọi dòng.

HỢP ĐỒNG AN TOÀN:
  - Mặc định là `--dry-run`: chỉ ĐỌC, in bảng kiểm toán 55 kỳ, KHÔNG ghi gì.
  - `--apply` chỉ chạy khi bảng mapping vượt qua MỌI kiểm tra logic (fail-closed: có lỗi ⇒ exit≠0,
    không ghi gì). Trước khi ghi: **sao lưu file DB**; migration kiểu ADDITIVE (chỉ thêm 2 cột,
    không sửa/xoá cột cũ); toàn bộ UPDATE trong MỘT transaction.
  - In số dòng đã ghi theo từng kỳ để đối chiếu với dry-run.

Chạy: backend/.venv/Scripts/python.exe backend/scripts/migrate_financial_facts_effective_date.py [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "financial_facts.db"
TODAY = date.today()
LAG_DAYS = {1: 30, 2: 45, 3: 30, 4: 90}  # TT96/2020/TT-BTC
BASIS = "STATUTORY_LAG"


def period_end(period: str) -> date:
    """'2025Q2' → 2025-06-30. Raise nếu sai định dạng."""
    y, q = period[:4], period[5:]
    if len(period) != 6 or period[4] != "Q" or q not in "1234" or not y.isdigit():
        raise ValueError(f"period không hợp lệ: {period!r}")
    month = int(q) * 3
    # ngày cuối quý: tháng 3/6/9 → 31/30/30; tháng 12 → 31
    day = 31 if month in (3, 12) else 30
    return date(int(y), month, day)


def build_mapping(periods: list[str]) -> list[dict]:
    rows = []
    for p in periods:
        pe = period_end(p)
        q = int(p[5])
        lag = LAG_DAYS[q]
        eff = pe + timedelta(days=lag)
        rows.append(
            {
                "period": p,
                "period_end": pe.isoformat(),
                "lag_days": lag,
                "effective_date": eff.isoformat(),
                "basis": BASIS,
            }
        )
    return sorted(rows, key=lambda r: r["effective_date"])


def validate(mapping: list[dict]) -> list[str]:
    """Trả về danh sách lỗi. Rỗng = đạt."""
    errs: list[str] = []
    seen = set()
    for r in mapping:
        if r["period"] in seen:
            errs.append(f"kỳ trùng trong mapping: {r['period']}")
        seen.add(r["period"])
        try:
            pe = date.fromisoformat(r["period_end"])
            eff = date.fromisoformat(r["effective_date"])
        except ValueError:
            errs.append(f"ngày sai định dạng ISO: {r}")
            continue
        if len(r["effective_date"]) != 10 or r["effective_date"][4] != "-" or r["effective_date"][7] != "-":
            errs.append(f"effective_date không phải YYYY-MM-DD: {r}")
        if eff <= pe:
            errs.append(f"effective_date không sau period_end: {r}")
        if (eff - pe).days != LAG_DAYS[int(r["period"][5])]:
            errs.append(f"độ trễ sai quy tắc TT96: {r}")
        if r["basis"] != BASIS:
            errs.append(f"basis sai: {r}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định: dry-run)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"ERR: không thấy {db}")
        return 1

    mode = "ro" if not args.apply else "rw"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode={mode}", uri=True, timeout=60.0)
    con.execute("PRAGMA busy_timeout=60000")

    periods = [r[0] for r in con.execute("SELECT DISTINCT period FROM financial_facts ORDER BY period")]
    counts = {r[0]: r[1] for r in con.execute("SELECT period, COUNT(*) FROM financial_facts GROUP BY period")}
    mapping = build_mapping(periods)
    errs = validate(mapping)

    print("=" * 78)
    print(f"  HẠNG MỤC A — effective_date  |  {'DRY-RUN (chỉ đọc)' if not args.apply else 'APPLY (ghi thật)'}")
    print(f"  DB: {db}  ({db.stat().st_size / 1e6:.0f} MB)  |  hôm nay: {TODAY.isoformat()}")
    print("=" * 78)
    print(f"  {'period':8s} {'period_end':12s} {'lag':>4s} {'effective_date':16s} {'basis':16s} {'rows':>8s}  usable_now")
    tot = 0
    usable = 0
    for r in mapping:
        n = counts.get(r["period"], 0)
        ok = "có" if r["effective_date"] <= TODAY.isoformat() else "CHƯA (tương lai)"
        tot += n
        usable += n if ok == "có" else 0
        print(
            f"  {r['period']:8s} {r['period_end']:12s} {r['lag_days']:4d} {r['effective_date']:16s} "
            f"{r['basis']:16s} {n:8d}  {ok}"
        )
    print("-" * 78)
    print(f"  TỔNG dòng sẽ gán: {tot:,} | được phép dùng hôm nay: {usable:,} | chưa tới ngày: {tot - usable:,}")
    print(f"  Số kỳ: {len(mapping)} | Lỗi kiểm tra: {len(errs)}")
    for e in errs:
        print(f"    [ERR] {e}")

    # kiểm tra thêm: cột đã tồn tại chưa, và bảng annual dùng period gì
    cols = [r[1] for r in con.execute("PRAGMA table_info(financial_facts)")]
    print(f"  Cột đã có sẵn: effective_date={'effective_date' in cols} effective_date_basis={'effective_date_basis' in cols}")
    try:
        ann = [r[0] for r in con.execute("SELECT DISTINCT period FROM financial_facts_annual ORDER BY period LIMIT 5")]
        print(f"  (tham chiếu) financial_facts_annual.period mẫu: {ann}")
    except sqlite3.Error as e:
        print(f"  (tham chiếu) financial_facts_annual: {e}")

    if errs:
        print("\n  KẾT LUẬN: FAIL — không ghi gì (fail-closed).")
        con.close()
        return 2

    if not args.apply:
        print("\n  KẾT LUẬN: mapping ĐẠT kiểm toán logic. Chạy lại với --apply để ghi thật (có backup).")
        con.close()
        return 0

    # ── APPLY ────────────────────────────────────────────────────────────────
    bak = db.with_suffix(f".db.bak_effective_date_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(db, bak)
    print(f"\n  [backup] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")

    if "effective_date" not in cols:
        con.execute("ALTER TABLE financial_facts ADD COLUMN effective_date TEXT")
    if "effective_date_basis" not in cols:
        con.execute("ALTER TABLE financial_facts ADD COLUMN effective_date_basis TEXT")
    print("  [schema] thêm 2 cột (additive) — không đụng cột cũ")

    written = 0
    cur = con.cursor()
    for r in mapping:
        cur.execute(
            "UPDATE financial_facts SET effective_date=?, effective_date_basis=? WHERE period=?",
            (r["effective_date"], r["basis"], r["period"]),
        )
        n = cur.rowcount
        exp = counts.get(r["period"], 0)
        flag = "OK" if n == exp else f"LỆCH (mong {exp})"
        written += n
        print(f"    {r['period']} → {r['effective_date']} : {n:,} dòng  [{flag}]")
    con.commit()

    nn = con.execute("SELECT COUNT(*) FROM financial_facts WHERE effective_date IS NOT NULL").fetchone()[0]
    bad = con.execute(
        "SELECT COUNT(*) FROM financial_facts WHERE effective_date IS NOT NULL "
        "AND (length(effective_date)<>10 OR date(effective_date) IS NULL)"
    ).fetchone()[0]
    basis_dist = con.execute(
        "SELECT effective_date_basis, COUNT(*) FROM financial_facts GROUP BY 1"
    ).fetchall()
    print(f"\n  [verify] tổng đã ghi: {written:,} | effective_date NOT NULL: {nn:,} | ngày sai định dạng: {bad}")
    print(f"  [verify] basis: {basis_dist}")
    print(f"  [verify] SHB mẫu: {con.execute('SELECT period, effective_date, effective_date_basis FROM financial_facts WHERE symbol=? ORDER BY period DESC LIMIT 3', ('SHB',)).fetchall()}")
    con.close()
    return 0 if (nn == tot and bad == 0) else 3


if __name__ == "__main__":
    sys.exit(main())
