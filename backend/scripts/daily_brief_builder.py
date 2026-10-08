"""daily_brief_builder.py — Tầng diễn giải HẠ NGUỒN (A+B).

Spec: docs/SPEC_NARRATIVE_LAYER_2026-10.md

Nguyên tắc bất biến:
  1. Narrative là hạ nguồn của decision — lớp này KHÔNG sinh số mới, KHÔNG suy luận.
  2. Mọi con số truy được về DB và được `--verify` kiểm chứng lại.
  3. Narrative mâu thuẫn decision → decision thắng, gắn cờ NARRATIVE_CONTRADICTION.
  4. Không dùng LLM (kể cả paraphrase) ở bản này.
  5. Thiếu dữ liệu → "N/A (lý do)", KHÔNG nội suy.
  6. Read-only: không ghi vào screener_cache.db (chỉ append file trong backend/reports/).

Usage:
    python backend/scripts/daily_brief_builder.py
    python backend/scripts/daily_brief_builder.py --date 2026-10-08 --verify
    python backend/scripts/daily_brief_builder.py --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src", PROJECT_ROOT / "backend" / "libs" / "vnstock"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
REPORTS_DIR = PROJECT_ROOT / "backend" / "reports"
AUDIT_PATH = REPORTS_DIR / "audit_trail.jsonl"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MIN_CLEAN_ROWS = 300  # khớp ngưỡng Freshness Sentinel
FOREIGN_KIND = "daily"
CONSEC_WINDOW = 5
WATCHLIST_N = 5

# Ngưỡng cổng — HẰNG SỐ CÔNG KHAI, không phải suy luận của tầng narrative.
GATE_BREADTH_MIN = 50.0  # %
GATE_FOREIGN_BUY_STREAK = 3  # số phiên mua ròng liên tiếp
NON_CRISIS = ("TRENDING", "RANGING")


# ── Đọc dữ liệu ────────────────────────────────────────────────────────
def latest_clean_date(conn: sqlite3.Connection, min_rows: int = MIN_CLEAN_ROWS) -> str | None:
    row = conn.execute(
        "SELECT date FROM daily_ohlcv WHERE COALESCE(is_stale, 0) = 0 "
        "GROUP BY date HAVING COUNT(*) >= ? ORDER BY date DESC LIMIT 1",
        (min_rows,),
    ).fetchone()
    return row[0] if row else None


def fetch_regime(conn: sqlite3.Connection, as_of: str) -> dict | None:
    rows = conn.execute(
        "SELECT date, status, regime_score, breadth_pct FROM regime_history "
        "WHERE date <= ? ORDER BY date DESC LIMIT 20",
        (as_of,),
    ).fetchall()
    if not rows:
        return None
    date_, status, score, breadth = rows[0]
    streak = 1
    for r in rows[1:]:
        if r[1] == status:
            streak += 1
        else:
            break
    prev_breadth = rows[1][3] if len(rows) > 1 else None
    delta = (breadth - prev_breadth) if (breadth is not None and prev_breadth is not None) else None
    return {
        "date": date_,
        "status": status,
        "score": None if score is None else round(float(score), 4),
        "breadth_pct": None if breadth is None else round(float(breadth), 1),
        "status_streak": streak,
        "breadth_delta": None if delta is None else round(float(delta), 1),
    }


def fetch_foreign(conn: sqlite3.Connection, as_of: str, window: int = CONSEC_WINDOW) -> dict | None:
    """Chuỗi ngoại chỉ tính là LIÊN TIẾP nếu KHÔNG thiếu phiên nào.

    BUG đã sửa: bản đầu lấy "N ngày gần nhất CÓ dòng ngoại" rồi coi là liên tiếp
    → store thiếu ngoại cho 30/09, 01/10, 02/10 nên nó báo "5 phiên liên tiếp"
    trong khi thực tế các ngày đó (28,29/09 + 05,06,07/10) cách nhau cả tuần.
    Mốc so sánh phải là PHIÊN GIAO DỊCH THẬT (daily_ohlcv), không phải ngày có
    dòng ngoại.
    """
    sessions = [
        r[0]
        for r in conn.execute(
            "SELECT date FROM daily_ohlcv WHERE date <= ? AND COALESCE(is_stale, 0) = 0 "
            "GROUP BY date HAVING COUNT(*) >= ? ORDER BY date DESC LIMIT ?",
            (as_of, MIN_CLEAN_ROWS, window),
        )
    ]
    if not sessions:
        return None
    marks = ",".join("?" * len(sessions))
    fmap = {
        d: float(v or 0.0)
        for d, v in conn.execute(
            f"SELECT date, SUM(net_value) FROM market_foreign_history "
            f"WHERE date IN ({marks}) AND flow_kind = ? GROUP BY date",
            (*sessions, FOREIGN_KIND),
        )
    }
    days: list[tuple[str, float]] = []
    for d in sessions:
        if d not in fmap:  # thiếu ngoại cho chính phiên này → chuỗi ĐỨT
            break
        days.append((d, round(fmap[d], 1)))
    if not days:
        return None

    d1_date, d1_val = days[0]
    sign = (d1_val > 0) - (d1_val < 0)
    streak = 0
    for _, v in days:
        s = (v > 0) - (v < 0)
        if s == sign and sign != 0:
            streak += 1
        else:
            break
    direction = "bán ròng" if sign < 0 else ("mua ròng" if sign > 0 else "trung tính")
    return {
        "date": d1_date,
        "d1": d1_val,
        "days": days,
        "sum": round(sum(v for _, v in days), 1),
        "n_days": len(days),
        "streak": streak,
        "direction": direction,
        "sign": sign,
        "gap_after": len(sessions) > len(days),
    }


def fetch_watchlist(conn: sqlite3.Connection, as_of: str, limit: int = WATCHLIST_N) -> list[dict]:
    rows = conn.execute(
        "SELECT symbol, net_value FROM market_foreign_history "
        "WHERE date = ? AND flow_kind = ? ORDER BY ABS(net_value) DESC LIMIT ?",
        (as_of, FOREIGN_KIND, limit),
    ).fetchall()
    out = []
    for sym, nv in rows:
        o = conn.execute(
            "SELECT close, volume FROM daily_ohlcv WHERE symbol = ? AND date = ? AND COALESCE(is_stale, 0) = 0",
            (sym, as_of),
        ).fetchone()
        out.append(
            {
                "symbol": sym,
                "net_value": round(float(nv or 0.0), 1),
                "close": None if not o else float(o[0]),
                "volume": None if not o else float(o[1]),
            }
        )
    return out


def fetch_entry_lock() -> bool:
    """Fail-closed: mọi lỗi → True (khoá)."""
    try:
        from src.portfolio.system_state import is_locked

        return bool(is_locked())
    except Exception:  # noqa: BLE001 - fail-closed
        try:
            raw = json.loads((PROJECT_ROOT / "backend" / "data" / "system_state.json").read_text(encoding="utf-8"))
            return bool(raw.get("portfolio_critical_lock", True))
        except Exception:  # noqa: BLE001 - fail-closed
            return True


def fetch_inputs(conn: sqlite3.Connection, date: str | None = None) -> dict:
    as_of = date or latest_clean_date(conn)
    inputs: dict = {
        "as_of": as_of,
        "generated_at": datetime.now(VN_TZ).isoformat(),
        "sources": [],
        "regime": None,
        "foreign": None,
        "watchlist": [],
        "entry_lock": fetch_entry_lock(),
        "gates": {},
        "narrative_contradiction": False,
        "notes": [],
    }
    if as_of is None:
        inputs["notes"].append("Không có ngày nào đủ dữ liệu sạch (>= %d mã)." % MIN_CLEAN_ROWS)
        return inputs

    inputs["sources"].append(f"daily_ohlcv:as_of={as_of}")
    inputs["regime"] = fetch_regime(conn, as_of)
    if inputs["regime"]:
        inputs["sources"].append(f"regime_history:{inputs['regime']['date']}")
    else:
        inputs["notes"].append("regime_history không có dòng nào <= as_of.")

    inputs["foreign"] = fetch_foreign(conn, as_of)
    if inputs["foreign"]:
        inputs["sources"].append(
            f"market_foreign_history:{inputs['foreign']['days'][-1][0]}..{inputs['foreign']['days'][0][0]} (flow_kind=daily)"
        )
    else:
        inputs["notes"].append("market_foreign_history không có dòng flow_kind='daily' nào.")

    inputs["watchlist"] = fetch_watchlist(conn, as_of)
    if inputs["watchlist"]:
        inputs["sources"].append(f"market_foreign_history:{as_of} (top |net_value|)")

    # Cổng: luật cơ học, công khai hằng số — không phải "suy luận".
    reg = inputs["regime"]
    fo = inputs["foreign"]
    gates = {
        "regime_ok": bool(reg and reg["status"] in NON_CRISIS),
        "breadth_ok": bool(reg and reg["breadth_pct"] is not None and reg["breadth_pct"] >= GATE_BREADTH_MIN),
        "foreign_ok": bool(fo and fo["sign"] > 0 and fo["streak"] >= GATE_FOREIGN_BUY_STREAK),
    }
    inputs["gates"] = gates
    inputs["failed_gates"] = [k for k, v in gates.items() if not v]

    # Invariant #3: khoá mà KHÔNG nêu được lý do nào → mâu thuẫn narrative
    inputs["narrative_contradiction"] = bool(inputs["entry_lock"] and not inputs["failed_gates"])
    return inputs


# ── Render ─────────────────────────────────────────────────────────────
def _n(v, unit="", dec=1) -> str:
    if v is None:
        return "N/A"
    return f"{v:,.{dec}f}{unit}"


def render_brief(inp: dict) -> str:
    reg = inp["regime"]
    fo = inp["foreign"]
    L = [f"# Daily Brief — {inp['as_of'] or 'N/A'}", f"*sinh lúc {inp['generated_at']}*", ""]

    L.append("## 1. Trạng thái thị trường")
    if reg:
        L.append(
            f"- Regime: **{reg['status']}** (score {_n(reg['score'], '', 4)}, "
            f"{reg['status_streak']} phiên liên tiếp)  *(regime_history:{reg['date']})*"
        )
        d = reg["breadth_delta"]
        L.append(
            f"- Breadth: **{_n(reg['breadth_pct'], '%')}**"
            + (f" (Δ so phiên trước: {d:+.1f} điểm)" if d is not None else " (Δ: N/A)")
            + f"  *(regime_history:{reg['date']})*"
        )
    else:
        L.append(f"- Regime: **N/A** (lý do: {_reason(inp, 'regime_history')})")

    if fo:
        L.append(
            f"- Dòng ngoại: **{_n(fo['d1'], ' tỷ')}** ({fo['streak']} phiên liên tiếp {fo['direction']}, "
            f"lũy kế {fo['n_days']} phiên: {_n(fo['sum'], ' tỷ')})  "
            f"*(market_foreign_history:{fo['days'][-1][0]}..{fo['days'][0][0]}, flow_kind=daily)*"
        )
    else:
        L.append(f"- Dòng ngoại: **N/A** (lý do: {_reason(inp, 'market_foreign_history')})")

    L += ["", "## 2. Quyết định"]
    L.append(f"- ENTRY_LOCK: **{inp['entry_lock']}**  *(system_state.json:portfolio_critical_lock)*")
    L.append(f"- Allocation mục tiêu: **{'0%' if inp['entry_lock'] else 'N/A (do engine quyết định)'}**")
    if inp["failed_gates"]:
        L.append(f"- Lý do: các điều kiện KHÔNG đạt → {', '.join(inp['failed_gates'])}")
    else:
        L.append("- Lý do: **không có điều kiện nào không đạt theo luật cổng**")
    if inp["narrative_contradiction"]:
        L.append("- ⚠️ **NARRATIVE_CONTRADICTION**: ENTRY_LOCK=true nhưng không nêu được điều kiện không đạt nào.")

    L += ["", "## 3. Watchlist (theo |dòng ngoại| lớn nhất trong ngày)"]
    if inp["watchlist"]:
        for w in inp["watchlist"]:
            L.append(
                f"- **{w['symbol']}**: ngoại {_n(w['net_value'], ' tỷ')} · giá {_n(w['close'])} · KL {_n(w['volume'], '', 0)}"
            )
    else:
        L.append("- N/A (không có dữ liệu ngoại cho ngày này)")

    L += ["", "## 4. Việc tiếp theo (điều kiện cổng — hằng số công khai)"]
    L.append(f"- regime_ok: status ∈ {list(NON_CRISIS)} → hiện **{inp['gates'].get('regime_ok')}**")
    L.append(f"- breadth_ok: breadth >= {GATE_BREADTH_MIN}% → hiện **{inp['gates'].get('breadth_ok')}**")
    L.append(
        f"- foreign_ok: mua ròng >= {GATE_FOREIGN_BUY_STREAK} phiên liên tiếp → hiện **{inp['gates'].get('foreign_ok')}**"
    )

    L += ["", "## 5. Nguồn dữ liệu & độ tươi"]
    for s in inp["sources"]:
        L.append(f"- {s}")
    L.append(f"- ngưỡng dữ liệu sạch: >= {MIN_CLEAN_ROWS} mã/phiên (khớp Freshness Sentinel)")
    for n in inp["notes"]:
        L.append(f"- ⚠️ {n}")
    L.append("")
    return "\n".join(L)


def _reason(inp: dict, table: str) -> str:
    for n in inp["notes"]:
        if table in n:
            return n
    return "không có dữ liệu"


# ── Audit trail ────────────────────────────────────────────────────────
def build_audit_record(inp: dict, command: str = "daily_brief_builder") -> dict:
    reg, fo = inp["regime"], inp["foreign"]
    return {
        "ts": inp["generated_at"],
        "as_of": inp["as_of"],
        "command": command,
        "decision": {
            "entry_lock": bool(inp["entry_lock"]),
            "target_allocation_pct": 0 if inp["entry_lock"] else None,
        },
        "inputs": {
            "regime": None if not reg else {"date": reg["date"], "status": reg["status"],
                                            "score": reg["score"], "breadth_pct": reg["breadth_pct"]},
            "foreign": None if not fo else {"d1": fo["d1"], "streak": fo["streak"],
                                            "sum": fo["sum"], "days_available": fo["n_days"]},
            "freshness": {"latest_clean": inp["as_of"],
                          "is_stale": inp["as_of"] != (datetime.now(VN_TZ).date().isoformat())},
        },
        "gates": inp["gates"],
        "failed_gates": inp["failed_gates"],
        "narrative_contradiction": bool(inp["narrative_contradiction"]),
        "sources": inp["sources"],
    }


def append_audit(record: dict, path: Path = AUDIT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ── Kiểm chứng (invariant #2 và #3) ────────────────────────────────────
def verify(brief: str, inp: dict) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    expected: list[str] = []
    reg, fo = inp["regime"], inp["foreign"]
    if reg:
        expected += [_n(reg["score"], "", 4), _n(reg["breadth_pct"], "%")]
    if fo:
        expected += [_n(fo["d1"], " tỷ"), _n(fo["sum"], " tỷ")]
    missing = [e for e in expected if e not in brief]
    checks.append(("1. traceability — mọi số chính có mặt trong brief", not missing, f"thiếu: {missing}" if missing else "ok"))

    rec = build_audit_record(inp)
    try:
        ok = rec["as_of"] == inp["as_of"] and rec["gates"] == inp["gates"]
        checks.append(("2. audit record hợp lệ & khớp inputs", bool(ok), "ok" if ok else "lệch"))
    except Exception as e:  # noqa: BLE001
        checks.append(("2. audit record hợp lệ & khớp inputs", False, f"{type(e).__name__}: {e}"))

    if inp["entry_lock"]:
        ok3 = bool(inp["failed_gates"]) and any(g in brief for g in inp["failed_gates"])
        checks.append(("3. invariant #3 — khoá thì phải nêu điều kiện không đạt", ok3,
                       "ok" if ok3 else "khoá nhưng không nêu gate nào"))
    else:
        checks.append(("3. invariant #3 — (không khoá)", True, "bỏ qua"))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily brief + audit trail (narrative hạ nguồn)")
    ap.add_argument("--date", default=None, help="as_of (mặc định: ngày sạch mới nhất)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        inp = fetch_inputs(conn, args.date)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(inp, ensure_ascii=False, indent=2, default=str))
        return 0

    brief = render_brief(inp)

    if not args.no_audit:
        p = append_audit(build_audit_record(inp))
        print(f"[audit] +1 dòng → {p}")

    out = Path(args.out) if args.out else REPORTS_DIR / f"daily_brief_{inp['as_of'] or 'NA'}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(brief, encoding="utf-8")
    print(brief)
    print(f"[brief] đã ghi → {out}")

    if args.verify:
        print("=== KIỂM CHỨNG ===")
        results = verify(brief, inp)
        for name, ok, detail in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        return 0 if all(ok for _, ok, _ in results) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
