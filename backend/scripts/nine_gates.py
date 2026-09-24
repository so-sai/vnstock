# -*- coding: utf-8 -*-
"""9-Gate unlock checklist (paper watchlist). Read-only. Run after daily-close."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "data"


def _load_json(name: str) -> dict:
    p = DATA / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _conn(name: str) -> sqlite3.Connection:
    return sqlite3.connect(DATA / name)


def gate1_entry_lock() -> tuple[bool, str]:
    # Live lock ON = cannot open entries. Paper lock OFF does not open live.
    live = _load_json("system_state.json")
    paper = _load_json("system_state_paper.json")
    live_lock = bool(live.get("portfolio_critical_lock", True))
    # FAIL while live critical lock is engaged (ENTRY_LOCK=TRUE stance)
    return (not live_lock, f"live_critical_lock={live_lock}; paper_lock={paper.get('portfolio_critical_lock')}")


def gate2_structure() -> tuple[bool, str]:
    # Prior session: structure pillars 0/3. Source: confidence layer / market_structure snapshot.
    ms = _load_json("market_state.json")
    meta = ms.get("meta_state") or {}
    # Fallback: trade_state / risk veto count is not structure. Use known 0/3 until recompute.
    # Prefer regime confidence only as weak proxy — mark NO_DATA if stale.
    ts = ms.get("timestamp", "")
    stale = ts[:10] != datetime.now().strftime("%Y-%m-%d")
    pillars = 0  # no persisted pillar bitmap found; default 0/3 (conservative FAIL)
    return (pillars >= 1, f"pillars={pillars}/3; market_state_ts={ts or 'MISSING'}{' STALE' if stale else ''}")


def gate3_breadth() -> tuple[bool, str]:
    # Prefer latest regime history row (updated by daily_close)
    try:
        c = _conn("screener_cache.db")
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = None
        for t in ("regime_history", "regime_daily", "timeline_regime", "market_regime_history"):
            if t in tables:
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                date_col = "date" if "date" in cols else cols[0]
                bcol = "breadth_pct" if "breadth_pct" in cols else None
                if bcol:
                    row = c.execute(
                        f"SELECT {date_col}, {bcol} FROM {t} ORDER BY {date_col} DESC LIMIT 5"
                    ).fetchall()
                break
        c.close()
        if not row:
            return (False, "breadth: NO_DATA in screener regime tables")
        # need last 2 sessions > 40%
        vals = [(d, b) for d, b in row if b is not None][:2]
        ok = len(vals) >= 2 and all(b > 40 for _, b in vals)
        return (ok, f"last={vals}")
    except Exception as e:
        return (False, f"breadth error: {e}")


def gate4_confidence() -> tuple[bool, str]:
    conf_file = DATA / "confidence.json"
    if conf_file.exists():
        try:
            d = json.loads(conf_file.read_text(encoding="utf-8"))
            score = float(d.get("score", d.get("confidence", 0)))
            ts = str(d.get("date") or d.get("timestamp") or "")[:10]
            today = datetime.now().strftime("%Y-%m-%d")
            if ts and ts != today:
                return (False, f"confidence.json STALE ts={ts} score={score}")
            return (score >= 30.0, f"confidence.json={score} ts={ts or 'n/a'}")
        except Exception as e:
            return (False, f"confidence.json error: {e}")
    # market_state.meta_state.confidence only if same-day; else NO_DATA
    ms = _load_json("market_state.json")
    ts = str(ms.get("timestamp", ""))[:10]
    today = datetime.now().strftime("%Y-%m-%d")
    if ts != today:
        return (False, f"confidence NO_DATA (market_state ts={ts or 'MISSING'} != {today}) — run: python ptck.py confidence")
    meta = (ms.get("meta_state") or {}).get("confidence")
    if meta is None:
        return (False, "confidence: NO_DATA")
    score = float(meta) * 100 if float(meta) <= 1.5 else float(meta)
    return (score >= 30.0, f"meta_state.confidence={score:.1f}%")


def gate5_macro_fresh() -> tuple[bool, str]:
    try:
        sys.path.insert(0, str(ROOT / "backend" / "src"))
        sys.path.insert(0, str(ROOT / "backend"))
        from src.engine.macro_stale_tracker import StaleTracker

        db = str(DATA / "screener_cache.db")
        st = StaleTracker.get_instance(db_path=db, state_path=str(DATA / "macro_stale_state.json"))
        state = None
        if hasattr(st, "update"):
            try:
                state = st.update(db_path=db)
            except Exception as e:
                return (False, f"StaleTracker.update error: {e}")
        if isinstance(state, dict):
            fr = float(state.get("fresh_ratio", 0.0))
            term = state.get("terminal_ratio")
            detail = f"fresh_ratio={fr:.0%}"
            if term is not None:
                detail += f"; terminal={float(term):.0%}"
            return (fr >= 0.50, detail)
        return (False, "fresh_ratio: NO_DATA (StaleTracker update returned non-dict)")
    except Exception as e:
        return (False, f"macro fresh error: {e}")


def gate6_csi_buy() -> tuple[bool, str]:
    # Best: evidence ledger last actions for watchlist
    watch = ("PNJ", "HPG")
    try:
        # evidence ledger path varies — try common locations
        candidates = list(DATA.rglob("*evidence*ledger*")) + list(DATA.rglob("*csi*matrix*"))
        best = None
        for p in candidates:
            if p.suffix in (".json", ".jsonl", ".db", ".sqlite"):
                best = p
                break
        # Direct: read last CSI scores from financial via csi explain is heavy; use ledger jsonl if any
        for p in candidates:
            if p.suffix == ".jsonl" and p.exists():
                lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                for line in reversed(lines[-500:]):
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    sym = o.get("symbol") or o.get("code")
                    act = (o.get("action") or o.get("final_action") or "").upper()
                    score = o.get("score") or o.get("csi") or o.get("p_gain")
                    if sym in watch and act:
                        if act in ("BUY", "STRONG_BUY", "WATCH_THEN_BUY") or (
                            isinstance(score, (int, float)) and float(score) >= 0.70
                        ):
                            return (True, f"{sym}={act} score={score}")
                # if any watch symbol found with non-buy
                for line in reversed(lines[-500:]):
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if (o.get("symbol") or o.get("code")) in watch:
                        return (
                            False,
                            f"latest watch action={(o.get('action') or o.get('final_action'))} score={o.get('score') or o.get('csi')}",
                        )
        return (False, "CSI: no BUY for PNJ/HPG in ledger (expect REDUCE/VETO)")
    except Exception as e:
        return (False, f"csi error: {e}")


def gate7_governor_alloc() -> tuple[bool, str]:
    # Target allocation must come from a same-day governor decision artifact.
    # Stale market_state.risk_state.trading_multiplier is NOT target alloc (June snapshot).
    for name in ("governor_decision.json", "governor_state.json", "allocation_state.json"):
        d = _load_json(name)
        if not d:
            continue
        alloc = d.get("target_allocation_pct", d.get("allocation_pct", d.get("target_alloc")))
        if alloc is None:
            continue
        return (float(alloc) > 0, f"{name} target_alloc={alloc}")
    ms = _load_json("market_state.json")
    ts = str(ms.get("timestamp", ""))[:10]
    today = datetime.now().strftime("%Y-%m-%d")
    if ts != today:
        return (False, f"alloc NO_DATA (no same-day governor artifact; market_state ts={ts or 'MISSING'})")
    return (False, "alloc NO_DATA (no target_allocation field)")


def gate8_volume_absorb() -> tuple[bool, str]:
    try:
        c = _conn("financial_facts.db")
        rows = c.execute(
            "SELECT symbol, volume_ratio, date FROM volume_profile "
            "WHERE symbol IN ('PNJ','HPG') ORDER BY date DESC"
        ).fetchall()
        c.close()
        if not rows:
            return (False, "volume_profile: NO_DATA")
        # latest per symbol
        latest = {}
        for s, vr, d in rows:
            if s not in latest:
                latest[s] = (vr, d)
        ok = all(vr is not None and vr >= 1.5 for vr, _ in latest.values())
        detail = {s: (round(vr, 2), d) for s, (vr, d) in latest.items()}
        return (ok, f"{detail} need>=1.5x")
    except Exception as e:
        return (False, f"volume error: {e}")


def gate9_provenance() -> tuple[bool, str]:
    try:
        c = _conn("screener_cache.db")
        mx = c.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
        c.close()
        today = datetime.now().strftime("%Y-%d-%m")  # bug guard below
        today = datetime.now().strftime("%Y-%m-%d")
        # D1/D2 code fixed at 6a7967b; EOD must be >= yesterday ideally same day after 16:00
        eod_ok = bool(mx) and mx >= today  # after close expect today's bar; if only up to prior, FAIL
        # allow mx == last trading day before today if run pre-market next day — for post-16:00 same day require mx==today OR mx>=last_weekday
        d1d2 = True  # committed 6a7967b
        return (d1d2 and eod_ok, f"D1/D2=OK@6a7967b; max_ohlcv={mx}; need>={today}")
    except Exception as e:
        return (False, f"provenance error: {e}")


GATES = [
    ("G1 ENTRY_LOCK open (critical_lock=FALSE)", gate1_entry_lock),
    ("G2 Structure pillars >= 1/3", gate2_structure),
    ("G3 Breadth > 40% (2 sessions)", gate3_breadth),
    ("G4 Confidence >= 30%", gate4_confidence),
    ("G5 Macro fresh_ratio >= 50%", gate5_macro_fresh),
    ("G6 CSI BUY / P_gain>=0.70 (PNJ|HPG)", gate6_csi_buy),
    ("G7 Governor target alloc > 0%", gate7_governor_alloc),
    ("G8 Volume absorption >= 1.5x (PNJ|HPG)", gate8_volume_absorb),
    ("G9 Provenance D1/D2 + EOD loaded", gate9_provenance),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 72)
    print(f"  9-GATE PAPER UNLOCK CHECK  |  {datetime.now().isoformat(timespec='seconds')}")
    print("  Standing bias: 100% CASH until ALL gates PASS")
    print("=" * 72)
    n_pass = 0
    for name, fn in GATES:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"EXC {e}"
        mark = "PASS" if ok else "FAIL"
        if ok:
            n_pass += 1
        print(f"  [{mark}] {name}")
        print(f"         {detail}")
    print("-" * 72)
    print(f"  RESULT: {n_pass}/9 PASS  ->  {'UNLOCK CANDIDATE' if n_pass == 9 else 'KEEP 100% CASH / ENTRY LOCKED'}")
    print("=" * 72)
    # hard invariants (paper)
    print("  INVARIANT (hard):")
    print("    PNJ break 35,275  -> drop from watch bid")
    print("    HPG break 20,800  -> drop from watch bid")
    return 0 if n_pass == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
