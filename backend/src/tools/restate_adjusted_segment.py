"""
restate_adjusted_segment.py
===========================
Mục (b) NQ-23: khôi phục giá RAW cho các phiên `daily_ohlcv` bị trộn nguồn (vendor trả giá đã back-adjust
cho thưởng cổ phiếu, trong khi phần còn lại của chuỗi là giá thô chưa điều chỉnh).

[WHY] FPT thưởng 10:1 (GDKHQ 2026-09-21). Nguồn `vci` ghi các phiên TRƯỚC ngày GDKHQ ở dạng đã chia 1,1,
còn các phiên `kbs` lân cận là raw. Hệ quả: nhảy giá giả (07-15 66.800 -> 07-16 61.820; 09-17 74.300 -> 09-18 65.180)
và NAV paper bị hụt vì số lượng cổ phiếu còn là số lượng TRƯỚC thưởng.
[INVARIANT] raw = adjusted x factor. Mọi giá sau khi nhân phải rơi vào lưới tick 100đ (sai số <= GRID_TOL);
một giá lệch lưới => DỪNG toàn bộ (missing > fake). Phiên 2026-09-18 phải khớp cum_close của registry.
Usage: python backend/src/tools/restate_adjusted_segment.py --dry-run | --apply
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if ROOT.name == "backend":
    ROOT = ROOT.parent
DB = ROOT / "backend" / "data" / "screener_cache.db"
sys.path.insert(0, str(Path(__file__).resolve().parent))

SYMBOL = "FPT"
FACTOR = 1.1
TICK = 100.0
GRID_TOL = 10.0  # vendor làm tròn adj tới 10đ => x1,1 sai tối đa ~11đ
CUM_CLOSE_CHECK = {"2026-09-18": 71700.0}  # paper_corporate_actions FPT|2026-09-21|STOCK_DIV


def candidate_rows(conn):
    """Phiên `vci` của FPT TRƯỚC ngày GDKHQ => đang ở dạng adjusted."""
    return conn.execute(
        "SELECT date, open, high, low, close, adj_close FROM daily_ohlcv "
        "WHERE symbol=? AND source='vci' AND date < '2026-09-21' ORDER BY date",
        (SYMBOL,),
    ).fetchall()


def to_raw(x):
    v = x * FACTOR
    snapped = round(v / TICK) * TICK
    if abs(v - snapped) > GRID_TOL:
        raise ValueError(f"giá {x} x {FACTOR} = {v:.2f} lệch lưới tick {TICK:.0f} quá {GRID_TOL}")
    return snapped


def build_plan(conn):
    plan = []
    for d, o, h, low_v, c, a in candidate_rows(conn):
        new = [to_raw(v) for v in (o, h, low_v, c, a)]
        if d in CUM_CLOSE_CHECK and new[3] != CUM_CLOSE_CHECK[d]:
            raise ValueError(f"{d}: close tái dựng {new[3]} != cum_close registry {CUM_CLOSE_CHECK[d]}")
        plan.append({"date": d, "old": [o, h, low_v, c, a], "new": new})
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        sys.exit("Chỉ định --dry-run hoặc --apply")

    conn = sqlite3.connect(DB)
    plan = build_plan(conn)
    print(f"[*] {SYMBOL}: {len(plan)} phiên cần restate (x{FACTOR})")
    for p in plan:
        print(f"  {p['date']}  close {p['old'][3]:>10,.0f} -> {p['new'][3]:>10,.0f}")
    if not plan:
        return

    from backfill_historical_equity import compute_daily_equity  # cùng thư mục tools

    if args.dry_run:
        print("[i] DRY-RUN: không ghi DB.")
        return

    cur = conn.cursor()
    for p in plan:
        n = p["new"]
        cur.execute(
            "UPDATE daily_ohlcv SET open=?, high=?, low=?, close=?, adj_close=?, source='vci_restated_x1.1' "
            "WHERE symbol=? AND date=?",
            (*n, SYMBOL, p["date"]),
        )
    eq_changes = []
    for p in plan:
        old = cur.execute("SELECT market_value, total_equity FROM paper_equity_curve WHERE date=?", (p["date"],)).fetchone()
        if not old:
            continue
        rec = compute_daily_equity(conn, p["date"])
        cur.execute(
            "UPDATE paper_equity_curve SET market_value=?, unrealized_pnl=?, total_equity=? WHERE date=?",
            (rec["market_value"], rec["unrealized_pnl"], rec["total_equity"], p["date"]),
        )
        eq_changes.append((p["date"], old[1], rec["total_equity"]))
    delta = sum(b - a for _, a, b in eq_changes)
    digest = hashlib.sha256(json.dumps({"ohlcv": plan, "equity": eq_changes}, sort_keys=True).encode()).hexdigest()
    cur.execute(
        "INSERT INTO eod_write_audit_ledger (run_timestamp, as_of_date, caller_module, target_table, parameters, "
        "input_hash, output_score, status, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            datetime.now().isoformat(),
            plan[-1]["date"],
            "restate_adjusted_segment.py",
            "daily_ohlcv+paper_equity_curve",
            f"symbol={SYMBOL} factor={FACTOR} ohlcv_rows={len(plan)} equity_rows={len(eq_changes)}",
            digest,
            delta,
            "RESTATED",
            f"NQ-23 muc (b): khoi phuc gia raw FPT; tong tang NAV {delta:,.0f} VND",
        ),
    )
    conn.commit()
    print(f"[+] APPLY: {len(plan)} dòng OHLCV, {len(eq_changes)} dòng equity; tổng NAV điều chỉnh {delta:+,.0f} VND")
    print(f"[+] audit RESTATED sha256={digest[:16]}...")


if __name__ == "__main__":
    main()
