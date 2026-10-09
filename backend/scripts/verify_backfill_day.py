"""verify_backfill_day.py — Nghiệm thu 1 phiên backfill (4 tiêu chí).

Usage:
    python backend/scripts/verify_backfill_day.py --date 2026-09-30 [--log <path>] [--samples HPG,VNM,SSI,PNJ,ACB]

Tiêu chí:
  1. is_stale của phiên (phải tiệm cận 0)
  2. số mã ghi được trong daily_ohlcv
  3. đối chiếu chéo close với vci.history (n mã mẫu)
  4. log: BACKFILL_DONE / G2_BAND_REJECT / G3_* / FOREIGN_FLOW_BLOCKED
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src", PROJECT_ROOT / "backend" / "libs" / "vnstock"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
DEFAULT_SAMPLES = ["HPG", "VNM", "SSI", "PNJ", "ACB"]

from src.utils.price_scale import UNIT_THOUSAND_VND, compare_with_store  # noqa: E402


def check_db(date: str, samples):
    conn = sqlite3.connect(str(DB))
    n = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date=?", (date,)).fetchone()[0]
    stale = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date=? AND is_stale=1", (date,)).fetchone()[0]
    print(f"[1] is_stale {date}: {stale}/{n}" + ("  ✅" if n and stale == 0 else "  ⚠️" if n else "  ❌ (không có dữ liệu)"))
    print(f"[2] số mã ghi được  : {n}")
    rows = {}
    for s in samples:
        r = conn.execute("SELECT close, volume FROM daily_ohlcv WHERE symbol=? AND date=?", (s, date)).fetchone()
        rows[s] = r
    conn.close()
    return n, stale, rows


def check_provider(date: str, samples, local_rows):
    from src.providers.vnstock_provider import VnstockProvider

    p = VnstockProvider(source="vci")
    print(f"[3] đối chiếu chéo với vci.history ({date}):")
    ok = bad = miss = 0
    for s in samples:
        try:
            df = p.history(s, start=date, end=date)
        except Exception as e:  # noqa: BLE001 - best-effort
            print(f"    {s:5s} lỗi fetch: {e}")
            miss += 1
            continue
        ref = None
        if df is not None and not df.empty:
            cols = [str(c).lower() for c in df.columns]
            dcol = "time" if "time" in cols else ("date" if "date" in cols else None)
            ccol = "close" if "close" in cols else None
            if dcol and ccol:
                dc = df.columns[cols.index(dcol)]
                cc = df.columns[cols.index(ccol)]
                mask = [str(x)[:10] == date for x in df[dc]]
                sub = df[mask]
                if len(sub):
                    ref = float(sub.iloc[0][cc])
        local = local_rows.get(s)
        lc = float(local[0]) if local and local[0] is not None else None
        if ref is None:
            print(f"    {s:5s} local={lc} | vci=không có  → bỏ qua")
            miss += 1
        else:
            # So sánh qua hàm chuẩn hoá duy nhất (vci trả NGHÌN đồng, store lưu ĐỒNG).
            match, ref_vnd = compare_with_store(lc, ref, provider_unit=UNIT_THOUSAND_VND)
            if match:
                print(f"    {s:5s} local={lc:,.0f} | vci={ref_vnd:,.0f}  ✅")
                ok += 1
            else:
                print(f"    {s:5s} local={lc} | vci={ref_vnd if ref_vnd else None}  ❌ LỆCH")
                bad += 1
    print(f"    → khớp {ok} | lệch {bad} | không có dữ liệu {miss}")
    return ok, bad, miss


def check_log(log_path: str):
    print("[4] log backfill:")
    if not log_path or not Path(log_path).exists():
        print("    (không có log)")
        return
    txt = Path(log_path).read_text(encoding="utf-8", errors="replace")
    for key in ("BACKFILL_SOURCE_SELECTED", "BACKFILL_DONE", "G3_SNAPSHOT_DUPLICATION_SUSPECTED",
                "FOREIGN_FLOW_BLOCKED", "BACKFILL_EMPTY", "BACKFILL_NO_SOURCE", "EXIT_"):
        m = [ln for ln in txt.splitlines() if key in ln]
        if m:
            print(f"    {key}: {len(m)} dòng | cuối: {m[-1].strip()[:150]}")
    for key in ("G1_DATE_MATCH_FAIL", "G2_BAND_REJECT", "G2_NO_REFERENCE", "BACKFILL_FETCH_FAIL", "INGESTION_SCALE_REJECT",
                "INGESTION_SCALE_TRANSFORM"):
        print(f"    {key}: {txt.count(key)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--samples", default=",".join(DEFAULT_SAMPLES))
    args = ap.parse_args()
    samples = [s.strip().upper() for s in args.samples.split(",") if s.strip()]
    print(f"=== NGHIỆM THU BACKFILL {args.date} ===")
    _, _, local_rows = check_db(args.date, samples)
    check_provider(args.date, samples, local_rows)
    check_log(args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
