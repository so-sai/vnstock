"""reset_replay_outcomes.py — Reset & re-resolve outcomes + OC cho replay DB.

QUYỀN GHI CÓ CHỦ ĐÍCH (đối nghịch với analyzer read-only):
  - Reset outcome_status='UNRESOLVED' cho 80 EXECUTE trong 4 file replay.
  - resolve_outcomes(decision_ids=EXECUTE+alternatives) → bounded, không
    iterate ~100k WATCH/REJECT còn UNRESOLVED.
  - Tính opportunity_cost H20/60/120 trực tiếp trên tập bounded (reuse hằng
    số từ calibration.opportunity_cost) — KHÔNG gọi compute_opportunity_cost
    vì hàm đó re-resolve toàn ledger (bẫy hiệu năng).

Idempotent: chạy lại an toàn.
Contamination boundary: 2025 DESCRIPTIVE (OOS), KHÔNG dùng để chọn tham số.
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

from calibration.opportunity_cost import (
    CAPITAL_DEPLOYMENT_ACTIONS,
    HORIZONS,
    NGUONG_ELIGIBLE,
    resolve_outcomes,
)

YEARS = ("2022", "2023", "2024", "2025")


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
    """EXECUTE + WATCH/REJECT đủ điều kiện alternative (deployment action, p_gain>=0.5).

    Benchmark (p_gain) và action là decision-time, không có look-ahead.
    """
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


def bounded_opportunity_cost(db_path: Path) -> dict:
    """OC chỉ trên EXECUTE đã RESOLVED + alternative (bounded), không full-scan.

    OC_H = R_best_eligible_rejected − R_executed (cùng H, cùng ngày).
    Best eligible xác định tại decision-time theo action + p_gain (NHƯ compute_opportunity_cost).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT decision_id, date, symbol, action, decision, p_gain, return_pct, "
            "return_pct_60, return_pct_120 FROM decision_ledger "
            "WHERE decision IN ('EXECUTE','WATCH','REJECT') "
            "AND outcome_status='RESOLVED'"
        ).fetchall()
        all_dec = [dict(r) for r in rows]
    finally:
        conn.close()

    by_date: dict[str, list[dict]] = {}
    for d in all_dec:
        by_date.setdefault(d["date"], []).append(d)

    updated = 0
    no_alt = 0
    conn = sqlite3.connect(str(db_path))
    try:
        for date, group in by_date.items():
            best_alt: dict[str, dict] = {}
            for h in HORIZONS:
                best_alt[str(h)] = None
            for d in group:
                if d["decision"] not in ("WATCH", "REJECT"):
                    continue
                if (d.get("action") or "").upper() not in CAPITAL_DEPLOYMENT_ACTIONS:
                    continue
                if (d.get("p_gain") or 0.0) < NGUONG_ELIGIBLE:
                    continue
                for h in HORIZONS:
                    key = str(h)
                    r = d.get(f"return_pct_{h}") if h != 20 else d.get("return_pct")
                    if r is None:
                        continue
                    cur = best_alt[key]
                    if cur is None or r > cur["return_pct"]:
                        best_alt[key] = {"symbol": d["symbol"], "return_pct": r, "decision_id": d["decision_id"]}

            for d in group:
                if d["decision"] != "EXECUTE":
                    continue
                r_exec = d.get("return_pct")
                if r_exec is None:
                    continue
                best20 = best_alt["20"]
                if best20 is None or best20["decision_id"] == d["decision_id"]:
                    no_alt += 1
                    conn.execute(
                        "UPDATE decision_ledger SET counterfactual_symbol=NULL, "
                        "counterfactual_return=NULL, opportunity_cost=NULL, "
                        "opportunity_cost_60=NULL, opportunity_cost_120=NULL WHERE decision_id=?",
                        (d["decision_id"],),
                    )
                    continue
                oc20 = round(best20["return_pct"] - r_exec, 4)
                oc60 = oc120 = None
                r60 = d.get("return_pct_60")
                r120 = d.get("return_pct_120")
                b60 = best_alt["60"]
                b120 = best_alt["120"]
                if r60 is not None and b60 is not None and b60["decision_id"] != d["decision_id"]:
                    oc60 = round(b60["return_pct"] - r60, 4)
                if r120 is not None and b120 is not None and b120["decision_id"] != d["decision_id"]:
                    oc120 = round(b120["return_pct"] - r120, 4)
                conn.execute(
                    "UPDATE decision_ledger SET counterfactual_symbol=?, "
                    "counterfactual_return=?, opportunity_cost=?, "
                    "opportunity_cost_60=?, opportunity_cost_120=? WHERE decision_id=?",
                    (best20["symbol"], best20["return_pct"], oc20, oc60, oc120, d["decision_id"]),
                )
                updated += 1
        conn.commit()
        return {"n_updated": updated, "n_no_alt": no_alt}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset & re-resolve outcomes + OC bounded cho replay DB")
    ap.add_argument("--years", default="all", help="2022,2023,... hoặc all")
    args = ap.parse_args()

    years = YEARS if args.years == "all" else tuple(y for y in args.years.split(",") if y in YEARS)

    for y in years:
        db = DATA / f"replay_{y}.db"
        if not db.exists():
            print(f"[SKIP] {db.name} không tồn tại")
            continue
        n_reset = reset_executes(db)
        ids = resolve_ids(db)
        r = resolve_outcomes(db_path=str(db), hold_days=20, decision_ids=ids)
        oc = bounded_opportunity_cost(db_path=str(db))
        print(
            f"{y}: reset={n_reset} resolved_ids={len(ids)} resolved={r['n_resolved']} "
            f"skipped={r['n_skipped']} | OC updated={oc['n_updated']} no_alt={oc['n_no_alt']}",
            flush=True,
        )
    print("DONE")


if __name__ == "__main__":
    main()
