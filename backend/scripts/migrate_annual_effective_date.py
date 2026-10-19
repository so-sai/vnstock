"""Gán `effective_date` (PIT) cho `financial_facts_annual` — TT96/2020, lag 90 ngày.

[WHY]
  Bảng BCTC Năm (28.730 dòng / 693 mã / FY2009–2025) **không có** `effective_date` ⇒ không PIT được.
  Consumer thật: `quant/vn20_quant_filter._annual_verification()` đọc theo `fiscal_year` cố định
  (`_VERIFY_YEAR = 2025`) nên (a) không thể gác PIT cho backtest và (b) sẽ "veto ngầm" khi sang năm
  tài chính mới (mọi mã trả `''`). Có `effective_date` mới chuyển được gate sang `effective_date <= target`.

[ROOT CAUSE]
  Hạng mục A chỉ migrate bảng QUÝ (`financial_facts`, 653.764 dòng). Bảng NĂM bị bỏ lại.

[INVARIANT]
  1. Công thức: `31/12 của fiscal_year + 90 ngày` — **dùng lại `LAG_DAYS[4]` của migration quý**
     (`migrate_financial_facts_effective_date.py`), không chép hằng số ⇒ một luật cho cả hai bảng.
     Hệ quả: năm nhuận (2012/2016/2020/2024) ⇒ **30/03**; năm thường ⇒ **31/03**.
  2. `effective_date > period_end` cho 100% dòng; KHÔNG NULL; basis = `STATUTORY_LAG` (khớp bảng quý).
  3. Idempotent: thêm cột theo `PRAGMA table_info`, UPDATE là phép gán lại ⇒ chạy nhiều lần vô hại.
  4. Fail-closed: `fiscal_year` NULL/dị thường ⇒ LỖI và **không ghi gì** (không đoán năm).
  5. Backup bằng SQLite Online Backup API trước mọi lệnh ghi.

Chạy:  python backend/scripts/migrate_annual_effective_date.py            # dry-run
       python backend/scripts/migrate_annual_effective_date.py --apply    # ghi (có backup)
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "financial_facts.db"
TABLE = "financial_facts_annual"
MIN_YEAR = 2000
MAX_YEAR = date.today().year + 1

# ── Tái dùng NGUỒN SỰ THẬT của migration QUÝ (DRY) ───────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "migrate_ff_effective_date", Path(__file__).with_name("migrate_financial_facts_effective_date.py")
)
assert _spec and _spec.loader
_q = importlib.util.module_from_spec(_spec)
sys.modules["migrate_ff_effective_date"] = _q
_spec.loader.exec_module(_q)

ANNUAL_LAG_DAYS = _q.LAG_DAYS[4]  # 90 ngày cho kỳ năm (TT96/2020)
BASIS = _q.BASIS  # "STATUTORY_LAG" — cùng basis với bảng quý


def annual_effective_date(fiscal_year) -> date:
    """Ngày hiệu lực của BCTC năm = 31/12 + 90 ngày. Raise ValueError nếu năm dị thường.

    [WHY] Năm nhuận phía sau ⇒ 30/03 (tháng 2 có 29 ngày). Đây là 7.514 dòng (26,2%) — nếu cứng 31/03
    thì bảng NĂM lệch 1 ngày so với Q4 cùng kỳ của bảng QUÝ, phá tính nhất quán giữa hai bảng.
    """
    try:
        fy = int(fiscal_year)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"fiscal_year không đọc được: {fiscal_year!r}") from exc
    if not (MIN_YEAR <= fy <= MAX_YEAR):
        raise ValueError(f"fiscal_year ngoài [{MIN_YEAR}, {MAX_YEAR}]: {fiscal_year!r}")
    return date(fy, 12, 31) + timedelta(days=ANNUAL_LAG_DAYS)


def build_mapping(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    """→ (mapping theo NĂM có dữ liệu, danh sách lỗi). Lỗi rỗng ⇒ đạt."""
    rows: list[dict] = []
    errs: list[str] = []
    years = [r[0] for r in conn.execute(f"SELECT DISTINCT fiscal_year FROM {TABLE} ORDER BY 1")]
    for fy in years:
        try:
            eff = annual_effective_date(fy)
        except ValueError as exc:
            errs.append(str(exc))
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE fiscal_year=?", (fy,)).fetchone()[0]
        rows.append(
            {
                "fiscal_year": fy,
                "period_end": date(int(fy), 12, 31).isoformat(),
                "lag_days": ANNUAL_LAG_DAYS,
                "effective_date": eff.isoformat(),
                "rows": n,
            }
        )
    return rows, errs


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Thêm 2 cột nếu chưa có (idempotent qua PRAGMA)."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    if "effective_date" not in cols:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN effective_date TEXT")
        print(f"  [schema] + {TABLE}.effective_date")
    if "effective_date_basis" not in cols:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN effective_date_basis TEXT")
        print(f"  [schema] + {TABLE}.effective_date_basis")


def apply_mapping(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Gán `effective_date` + basis theo từng NĂM. Trả tổng số dòng đã cập nhật."""
    ensure_columns(conn)
    total = 0
    for r in rows:
        cur = conn.execute(
            f"UPDATE {TABLE} SET effective_date=?, effective_date_basis=? WHERE fiscal_year=?",
            (r["effective_date"], BASIS, r["fiscal_year"]),
        )
        if cur.rowcount != r["rows"]:
            raise RuntimeError(
                f"DỪNG — FY{r['fiscal_year']}: rowcount {cur.rowcount} ≠ số dòng dự kiến {r['rows']}"
            )
        total += cur.rowcount
    return total


def verify(conn: sqlite3.Connection) -> list[str]:
    """Kiểm sau ghi. Rỗng = đạt."""
    errs: list[str] = []
    n_total = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    n_null = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE effective_date IS NULL").fetchone()[0]
    if n_null:
        errs.append(f"{n_null}/{n_total} dòng NULL effective_date")
    n_basis = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE effective_date_basis IS NOT ?", (BASIS,)
    ).fetchone()[0]
    if n_basis:
        errs.append(f"{n_basis} dòng sai basis (≠ {BASIS})")
    bad_fmt = 0
    bad_order = 0
    for fy, eff in conn.execute(f"SELECT fiscal_year, effective_date FROM {TABLE}").fetchall():
        if eff is None:
            continue
        try:
            d = date.fromisoformat(eff)
        except ValueError:
            bad_fmt += 1
            continue
        if d <= date(int(fy), 12, 31):
            bad_order += 1
    if bad_fmt:
        errs.append(f"{bad_fmt} dòng sai định dạng ngày")
    if bad_order:
        errs.append(f"{bad_order} dòng có effective_date ≤ period_end (look-ahead!)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="Gán effective_date cho financial_facts_annual (lag 90 ngày)")
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định chỉ đọc)")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()

    con = sqlite3.connect(str(a.db))
    rows, errs = build_mapping(con)
    print(f"DB: {a.db}\nCHẾ ĐỘ: {'APPLY (ghi thật)' if a.apply else 'DRY-RUN (không ghi)'}")
    print(f"  lag = {ANNUAL_LAG_DAYS} ngày · basis = {BASIS} · dải năm = [{MIN_YEAR}, {MAX_YEAR}]")
    print(f"\n  {'FY':>5} {'period_end':>12} {'lag':>4} {'effective_date':>15} {'dòng':>7}")
    for r in rows:
        print(
            f"  {r['fiscal_year']:>5} {r['period_end']:>12} {r['lag_days']:>4} "
            f"{r['effective_date']:>15} {r['rows']:>7}"
        )
    total = sum(r["rows"] for r in rows)
    leap = sum(r["rows"] for r in rows if r["effective_date"].endswith("-03-30"))
    print(f"  TỔNG: {total:,} dòng | rơi vào 30/03 (năm nhuận): {leap:,}")

    if errs:
        print(f"\n  LỖI ({len(errs)}):")
        for e in errs:
            print(f"    [ERR] {e}")
        print("\n  KẾT LUẬN: FAIL — không ghi gì (fail-closed).")
        con.close()
        return 2
    if not a.apply:
        print("\n  DRY-RUN: chưa ghi gì. Thêm --apply để thực thi (có backup).")
        con.close()
        return 0

    bak = a.db.with_name(f"{a.db.name}.bak_annual_effdate_{datetime.now():%Y%m%d_%H%M%S}")
    src = sqlite3.connect(f"file:{a.db.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(bak))
    src.backup(dst)
    src.close()
    dst.close()
    print(f"\n  [backup] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")

    try:
        with con:
            n = apply_mapping(con, rows)
    except Exception as exc:  # noqa: BLE001 — fail-closed: không ghi nửa vời
        con.rollback()
        print(f"\n  KẾT LUẬN: FAIL — đã rollback. Lý do: {exc}")
        con.close()
        return 1
    print(f"  [apply] đã gán {n:,} dòng")

    checks = verify(con)
    print("\n  NGHIỆM THU:")
    n_null = con.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE effective_date IS NULL").fetchone()[0]
    print(f"    NULL effective_date  : {n_null}")
    dist = con.execute(f"SELECT effective_date, COUNT(*) FROM {TABLE} GROUP BY 1 ORDER BY 1").fetchall()
    print(f"    phân bố ngày hiệu lực: {dist}")
    for sym in ("HPG", "SHB", "PNJ"):
        rows_sym = con.execute(
            f"SELECT fiscal_year, effective_date FROM {TABLE} WHERE symbol=? ORDER BY fiscal_year", (sym,)
        ).fetchall()
        print(f"    {sym}: {rows_sym}")
    if checks:
        print(f"\n  LỖI KIỂM TRA ({len(checks)}):")
        for c in checks:
            print(f"    [ERR] {c}")
        con.close()
        return 1
    print("  KẾT LUẬN: ĐẠT — 100% dòng có effective_date hợp lệ, > period_end.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
