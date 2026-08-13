"""apply_selection_v0.py — Áp Selection Layer v0 lên replay DB + re-resolve outcomes.

Selection Layer v0 (backtest):
  per-day ranking theo p_gain desc + daily_cap=1 + p_gain_min=0.55 + symbol dedup 1/năm.
  Thay thế hoàn toàn FIFO budget post-hoc trong replay_ledger_2022.write_ledger_once.

Cách hoạt động:
  1. Đọc toàn bộ decision_ledger của từng năm.
  2. select_year() trả về {decision_id: final_decision} cho deployment candidates.
  3. Cập nhật decision + slot_consumed + slot_index + decision_reason vào DB.
  4. Re-resolve outcomes (EXECUTE mới) + OC bounded — reuse reset_replay_outcomes.

QUYỀN GHI CÓ CHỦ ĐÍCH: chỉ sửa decision_ledger của replay DB (gitignored).
PIT-safe: selection dùng date/p_gain/action (decision-time); return_pct chỉ để resolve.
Contamination boundary: 2022-2024 IS evidence; 2025 DESCRIPTIVE.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
DATA = BACKEND / "data" / "replays"
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

from calibration.selection_layer import EXECUTE, WATCH, select_year

YEARS = ("2022", "2023", "2024", "2025")
BUDGET = 20
DAILY_CAP = 1
P_GAIN_MIN = 0.55

DEPLOY_ACTIONS = {"BUY", "SCALE_IN", "OPEN"}


def apply_selection(db_path: Path, budget: int, daily_cap: int, p_gain_min: float) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r) for r in conn.execute("SELECT decision_id, date, symbol, p_gain, action, decision FROM decision_ledger")
        ]
    finally:
        conn.close()

    sel = select_year(rows, budget=budget, daily_cap=daily_cap, p_gain_min=p_gain_min)

    # Update decisions. Reset slot/slot_index trước, rồi gán lại cho EXECUTE.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE decision_ledger SET slot_consumed=0, slot_index=NULL, decision_reason=NULL")
        n_exec = 0
        n_watch = 0
        slot = 0
        # gán slot theo thứ tự ngày tăng dần (PIT-safe); date map dựng 1 lần
        date_map = {r["decision_id"]: r["date"] for r in rows}
        ordered = sorted(sel.items(), key=lambda kv: date_map[kv[0]])
        for did, decision in ordered:
            if decision == EXECUTE:
                slot += 1
                n_exec += 1
                conn.execute(
                    "UPDATE decision_ledger SET decision=?, slot_consumed=1, slot_index=?, "
                    "decision_reason='SELECTION_V0_TOP_RANK' WHERE decision_id=?",
                    (EXECUTE, slot, did),
                )
            elif decision == WATCH:
                n_watch += 1
                conn.execute(
                    "UPDATE decision_ledger SET decision=?, slot_consumed=0, slot_index=NULL, "
                    "decision_reason='SELECTION_V0_BELOW_TOP' WHERE decision_id=?",
                    (WATCH, did),
                )
            # REJECT giữ nguyên decision gốc
        conn.commit()
        return {"n_exec": n_exec, "n_watch": n_watch}
    finally:
        conn.close()


def reset_executes(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE decision_ledger SET outcome_status='UNRESOLVED', entry_price=NULL, "
            "exit_price=NULL, return_pct=NULL, return_pct_60=NULL, return_pct_120=NULL, "
            "resolved_at=NULL, counterfactual_symbol=NULL, counterfactual_return=NULL, "
            "opportunity_cost=NULL, opportunity_cost_60=NULL, opportunity_cost_120=NULL "
            "WHERE decision='EXECUTE'"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def resolve_ids(db_path: Path) -> list[int]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT decision_id FROM decision_ledger "
            "WHERE decision='EXECUTE' "
            "OR (decision IN ('WATCH','REJECT') AND action IN ('BUY','SCALE_IN','OPEN') "
            "    AND p_gain >= 0.5) "
            "ORDER BY decision_id"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Áp Selection Layer v0 lên replay DB")
    ap.add_argument("--years", default="all")
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--p-gain-min", type=float, default=P_GAIN_MIN)
    ap.add_argument(
        "--db-prefix",
        default="replay",
        help="Tiền tố tên DB trong data/replays (vd 'replay_58' -> replay_58_2022.db)",
    )
    args = ap.parse_args()

    years = YEARS if args.years == "all" else tuple(y for y in args.years.split(",") if y in YEARS)

    for y in years:
        db = DATA / f"{args.db_prefix}_{y}.db"
        if not db.exists():
            print(f"[SKIP] {db.name} không tồn tại")
            continue
        r = apply_selection(db, args.budget, args.daily_cap, args.p_gain_min)
        reset_executes(db)
        ids = resolve_ids(db)
        from calibration.opportunity_cost import resolve_outcomes

        res = resolve_outcomes(db_path=str(db), hold_days=20, decision_ids=ids)
        print(
            f"{y}: selection EXEC={r['n_exec']} WATCH={r['n_watch']} | "
            f"resolved={res['n_resolved']} skipped={res['n_skipped']}",
            flush=True,
        )
    print("DONE")


if __name__ == "__main__":
    main()
