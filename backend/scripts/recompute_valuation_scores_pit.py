"""Hạng mục B — tính lại `valuation_scores` theo chuẩn PIT, có chốt chặn "writer im lặng".

BA VÁ ĐÃ NẰM TRONG `valuation_engine.py` (đã test, xem `backend/tests/test_valuation_engine_pit.py`):
  1. Bỏ fallback `ORDER BY date DESC LIMIT 1` ⇒ không còn giá TƯƠNG LAI (636/1.789 cặp từng nhiễm).
  2. Tự hiệu chuẩn chu kỳ EPS theo từng mã (QUARTER ×4 · TTM ×1 · YTD ×4/q · UNKNOWN ⇒ cờ + không xếp hạng).
  3. Chọn số cổ phiếu có GUARD pha loãng (ưu tiên `SHARES_OUT`/tcbs nếu lệch ≤ 25%; vượt ⇒ charter + cờ).
  4. Gắn cờ kiểm toán của Hạng mục E vào chính hàng điểm (`anomaly_flags`, `is_clean_metric`,
     `price_basis`, `shares_basis`, `eps_flow_kind` — thêm cột ADDITIVE, không sửa dữ liệu cũ).

CHỐT CHẶN: SQLite chỉ 1 writer. Script ĐO số dòng và CHỈ chạy khi bảng không đổi trong
`--min-idle` giây (mặc định 60). Không đạt ⇒ exit 4, KHÔNG ghi gì.

Chạy:
  python backend/scripts/recompute_valuation_scores_pit.py                  # dry-run: chỉ đọc + đo hiện trạng
  python backend/scripts/recompute_valuation_scores_pit.py --apply --limit 5 # smoke 5 mã (ghi thật)
  python backend/scripts/recompute_valuation_scores_pit.py --apply          # toàn bộ universe
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "financial_facts.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.financial.valuation_engine import ValuationEngine


def count_rows(path: Path) -> int:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    try:
        return con.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
    finally:
        con.close()


def wait_for_idle(path: Path, seconds: int, timeout_s: int = 1800) -> tuple[bool, int]:
    """→ (im lặng?, số dòng). Đo mỗi 5s; cần `seconds` giây không đổi."""
    last = count_rows(path)
    stable_since = time.monotonic()
    t0 = time.monotonic()
    print(f"   [guard] chờ writer im lặng ≥{seconds}s (bắt đầu: {last:,} dòng)...")
    while time.monotonic() - t0 < timeout_s:
        time.sleep(5)
        cur = count_rows(path)
        if cur != last:
            print(f"   [guard] writer ĐANG ghi: {last:,} → {cur:,} (+{cur - last:,}) — reset đồng hồ")
            last = cur
            stable_since = time.monotonic()
            continue
        if time.monotonic() - stable_since >= seconds:
            print(f"   [guard] OK: {last:,} dòng không đổi trong {seconds}s")
            return True, last
    print(f"   [guard] QUÁ HẠN {timeout_s}s mà writer chưa im lặng")
    return False, last


def current_state(path: Path) -> None:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    cols = {r[1] for r in con.execute("PRAGMA table_info(valuation_scores)")}
    n_rows = con.execute("SELECT COUNT(*) FROM valuation_scores").fetchone()[0]
    n_sym = con.execute("SELECT COUNT(DISTINCT symbol) FROM valuation_scores").fetchone()[0]
    n_per = con.execute("SELECT COUNT(DISTINCT period) FROM valuation_scores").fetchone()[0]
    print(f"   valuation_scores: {n_rows:,} dòng | {n_sym} mã | {n_per} kỳ")
    _have = sorted(cols & {"anomaly_flags", "is_clean_metric", "price_basis", "shares_basis", "eps_flow_kind"})
    print(f"   cột cờ đã có: {_have or 'CHƯA'}")
    _miss = con.execute("SELECT COUNT(*) FROM financial_facts WHERE effective_date IS NULL").fetchone()[0]
    print(f"   dòng thiếu effective_date: {_miss:,}")
    _q = "SELECT COUNT(*) FROM valuation_scores WHERE anomaly_flags LIKE '%PRICE_FALLBACK_FUTURE%'"
    w = con.execute(_q).fetchone()[0] if "anomaly_flags" in cols else -1
    print(f"   dòng mang cờ PRICE_FALLBACK_FUTURE: {w if w >= 0 else 'chưa có cột cờ'}")
    con.close()


def dod_report(path: Path) -> dict:
    """3 tiêu chí nghiệm thu Hạng mục B (+ phân bố cờ)."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60.0)
    cols = {r[1] for r in con.execute("PRAGMA table_info(valuation_scores)")}
    if "is_clean_metric" not in cols:
        print("   [DoD] chưa có cột cờ ⇒ chưa chạy B")
        con.close()
        return {}
    out: dict = {}
    out["fallback_rows"] = con.execute(
        "SELECT COUNT(*) FROM valuation_scores WHERE anomaly_flags LIKE '%PRICE_FALLBACK_FUTURE%'"
    ).fetchone()[0]
    out["price_null_rows"] = con.execute("SELECT COUNT(*) FROM valuation_scores WHERE price IS NULL").fetchone()[0]
    out["pe_lt2_all"] = con.execute(
        "SELECT COUNT(*) FROM valuation_scores WHERE ratio_name='PE' AND ratio_value < 2"
    ).fetchone()[0]
    out["pe_lt2_clean"] = con.execute(
        "SELECT COUNT(*) FROM valuation_scores WHERE ratio_name='PE' AND ratio_value < 2 AND is_clean_metric=1"
    ).fetchone()[0]
    vals = [
        r[0]
        for r in con.execute(
            "SELECT ratio_value FROM valuation_scores WHERE ratio_name='PE' AND ratio_value > 0 AND is_clean_metric=1"
        )
    ]
    out["pe_n"] = len(vals)
    out["pe_median"] = statistics.median(vals) if vals else None
    out["pe_p10"] = statistics.quantiles(vals, n=10)[0] if len(vals) > 10 else None
    out["pe_p90"] = statistics.quantiles(vals, n=10)[-1] if len(vals) > 10 else None
    out["flow_kind"] = con.execute(
        "SELECT eps_flow_kind, COUNT(*) FROM valuation_scores GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    out["shares_basis"] = con.execute(
        "SELECT shares_basis, COUNT(*) FROM valuation_scores GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    _fd = (
        "SELECT anomaly_flags, COUNT(*) FROM valuation_scores "
        "WHERE anomaly_flags IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
    )
    out["flag_dist"] = con.execute(_fd).fetchall()
    out["clean_rows"] = con.execute("SELECT COUNT(*) FROM valuation_scores WHERE is_clean_metric=1").fetchone()[0]
    out["total_rows"] = con.execute("SELECT COUNT(*) FROM valuation_scores").fetchone()[0]
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định: chỉ đọc)")
    ap.add_argument("--min-idle", type=int, default=60)
    ap.add_argument("--limit", type=int, default=0, help="chỉ chạy N mã đầu (smoke)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"ERR: không thấy {db}")
        return 1

    print("=" * 78)
    print(f"  HẠNG MỤC B — valuation_scores PIT | {'APPLY' if args.apply else 'DRY-RUN'}")
    print("=" * 78)
    print("   HIỆN TRẠNG:")
    current_state(db)
    if not args.apply:
        print("\n   KẾT LUẬN: dry-run xong. Thêm `--apply` (sau khi writer im lặng) để tính lại.")
        return 0

    ok, n0 = wait_for_idle(db, args.min_idle)
    if not ok:
        print("\n   KẾT LUẬN: FAIL — writer chưa im lặng, KHÔNG ghi gì.")
        return 4

    # ── Bù effective_date cho các dòng mới (đúng luật: ingestion xong phải vá lại) ──────
    missing = (
        sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        .execute("SELECT COUNT(*) FROM financial_facts WHERE effective_date IS NULL")
        .fetchone()[0]
    )
    if missing:
        print(f"\n   [A] {missing:,} dòng thiếu effective_date ⇒ chạy lại migration A...")
        rc = subprocess.call(
            [sys.executable, str(Path(__file__).with_name("migrate_financial_facts_effective_date.py")), "--apply"],
            stdout=subprocess.DEVNULL,
        )
        print(f"   [A] exit={rc}")

    # ── Hạng mục C ────────────────────────────────────────────────────────────────────
    print("\n   [C] chạy migrate_shares_history.py --apply...")
    rc = subprocess.call(
        [sys.executable, str(Path(__file__).with_name("migrate_shares_history.py")), "--apply"],
        stdout=subprocess.DEVNULL,
    )
    print(f"   [C] exit={rc}")

    # ── B: backup + tính lại toàn universe ────────────────────────────────────────────
    bak = db.with_suffix(f".db.bak_recompute_scores_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(db, bak)
    print(f"\n   [B][backup] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM financial_facts ORDER BY symbol")]
    con.close()
    if args.limit:
        syms = syms[: args.limit]
    print(f"   [B] universe: {len(syms)} mã | n0={n0:,} dòng facts")

    eng = ValuationEngine()
    done = err = 0
    t0 = time.monotonic()
    for i, sym in enumerate(syms, 1):
        try:
            eng.compute_valuation(sym)
            done += 1
        except (sqlite3.Error, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            err += 1
            print(f"     [ERR] {sym}: {type(exc).__name__}: {exc}")
        if i % 100 == 0:
            print(f"     ... {i}/{len(syms)} ({time.monotonic() - t0:.0f}s)")
    print(f"   [B] xong: {done} mã OK · {err} lỗi · {time.monotonic() - t0:.0f}s")

    print("\n   NGHIỆM THU (DoD Hạng mục B):")
    rep = dod_report(db)
    for k, v in rep.items():
        print(f"     {k:16s}: {v}")
    print("\n   3 TIÊU CHÍ:")
    fb = rep.get("fallback_rows", "?")
    print(f"     (1) cặp dùng giá fallback tương lai : {fb}  (cờ GIỮ LẠI để audit, giá đã về NULL)")
    _a, _c = rep.get("pe_lt2_all", "?"), rep.get("pe_lt2_clean", "?")
    print(f"     (2) số dòng PE < 2                  : {_a} (tổng) / {_c} (sạch)")
    print(f"     (3) median PE (dòng sạch)           : {rep.get('pe_median')}")
    return 0 if err == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
