"""selection_diagnostic.py — READ-ONLY: chỉ đọc replay DB, in selection diagnostic.

Trả lời câu hỏi cốt lõi: "Selected có outperform eligible-but-rejected hay không?"
So sánh return H20 của EXECUTE vs eligible WATCH/REJECT trong cùng p_gain bucket.
"""

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
DATA = BACKEND / "data" / "replays"
sys.path.insert(0, str(BACKEND / "src"))

import statistics

YEARS = ("2022", "2023", "2024", "2025")


def load(year: str, db_prefix: str = "replay_58") -> list[dict]:
    conn = sqlite3.connect(str(DATA / f"{db_prefix}_{year}.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT date, symbol, action, decision, p_gain, decision_quality, "
            "n_independent_evidence, evidence_clusters, slot_consumed, slot_index, "
            "return_pct, return_pct_60, return_pct_120, opportunity_cost, decision_reason "
            "FROM decision_ledger ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def summarize(rows, label):
    r = [x for x in rows if x["return_pct"] is not None]
    if not r:
        print(f"  {label}: n_resolved=0")
        return None
    wins = [x for x in r if x["return_pct"] > 0]
    rp = [x["return_pct"] for x in r]
    print(
        f"  {label}: n={len(r)} winrate={len(wins) / len(r) * 100:.1f}% "
        f"meanR20={mean(rp):+.2f}% medianR20={statistics.median(rp):+.2f}% "
        f"min={min(rp):+.1f} max={max(rp):+.1f} (H20)"
    )
    return r


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Selection diagnostic (READ-ONLY)")
    ap.add_argument("--db-prefix", default="replay_58", help="Tiền tố DB trong data/replays")
    args = ap.parse_args()

    for y in YEARS:
        rows = load(y, args.db_prefix)
        print("=" * 80)
        print(f"  {y} — total rows={len(rows)}")
        deploy = [r for r in rows if r["action"] in ("OPEN", "SCALE_IN")]
        print(f"  deployment candidates (OPEN/SCALE_IN): {len(deploy)}")
        if not deploy:
            print("  -> KHÔNG có candidate hợp lệ nào (p_gain max xem dưới)")
            pg = [r["p_gain"] for r in rows]
            print(f"  p_gain range {min(pg):.2f}..{max(pg):.2f}")
            continue

        # p_gain distribution pre-selection
        over045 = sum(1 for r in deploy if r["p_gain"] >= 0.45)
        over050 = sum(1 for r in deploy if r["p_gain"] >= 0.50)
        over055 = sum(1 for r in deploy if r["p_gain"] >= 0.55)
        print(f"  deploy p_gain>=0.45: {over045} | >=0.50: {over050} | >=0.55: {over055}")

        exe = [r for r in deploy if r["decision"] == "EXECUTE"]
        watch = [r for r in deploy if r["decision"] == "WATCH"]
        rejected = [r for r in deploy if r["decision"] == "REJECT"]
        print(f"  decisions: EXECUTE={len(exe)} WATCH={len(watch)} REJECT={len(rejected)}")

        known = [r for r in deploy if r["decision"] in ("EXECUTE", "WATCH", "REJECT")]
        el = [r for r in known if r["p_gain"] >= 0.45]
        print("\n  --- Selected vs eligible-but-rejected (>=0.45) ---")
        print(f"  eligible pool n={len(el)}")
        summarize(exe, "selected EXECUTE")
        rejected_el = [r for r in el if r["decision"] != "EXECUTE"]
        summarize(rejected_el, "eligible-but-rejected")

        # bucket comparison
        print("\n  --- p_gain bucket comparison (return H20 resolved only) ---")
        for lo, hi, lbl in [
            (0.45, 0.50, "0.45-0.50"),
            (0.50, 0.55, "0.50-0.55"),
            (0.55, 0.60, "0.55-0.60"),
            (0.60, 1.01, ">=0.60"),
        ]:
            b = [r for r in known if lo <= r["p_gain"] < hi]
            if not b:
                continue
            b_exe = [r for r in b if r["decision"] == "EXECUTE"]
            b_rej = [r for r in b if r["decision"] != "EXECUTE"]
            summarize(b_exe, f"  bucket[{lbl}] EXECUTE")
            summarize(b_rej, f"  bucket[{lbl}] rejected")
        # decision_quality / evidence
        print("\n  --- decision_quality / evidence (EXECUTE resolved) ---")
        q = [r for r in exe if r["return_pct"] is not None]
        dq = [r["decision_quality"] for r in q]
        dqset = sorted(set(dq))
        print(f"  decision_quality values: {dqset}")
        ev = [r["n_independent_evidence"] for r in q]
        if ev:
            print(f"  n_independent_evidence: min={min(ev)} avg={mean(ev):.1f} max={max(ev)}")
        # budget binding days
        slot_days = sorted(set(r["date"] for r in exe))
        print("\n  --- budget binding ---")
        slot_first = slot_days[0] if slot_days else "-"
        slot_last = slot_days[-1] if slot_days else "-"
        print(f"  EXECUTE phân bổ {len(slot_days)} ngày: từ {slot_first} đến {slot_last}")
        all_days = sorted(set(r["date"] for r in rows))
        days_none_eligible = [
            d
            for d in all_days
            if not any(r["date"] == d and r["action"] in ("OPEN", "SCALE_IN") and r["p_gain"] >= 0.45 for r in known)
        ]
        print(f"  số ngày không có candidate >=0.45: {len(days_none_eligible)}/{len(all_days)}")

        # top/bottom selected candidates
        print("\n  --- top/bottom selected (by return) ---")
        resolved_exe = [r for r in exe if r["return_pct"] is not None]
        resolved_exe.sort(key=lambda r: r["return_pct"])
        print("  bottom-5:")
        for r in resolved_exe[:5]:
            oc = r.get("opportunity_cost")
            print(
                f"    {r['date']} {r['symbol']} p={r['p_gain']:.2f} "
                f"dq={r.get('decision_quality')} r20={r['return_pct']:+.2f} "
                f"oc={oc} slot={r['slot_index']}"
            )
        print("  top-5:")
        for r in resolved_exe[-5:][::-1]:
            oc = r.get("opportunity_cost")
            print(
                f"    {r['date']} {r['symbol']} p={r['p_gain']:.2f} "
                f"dq={r.get('decision_quality')} r20={r['return_pct']:+.2f} "
                f"oc={oc} slot={r['slot_index']}"
            )


if __name__ == "__main__":
    main()
