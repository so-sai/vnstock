"""RE-PULL macro_history: gia tri tranh chap + dong source NULL (Muc 4, 2026-09-28).

Pham vi (duyet truoc do, xem ingestion-ledger.jsonl E9 + phan quyet 5 muc):
  - Scope A: dong source NULL/'' trong macro_history (7.807 dong luc khao sat).
  - Scope B: 1.687 khoa (date, variable) co GIA TRI KHAC NHAU trong
    macro_history_bak_e9 (backup truoc dedupe E9) -> gia tri giu lai la
    MAX(rowid) tuong doi tu quyet, khong co nguon chung minh.

Nguyen tac (Zero-Hallucination):
  - Chi ghi de gia tri khi co PROVIDER SONG tra ve cung (date, variable).
  - Provider chi lay tu mapping DA CO trong repo:
      * yahoo  -> daily_updater.MACRO_TICKERS (mapping goc cua chinh writer v1)
      * fred   -> world_sensor.FRED_SERIES_MAP + DFEDTARU_SERIES (zero-auth CSV)
  - Moi bien truoc khi ghi de phai dat agreement >= --min-agree tren
    >= --min-overlap diem chong (bi loi sai ticker/sai don vi -> MISMATCH, khong ghi).
  - Khong co provider -> giu nguyen, ghi NO_PROVIDER (khong doan nguon).
  - Tat ca thay doi deu vao bang audit macro_history_repull_audit + backup
    macro_history_bak_repull -> rollback duoc.

Chay:
    python backend/scripts/repull_macro_series.py             # dry-run (mac dinh)
    python backend/scripts/repull_macro_series.py --apply     # thuc thi
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "screener_cache.db"
AUDIT_TABLE = "macro_history_repull_audit"
BACKUP_TABLE = "macro_history_bak_repull"

# Bien su dung provider FRED (ten repo) -> series_id FRED doc trong world_sensor.
FRED_SERIES_MAP = {
    "FED_TARGET_RATE": "DFEDTARU",
    "USD_INDEX": "DTWEXBGS",
    "RESERVES_TR": "WRESBAL",
    "QT_BALANCE_TR": "WALCL",
    "US10Y_YIELD": "DGS10",
}
FRED_GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FRED_TIMEOUT = 45.0

STATUS_OVERWRITTEN = "OVERWRITTEN"
STATUS_STAMPED = "SOURCE_STAMPED"
STATUS_MATCHED = "MATCHED"
STATUS_NO_PROVIDER = "NO_PROVIDER"
STATUS_NO_DATA = "NO_PROVIDER_DATA"
STATUS_MISMATCH = "MISMATCH"


def build_scope(con: sqlite3.Connection) -> list[tuple[str, str, float | None, str | None]]:
    """Tra ve (variable, date, current_value, current_source) thuoc 2 scope A + B."""
    rows: dict[tuple[str, str], tuple[float | None, str | None]] = {}
    for variable, date, value, source in con.execute(
        "SELECT variable, date, value, source FROM macro_history WHERE source IS NULL OR source = ''"
    ):
        rows[(variable, date)] = (value, source)
    has_bak = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='macro_history_bak_e9'").fetchone()[
        0
    ]
    if has_bak:
        for variable, date in con.execute(
            "SELECT variable, date FROM macro_history_bak_e9 GROUP BY variable, date HAVING COUNT(DISTINCT value) > 1"
        ):
            if (variable, date) not in rows:
                cur = con.execute(
                    "SELECT value, source FROM macro_history WHERE variable=? AND date=? ORDER BY rowid DESC LIMIT 1",
                    (variable, date),
                ).fetchone()
                if cur is not None:
                    rows[(variable, date)] = (cur[0], cur[1])
    return [(v, d, val, src) for (v, d), (val, src) in sorted(rows.items())]


def yahoo_symbol_map() -> dict[str, str]:
    """Mapping bien -> ticker tu writer goc (daily_updater)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.daily_updater import MACRO_TICKERS

    return dict(MACRO_TICKERS)


def fetch_yahoo(symbols: dict[str, str], start: str, end: str) -> dict[str, dict[str, float]]:
    """Batch download Close theo ticker -> {variable: {date: value}}."""
    if not symbols:
        return {}
    import yfinance as yf

    end_inclusive = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    out: dict[str, dict[str, float]] = {var: {} for var in symbols}
    try:
        batch = yf.download(
            list(symbols.values()),
            start=start,
            end=end_inclusive,
            interval="1d",
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001 - provider resilience: bao cao ro, khong ghi gia tri ao
        print(f"  [yahoo] batch download loi: {type(exc).__name__}: {exc}")
        return out
    if batch is None or batch.empty:
        print("  [yahoo] batch rong")
        return out
    close = batch["Close"] if "Close" in getattr(batch, "columns", []) else batch
    for variable, ticker in symbols.items():
        series = None
        if hasattr(close, "columns"):
            try:
                series = close[ticker]
            except KeyError, IndexError, TypeError:
                if len(close.columns) == 1:
                    series = close.iloc[:, 0]
        else:
            series = close
        if series is None:
            print(f"  [yahoo] {variable} ({ticker}) khong co trong ket qua batch")
            continue
        for idx, val in series.items():
            try:
                num = float(val)
            except TypeError, ValueError:
                continue
            if num == num:  # khong phai NaN
                out[variable][idx.strftime("%Y-%m-%d")] = num
    return out


def fetch_fred(series_map: dict[str, str]) -> dict[str, dict[str, float]]:
    """CSV endpoint zero-auth (world_sensor.FRED_GRAPH_CSV_URL) -> {variable: {date: value}}."""
    import requests

    out: dict[str, dict[str, float]] = {var: {} for var in series_map}
    if not series_map:
        return out
    items = list(series_map.items())
    # Probe 1 series truoc: FRED bi chan/cham thi khong dotg 5*timeout.
    for attempt, (variable, series_id) in enumerate(items):
        timeout = 15.0 if attempt == 0 else FRED_TIMEOUT
        try:
            resp = requests.get(
                FRED_GRAPH_CSV_URL.format(series_id=series_id),
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - provider unreachable -> NO_PROVIDER, khong doan
            print(f"  [fred] {variable} ({series_id}) loi: {type(exc).__name__}: {exc}")
            if attempt == 0:
                print("  [fred] probe that bai -> bo qua toan bo provider FRED")
                return out
            continue
        rows = list(csv.reader(io.StringIO(resp.text)))
        for rec in rows[1:]:
            if len(rec) >= 2 and rec[1] not in (".", ""):
                try:
                    out[variable][rec[0]] = float(rec[1])
                except ValueError:
                    continue
        time.sleep(0.5)
    return out


def agreement_rate(db_values: list[float], provider: dict[str, float], rel_tol: float) -> tuple[int, int, float]:
    """(overlap, agree, rate) tren cac diem trung ngay voi provider."""
    overlap = agree = 0
    for date, val in db_values:
        if date not in provider or val is None:
            continue
        overlap += 1
        pv = provider[date]
        if abs(pv - val) <= rel_tol * max(abs(val), 1e-12):
            agree += 1
    rate = (agree / overlap) if overlap else 0.0
    return overlap, agree, rate


def decide_plan(
    scope: list[tuple[str, str, float | None, str | None]],
    provider_series: dict[str, dict[str, float]],
    provider_tags: dict[str, str],
    db_history: dict[str, list[tuple[str, float]]],
    min_agree: float,
    min_overlap: int,
    rel_tol: float,
    diff_tol: float,
    trust_low_overlap: set[str],
) -> list[dict]:
    """Quyet dinh status moi khoa. Khong provider / mismatch -> khong ghi."""
    checks: dict[str, tuple[int, int, float]] = {}
    for variable in {v for v, *_ in scope}:
        series = provider_series.get(variable)
        if series is None:
            checks[variable] = (0, 0, 0.0)
            continue
        checks[variable] = agreement_rate(db_history.get(variable, []), series, rel_tol)

    plan: list[dict] = []
    for variable, date, cur_val, cur_src in scope:
        series = provider_series.get(variable)
        base = {
            "variable": variable,
            "date": date,
            "old_value": cur_val,
            "old_source": cur_src,
            "provider": provider_tags.get(variable, ""),
            "ticker": "",
        }
        if series is None:
            plan.append({**base, "status": STATUS_NO_PROVIDER, "new_value": None, "rel_diff": None})
            continue
        overlap, agree, rate = checks[variable]
        verified = overlap >= min_overlap and rate >= min_agree
        low_overlap_ok = overlap < min_overlap and variable in trust_low_overlap
        if not (verified or low_overlap_ok):
            plan.append({**base, "status": STATUS_MISMATCH, "new_value": None, "rel_diff": rate})
            continue
        if date not in series:
            plan.append({**base, "status": STATUS_NO_DATA, "new_value": None, "rel_diff": None})
            continue
        new_val = series[date]
        if cur_val is None or abs(new_val - cur_val) > diff_tol * max(abs(cur_val), 1e-12):
            rel = None if cur_val in (None, 0) else abs(new_val - cur_val) / abs(cur_val)
            plan.append({**base, "status": STATUS_OVERWRITTEN, "new_value": new_val, "rel_diff": rel})
        elif cur_src is None or cur_src == "":
            plan.append({**base, "status": STATUS_STAMPED, "new_value": new_val, "rel_diff": 0.0})
        else:
            plan.append({**base, "status": STATUS_MATCHED, "new_value": new_val, "rel_diff": 0.0})
    return plan


def ensure_audit_tables(con: sqlite3.Connection) -> None:
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            run_id TEXT, variable TEXT, date TEXT,
            old_value REAL, old_source TEXT, new_value REAL, new_source TEXT,
            provider TEXT, ticker TEXT, status TEXT, rel_diff REAL, applied_at TEXT
        )"""
    )
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS
            SELECT * FROM macro_history WHERE 0"""
    )


def backup_affected(con: sqlite3.Connection, plan: list[dict]) -> int:
    """Snapshot cac dong se bi sua vao bang backup (chi tao 1 lan)."""
    existed = con.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0]
    if existed:
        return existed
    rows = con.execute("SELECT date, variable, value, is_stale, source FROM macro_history").fetchall()
    wanted = {(p["variable"], p["date"]) for p in plan if p["status"] in (STATUS_OVERWRITTEN, STATUS_STAMPED)}
    affected = [r for r in rows if (r[1], r[0]) in wanted]
    con.executemany(
        f"INSERT INTO {BACKUP_TABLE} (date, variable, value, is_stale, source) VALUES (?,?,?,?,?)",
        affected,
    )
    return len(affected)


def apply_plan(con: sqlite3.Connection, plan: list[dict], run_id: str) -> dict[str, int]:
    ensure_audit_tables(con)
    backup_affected(con, plan)
    now = datetime.now().isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for item in plan:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
        con.execute(
            f"INSERT INTO {AUDIT_TABLE} (run_id, variable, date, old_value, old_source, "
            "new_value, new_source, provider, ticker, status, rel_diff, applied_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                item["variable"],
                item["date"],
                item["old_value"],
                item["old_source"],
                item["new_value"],
                item["provider"],
                item["provider"],
                item["ticker"],
                status,
                item["rel_diff"],
                now,
            ),
        )
        if status in (STATUS_OVERWRITTEN, STATUS_STAMPED):
            con.execute(
                "UPDATE macro_history SET value=?, source=?, is_stale=0 WHERE variable=? AND date=?",
                (item["new_value"], item["provider"], item["variable"], item["date"]),
            )
    con.commit()
    return counts


def print_report(plan: list[dict]) -> None:
    by_var: dict[str, dict[str, int]] = {}
    for item in plan:
        by_var.setdefault(item["variable"], {})
        by_var[item["variable"]][item["status"]] = by_var[item["variable"]].get(item["status"], 0) + 1
    totals: dict[str, int] = {}
    for item in plan:
        totals[item["status"]] = totals.get(item["status"], 0) + 1
    print("\n== TONG KET (theo trang thai) ==")
    for status in sorted(totals, key=lambda s: -totals[s]):
        print(f"  {status:20s} {totals[status]:,}")
    print(f"  {'TONG KHOA':20s} {len(plan):,}")
    print("\n== THEO BIEN ==")
    for variable in sorted(by_var):
        stats = " ".join(f"{k}={v}" for k, v in sorted(by_var[variable].items()))
        print(f"  {variable:22s} {stats}")


def main(argv: list[str] | None = None, fetchers: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="thuc thi (mac dinh la dry-run)")
    ap.add_argument("--vars", type=str, default="", help="chi xu ly cac bien (phan dau, cach dau phay)")
    ap.add_argument("--min-agree", type=float, default=0.9)
    ap.add_argument("--min-overlap", type=int, default=10)
    ap.add_argument("--rel-tol", type=float, default=0.005, help="nguong dong y agreement")
    ap.add_argument("--diff-tol", type=float, default=1e-9, help="nguong coi la khac gia tri -> ghi de")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"khong tim thay DB: {args.db}")
        return 2

    con = sqlite3.connect(f"file:{args.db}?mode={'rw' if args.apply else 'ro'}", uri=True)
    try:
        scope = build_scope(con)
        if args.vars:
            wanted = {v.strip() for v in args.vars.split(",") if v.strip()}
            scope = [row for row in scope if row[0] in wanted]
        if not scope:
            print("khong co khoa nao thuoc scope.")
            return 0

        variables = sorted({v for v, *_ in scope})
        dates = [d for _, d, *_ in scope]
        start, end = min(dates), max(dates)
        print(f"scope: {len(scope):,} khoa / {len(variables)} bien / {start}..{end}")

        yahoo_map = {v: t for v, t in yahoo_symbol_map().items() if v in variables}
        fred_map = {v: s for v, s in FRED_SERIES_MAP.items() if v in variables}

        fetchers = fetchers or {}
        yahoo_fetch = fetchers.get("yahoo")
        fred_fetch = fetchers.get("fred")
        print(f"provider: yahoo={len(yahoo_map)} bien, fred={len(fred_map)} bien")

        provider_series: dict[str, dict[str, float]] = {}
        provider_tags: dict[str, str] = {}
        if yahoo_map:
            got = yahoo_fetch(yahoo_map, start, end) if yahoo_fetch else fetch_yahoo(yahoo_map, start, end)
            for variable, series in got.items():
                if series:
                    provider_series[variable] = series
                    provider_tags[variable] = "yahoo"
        if fred_map:
            got = fred_fetch(fred_map) if fred_fetch else fetch_fred(fred_map)
            for variable, series in got.items():
                if series:
                    provider_series[variable] = series
                    provider_tags[variable] = "fred"

        db_history: dict[str, list[tuple[str, float]]] = {}
        for variable in provider_series:
            db_history[variable] = [
                (d, v)
                for d, v in con.execute(
                    "SELECT date, value FROM macro_history WHERE variable=? AND value IS NOT NULL",
                    (variable,),
                )
            ]

        plan = decide_plan(
            scope,
            provider_series,
            provider_tags,
            db_history,
            min_agree=args.min_agree,
            min_overlap=args.min_overlap,
            rel_tol=args.rel_tol,
            diff_tol=args.diff_tol,
            trust_low_overlap=set(yahoo_map),
        )
        symbols = {**yahoo_map, **{v: s for v, s in fred_map.items()}}
        for item in plan:
            item["ticker"] = symbols.get(item["variable"], "")
        print_report(plan)

        if not args.apply:
            print("\nDRY-RUN -> khong thay doi gi. Chay lai voi --apply de thuc thi.")
            return 0

        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        counts = apply_plan(con, plan, run_id)
        written = counts.get(STATUS_OVERWRITTEN, 0) + counts.get(STATUS_STAMPED, 0)
        print(f"\n== APPLY run_id={run_id} ==\n  da ghi: {written:,} (audit: {AUDIT_TABLE})")
        remaining = con.execute("SELECT COUNT(*) FROM macro_history WHERE source IS NULL OR source=''").fetchone()[0]
        print(f"  source NULL con lai: {remaining:,}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
