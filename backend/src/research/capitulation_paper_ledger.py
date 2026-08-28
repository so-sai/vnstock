"""PIT-safe, append-only ledger for measuring capitulation entry lag.

This module is deliberately independent from Governor/allocation code.  A
snapshot is written once; only forward fields are updated after their trading
session maturity.  The public pure functions are also suitable for replay
tests with an in-memory SQLite database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve()
while ROOT != ROOT.parent and not ((ROOT / "AGENTS.md").exists() and (ROOT / "backend").is_dir()):
    ROOT = ROOT.parent
BACKEND = ROOT / "backend"
DATA_DIR = BACKEND / "data"
LEDGER_DB = DATA_DIR / "capitulation_ledger.db"
SCREENER_DB = DATA_DIR / "screener_cache.db"
TABLE = "capitulation_snapshots"
HORIZONS = (5, 20, 30)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
 snapshot_date TEXT PRIMARY KEY, p_cap_raw REAL NOT NULL, p_cap_final REAL NOT NULL,
 regime_score REAL, breadth_pct REAL, structure_status TEXT, stability_pass INTEGER,
 veto TEXT, zone TEXT NOT NULL, provenance TEXT NOT NULL, params_hash TEXT,
 source_ts TEXT NOT NULL, mature_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'VALID',
 ret_h5 REAL, ret_h20 REAL, ret_h30 REAL, mae_h20 REAL, mfe_h20 REAL,
 settled_h5_at TEXT, settled_h20_at TEXT, settled_h30_at TEXT
);
CREATE TABLE IF NOT EXISTS capitulation_forwards (
 snapshot_date TEXT NOT NULL, horizon INTEGER NOT NULL, return_pct REAL,
 close_t REAL, close_th REAL, effective_date_th TEXT, provenance TEXT NOT NULL,
 settled_at TEXT, PRIMARY KEY(snapshot_date, horizon)
);
"""


def zone_bucket(p_cap: float) -> str:
    """Return the research bucket; boundaries are frozen by the spec."""
    value = float(p_cap)
    if not 0.0 <= value <= 1.0:
        raise ValueError("p_cap must be in [0, 1]")
    if value < 0.30:
        return "BELOW_030"
    if value < 0.50:
        return "030_050"
    if value < 0.70:
        return "050_070"
    return "GE_070"


def trading_maturity(calendar: Iterable[str | dt.date], date: str, sessions: int) -> str | None:
    dates = sorted({str(pd.Timestamp(x).date()) for x in calendar})
    try:
        i = dates.index(str(pd.Timestamp(date).date()))
    except ValueError:
        return None
    j = i + int(sessions)
    return dates[j] if j < len(dates) else None


def forward_metrics(prices: pd.DataFrame, snapshot_date: str) -> dict[str, float | None]:
    """Compute close returns and H20 excursion from VNINDEX OHLCV rows.

    ``prices`` must contain date, close and may contain high/low.  Only rows
    strictly after T0 are used for excursions, preventing same-day leakage.
    """
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    df = df.sort_values("date").drop_duplicates("date")
    dates = df["date"].tolist()
    t0 = str(pd.Timestamp(snapshot_date).date())
    if t0 not in dates:
        return {f"ret_h{h}": None for h in HORIZONS} | {"mae_h20": None, "mfe_h20": None}
    i = dates.index(t0)
    close0 = float(df.iloc[i]["close"])
    out: dict[str, float | None] = {}
    for h in HORIZONS:
        out[f"ret_h{h}"] = (float(df.iloc[i + h]["close"]) / close0 - 1.0) * 100 if i + h < len(df) else None
    window = df.iloc[i + 1 : i + 21]
    lows = pd.to_numeric(window.get("low", window["close"]), errors="coerce").dropna()
    highs = pd.to_numeric(window.get("high", window["close"]), errors="coerce").dropna()
    out["mae_h20"] = (float(lows.min()) / close0 - 1.0) * 100 if len(lows) else None
    out["mfe_h20"] = (float(highs.max()) / close0 - 1.0) * 100 if len(highs) else None
    return out


def connect(path: str | Path = LEDGER_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def init_ledger(path: str | Path = LEDGER_DB) -> None:
    conn = connect(path)
    conn.commit()
    conn.close()


def insert_snapshot(snapshot: Mapping[str, object], calendar: Iterable[str], path: str | Path = LEDGER_DB) -> bool:
    required = ("snapshot_date", "p_cap_raw", "p_cap_final", "provenance", "source_ts")
    missing = [k for k in required if snapshot.get(k) is None]
    if missing:
        raise ValueError(f"missing snapshot fields: {', '.join(missing)}")
    date = str(pd.Timestamp(snapshot["snapshot_date"]).date())
    mature = trading_maturity(calendar, date, 30)
    conn = connect(path)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO {TABLE} (snapshot_date,p_cap_raw,p_cap_final,regime_score,breadth_pct,structure_status,stability_pass,veto,zone,provenance,params_hash,source_ts,mature_date,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",  # noqa: E501
        (
            date,
            float(snapshot["p_cap_raw"]),
            float(snapshot["p_cap_final"]),
            snapshot.get("regime_score"),
            snapshot.get("breadth_pct"),
            snapshot.get("structure_status"),
            snapshot.get("stability_pass"),
            snapshot.get("veto"),
            zone_bucket(float(snapshot["p_cap_final"])),
            str(snapshot["provenance"]),
            snapshot.get("params_hash"),
            str(snapshot["source_ts"]),
            mature or "",
            "VALID" if mature else "PENDING_CALENDAR",
        ),
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def settle(path: str | Path, prices: pd.DataFrame, as_of: str | None = None) -> int:
    """Settle only horizons whose effective date is available by ``as_of``."""
    conn = connect(path)
    rows = conn.execute(f"SELECT snapshot_date, provenance FROM {TABLE} ORDER BY snapshot_date").fetchall()
    count = 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    price_dates = set(pd.to_datetime(prices["date"]).dt.date.astype(str))
    limit = str(pd.Timestamp(as_of).date()) if as_of else None
    for date, provenance in rows:
        metrics = forward_metrics(prices, date)
        for h in HORIZONS:
            col = f"ret_h{h}"
            if metrics[col] is None or (
                limit and trading_maturity(price_dates, date, h) and trading_maturity(price_dates, date, h) > limit
            ):
                continue
            stamp_col = f"settled_h{h}_at"
            effective = trading_maturity(price_dates, date, h)
            exists = conn.execute(
                "SELECT 1 FROM capitulation_forwards WHERE snapshot_date=? AND horizon=?", (date, h)
            ).fetchone()
            if exists:
                continue
            dates = pd.to_datetime(prices["date"]).dt.date.astype(str).tolist()
            i = dates.index(date)
            close_t = float(prices.iloc[i]["close"])
            close_th = float(prices.iloc[i + h]["close"])
            conn.execute(
                "INSERT OR IGNORE INTO capitulation_forwards VALUES (?,?,?,?,?,?,?,?)",
                (date, h, metrics[col], close_t, close_th, effective, provenance, now),
            )
            cur = conn.execute(
                f"UPDATE {TABLE} SET {col}=?, {stamp_col}=? WHERE snapshot_date=? AND {stamp_col} IS NULL",
                (metrics[col], now, date),
            )
            count += cur.rowcount
        conn.execute(
            f"UPDATE {TABLE} SET mae_h20=?, mfe_h20=? WHERE snapshot_date=? AND mae_h20 IS NULL",
            (metrics["mae_h20"], metrics["mfe_h20"], date),
        )
    conn.commit()
    conn.close()
    return count


def report(path: str | Path = LEDGER_DB) -> list[dict]:
    conn = connect(path)
    rows = pd.read_sql_query(
        f"SELECT zone, COUNT(*) n, AVG(ret_h5) ret_h5, AVG(ret_h20) ret_h20, AVG(ret_h30) ret_h30, AVG(mae_h20) mae_h20, AVG(mfe_h20) mfe_h20 FROM {TABLE} GROUP BY zone ORDER BY zone",  # noqa: E501
        conn,
    )
    conn.close()
    return rows.where(pd.notna(rows), None).to_dict("records")


def _load_prices(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT date, close, high, low FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date", conn)


def update_from_source(source_db: str | Path = SCREENER_DB, path: str | Path = LEDGER_DB) -> dict:
    """EOD hook: consume only an explicit PIT snapshot source, then settle.

    The optional ``capitulation_eod_snapshots`` table is written by the EOD
    detector.  If it is absent, no snapshot is invented; existing rows are
    still settled from VNINDEX prices.
    """
    with sqlite3.connect(str(source_db)) as conn:
        prices = _load_prices(conn)
        inserted = 0
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "capitulation_eod_snapshots" in tables:
            rows = conn.execute("SELECT * FROM capitulation_eod_snapshots ORDER BY snapshot_date").fetchall()
            cols = [r[1] for r in conn.execute("PRAGMA table_info(capitulation_eod_snapshots)")]
            for row in rows:
                inserted += int(insert_snapshot(dict(zip(cols, row)), prices["date"], path))
    return {"inserted": inserted, "settled": settle(path, prices)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capitulation Forward Paper Ledger")
    parser.add_argument("action", nargs="?", choices=("init", "update", "report"), default="update")
    parser.add_argument("--db", type=Path, default=LEDGER_DB)
    parser.add_argument("--snapshot-json", type=Path, help="PIT-clean JSON list of snapshots")
    args = parser.parse_args()
    init_ledger(args.db)
    if args.action == "init":
        return
    with sqlite3.connect(str(SCREENER_DB)) as src:
        prices = _load_prices(src)
        calendar = prices["date"].tolist()
        if args.snapshot_json:
            for snap in json.loads(args.snapshot_json.read_text(encoding="utf-8")):
                insert_snapshot(snap, calendar, args.db)
    if args.action == "update":
        print({"settled": settle(args.db, prices)})
    else:
        print(json.dumps(report(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
