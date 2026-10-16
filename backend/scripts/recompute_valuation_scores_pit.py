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
from datetime import date, datetime
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
    # Tiêu chí (1) đo ĐÚNG cách: các cặp (mã,kỳ) CÓ dữ liệu cơ bản nhưng KHÔNG có hàng ratio nào
    # = bị LOẠI vì không có bar trong cửa sổ PIT (trước đây chúng bị gán giá TƯƠNG LAI).
    pairs_fund = {
        (s, p)
        for s, p in con.execute(
            "SELECT DISTINCT symbol, period FROM financial_facts "
            "WHERE metric IN ('EPS', 'TOTAL_EQUITY') AND value IS NOT NULL"
        )
    }
    pairs_score = {(s, p) for s, p in con.execute("SELECT DISTINCT symbol, period FROM valuation_scores")}
    out["pairs_with_fundamentals"] = len(pairs_fund)
    out["pairs_scored"] = len(pairs_score)
    out["pairs_excluded_no_price"] = len(pairs_fund - pairs_score)
    # Chẻ nhỏ để KHÔNG trộn hai cơ chế mất độ phủ (luật AGENTS: phân loại, cấm gộp một con số):
    #  · trước 2018 ⇒ chuỗi giá trong store CHƯA tồn tại (PRICE_MISSING) — thiếu nguồn, không phải lỗi;
    #  · từ 2018 ⇒ đây là nhóm "di sản" từng bị gán giá TƯƠNG LAI (636 cặp đo ở BƯỚC P).
    _ex = pairs_fund - pairs_score
    out["excl_before_2018"] = sum(1 for _s, p in _ex if p[:4] < "2018")
    out["excl_from_2018"] = sum(1 for _s, p in _ex if p[:4] >= "2018")
    out["clean_by_ratio"] = con.execute(
        "SELECT ratio_name, COUNT(*) FROM valuation_scores WHERE is_clean_metric=1 GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
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


def missing_effective_date(db: Path) -> int:
    """Số dòng `financial_facts` thiếu `effective_date` ⇒ cần migration [A] trước khi tính lại.

    [WHY] Tách khỏi `main()` để test được nhánh fail-closed của [A]: nếu để inline, muốn kiểm
    "bước [A] thất bại ⇒ dừng" thì test phải giả lập cả một connection sqlite.
    """
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM financial_facts WHERE effective_date IS NULL").fetchone()[0]
    finally:
        con.close()


def _run_child(script: str, tag: str) -> int:
    """Chạy script con và TRẢ mã thoát THẬT; in chẩn đoán của nó khi thất bại.

    [WHY] ĐO ĐƯỢC (Bước 3, 2026-10-16): bản cũ dùng `subprocess.call(..., stdout=DEVNULL)` rồi chỉ in
    `[C] exit=2` và ĐI TIẾP, kết thúc exit 0 — trong khi `migrate_shares_history.py` đã FAIL-CLOSED vì
    canary `('HPG','2018Q1','CHARTER_PIT')` + `('HPG','2025Q2','CHARTER_PIT')`. Báo cáo tổng ghi
    "791 mã OK · 0 lỗi" ⇒ che giấu một bước con thất bại (cùng lớp lỗi với "cấm pipe che mã thoát").

    [ROOT CAUSE] `subprocess.call` trả mã thoát nhưng caller không kiểm; `DEVNULL` xoá luôn chẩn đoán.

    [INVARIANT] Mã thoát của bước con PHẢI lan truyền: `rc != 0` ⇒ caller DỪNG và KHÔNG ghi gì.
    """
    p = subprocess.run(
        [sys.executable, str(Path(__file__).with_name(script)), "--apply"],
        capture_output=True,
        text=True,
    )
    print(f"   [{tag}] exit={p.returncode}")
    if p.returncode != 0:
        lines = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()[-20:]
        print(f"   [{tag}] CHẨN ĐOÁN (20 dòng cuối của script con):")
        for line in lines:
            print(f"       | {line}")
    return p.returncode


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
    missing = missing_effective_date(db)
    if missing:
        print(f"\n   [A] {missing:,} dòng thiếu effective_date ⇒ chạy lại migration A...")
        rc = _run_child("migrate_financial_facts_effective_date.py", "A")
        if rc != 0:
            print("\n   KẾT LUẬN: FAIL — bước [A] thất bại (fail-closed), KHÔNG ghi gì.")
            return 6

    # ── Hạng mục C ────────────────────────────────────────────────────────────────────
    print("\n   [C] chạy migrate_shares_history.py --apply...")
    rc = _run_child("migrate_shares_history.py", "C")
    if rc != 0:
        print("\n   KẾT LUẬN: FAIL — bước [C] thất bại (fail-closed), KHÔNG ghi gì.")
        return 7

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

    # ── Đánh dấu dòng CŨ (chưa qua PIT) — KHÔNG xoá cứng, chỉ loại khỏi xếp hạng ─────────
    con2 = sqlite3.connect(f"file:{db.as_posix()}?mode=rw", uri=True, timeout=120.0)
    con2.execute("PRAGMA busy_timeout=120000")
    cur = con2.execute(
        "UPDATE valuation_scores SET anomaly_flags='LEGACY_PRE_PIT', is_clean_metric=0 "
        "WHERE is_clean_metric IS NULL"
    )
    n_legacy = cur.rowcount
    con2.commit()
    # Kỳ CHƯA tới ngày hiệu lực (`effective_date` > hôm nay): engine ĐÃ chặn nên lần chạy này không
    # sinh mới, nhưng các dòng do lần chạy TRƯỚC ghi ra vẫn còn ⇒ đánh dấu (KHÔNG xoá) để consumer loại.
    today = date.today().isoformat()
    fut = [
        r[0]
        for r in con2.execute(
            "SELECT DISTINCT period FROM financial_facts WHERE effective_date > ? AND period IS NOT NULL",
            (today,),
        )
    ]
    n_fut = 0
    if fut:
        ph = ",".join("?" * len(fut))
        cur = con2.execute(
            "UPDATE valuation_scores SET anomaly_flags="
            "COALESCE(anomaly_flags || ',', '') || 'PIT_FUTURE_PERIOD', is_clean_metric=0 "
            f"WHERE period IN ({ph})",
            fut,
        )
        n_fut = cur.rowcount
        con2.commit()
    con2.close()
    print(f"   [B] {n_legacy:,} dòng CŨ (chưa qua PIT) ⇒ gắn LEGACY_PRE_PIT + is_clean_metric=0 (không xoá)")
    print(f"   [B] {n_fut:,} dòng thuộc kỳ CHƯA tới ngày hiệu lực {fut or '-'} ⇒ PIT_FUTURE_PERIOD + is_clean=0")

    print("\n   NGHIỆM THU (DoD Hạng mục B):")
    rep = dod_report(db)
    for k, v in rep.items():
        print(f"     {k:26s}: {v}")
    print("\n   3 TIÊU CHÍ:")
    print(f"     (1) cặp bị LOẠI vì không có bar trong cửa sổ PIT: {rep.get('pairs_excluded_no_price', '?')}"
          f"  (trước đây các cặp này bị gán giá TƯƠNG LAI; nay không sinh hàng ratio nào)")
    _a, _c = rep.get("pe_lt2_all", "?"), rep.get("pe_lt2_clean", "?")
    print(f"     (2) số dòng PE < 2                  : {_a} (tổng) / {_c} (được coi là sạch)")
    print(f"     (3) median PE (dòng sạch)           : {rep.get('pe_median')}  (n={rep.get('pe_n')})")
    return 0 if err == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
