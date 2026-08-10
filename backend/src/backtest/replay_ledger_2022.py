"""replay_ledger_2022.py — Decision System Replay: Governor assess() per historical day.

Mục tiêu (Phase 1 audit):
  Tái tạo decision pipeline (Evidence → Clustering → Budget Policy → Ledger)
  trên lịch sử bằng cách gọi Governor.assess(symbol, target_date) cho từng
  phiên, đảm bảo PIT (no look-ahead). Kết quả ghi vào decision_ledger với
  date=target_date để audit slot accounting + opportunity cost.

Bottleneck đã xử lý:
  1. set_context_for_date(): macro/transmission/sector theo target_date (fix PIT)
  2. _compute_sector_context cache theo (sector, date): giảm ~458× macro recompute
  3. lru_cache module-level cho macro_history query: reuse giữa ngày/sector

Multiprocessing: chia theo ngày, mỗi process xử lý 1 ngày (universe ADV>=50k).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ── Sentinel path (giống company_state.py) ──
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = BACKEND_DIR / "data"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SCREENER_DB = DATA_DIR / "screener_cache.db"
LEDGER_DB = DATA_DIR / "calibration.db"
ADV_MIN = 50000
MAX_WORKERS_DEFAULT = 12


def get_trading_days(start: str, end: str, db_path=None) -> list[str]:
    conn = sqlite3.connect(str(db_path or SCREENER_DB))
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_universe_adv20(date: str, db_path=None) -> list[str]:
    """Universe tại 1 ngày: các symbol có ADV_20D >= ADV_MIN.

    ADV tính bằng rolling 20 phiên trung bình (không look-ahead).
    """
    conn = sqlite3.connect(str(db_path or SCREENER_DB))
    try:
        rows = conn.execute(
            """
            WITH recent AS (
                SELECT symbol, date, volume,
                    AVG(volume) OVER (
                        PARTITION BY symbol ORDER BY date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) AS adv20
                FROM daily_ohlcv
                WHERE date <= ?
            )
            SELECT symbol FROM recent
            WHERE date = ? AND adv20 >= ?
              AND symbol NOT LIKE '%INDEX%'
              AND symbol NOT LIKE 'VN%30%'
              AND symbol NOT LIKE 'VN%100%'
              AND symbol NOT IN ('VNXALL', 'VNALLSHARE', 'HNXUPCOM', 'HOSE')
            GROUP BY symbol
            """,
            (date, date, ADV_MIN),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def run_day(date: str, universe: list[str], ledger_db: str, budget: int) -> dict:
    """Worker: replay 1 ngày — set_context_for_date + assess từng symbol.

    Dùng SQLite IN-MEMORY (:memory:) — KHÔNG tạo file .db tạm trên đĩa.
    Loại bỏ hoàn toàn Disk I/O contention / File Lock Thrashing khi nhiều
    worker chạy song song. Trả về toàn bộ decision rows qua future result
    (In-Memory Queue), main process ghi 1 lần executemany().

    Budget: mỗi ngày mặc định còn đủ 20 slot (budget tracking xuyên ngày do
    bước truncation trong merge_day_dbs xử lý).
    """
    from calibration.evidence_ledger import SCHEMA_SQL
    from src.governor.company_state import BayesianGovernor

    # In-memory SQLite — không ghi đĩa
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    governor = BayesianGovernor()
    governor.set_context_for_date(date)

    try:
        for sym in universe:
            try:
                governor.assess(
                    sym,
                    target_date=date,
                    replay_mode=True,
                    ledger_conn=conn,
                )
            except AttributeError, TypeError, ValueError, sqlite3.Error, KeyError:
                continue
        conn.commit()
    finally:
        pass

    # Đọc toàn bộ rows của ngày từ in-memory
    rows = conn.execute(
        "SELECT * FROM decision_ledger WHERE date = ? ORDER BY decision_id",
        (date,),
    ).fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(decision_ledger)").fetchall()]
    day_rows = [tuple(r) for r in rows]
    conn.close()

    n_execute = sum(1 for r in day_rows if r[cols.index("decision")] == "EXECUTE")
    stats = {
        "date": date,
        "n_symbols": len(universe),
        "n_recorded": len(day_rows),
        "n_execute": n_execute,
        "n_watch": sum(1 for r in day_rows if r[cols.index("decision")] == "WATCH"),
        "n_reject": sum(1 for r in day_rows if r[cols.index("decision")] == "REJECT"),
        "_cols": cols,
        "_rows": day_rows,
    }
    return stats


def merge_day_dbs(day_dbs: list[str], target_db: str, budget: int = 20) -> None:
    """Merge per-day temp DBs into target ledger DB.

    After merge, applies budget constraint: keeps only first N EXECUTE per year
    (by date order), converting excess EXECUTE to WATCH.
    """
    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    # Get max existing decision_id to avoid conflicts
    max_id = conn.execute("SELECT COALESCE(MAX(decision_id), 0) FROM decision_ledger").fetchone()[0]

    for db_path in day_dbs:
        if not os.path.exists(db_path):
            continue
        try:
            day_conn = sqlite3.connect(db_path)
            rows = day_conn.execute("SELECT * FROM decision_ledger").fetchall()
            cols = [d[1] for d in day_conn.execute("PRAGMA table_info(decision_ledger)").fetchall()]
            day_conn.close()
            if rows:
                # Re-number decision_id to avoid conflicts
                id_col_idx = cols.index("decision_id") if "decision_id" in cols else 0
                renumbered = []
                for row in rows:
                    row_list = list(row)
                    max_id += 1
                    row_list[id_col_idx] = max_id
                    renumbered.append(tuple(row_list))

                placeholders = ",".join(["?"] * len(cols))
                col_names = ",".join(cols)
                conn.executemany(
                    f"INSERT INTO decision_ledger ({col_names}) VALUES ({placeholders})",
                    renumbered,
                )
            os.remove(db_path)
        except (sqlite3.Error, OSError, TypeError, ValueError, KeyError) as e:
            print(f"  Warning: failed to merge {os.path.basename(db_path)}: {e}")
            continue
    conn.commit()

    # Apply budget constraint: keep only first `budget` EXECUTE per year
    for year in range(2018, 2030):
        exe_rows = conn.execute(
            "SELECT rowid, date FROM decision_ledger WHERE decision='EXECUTE' AND decision_budget_year=? ORDER BY date",
            (year,),
        ).fetchall()
        if len(exe_rows) > budget:
            excess_rowids = [r[0] for r in exe_rows[budget:]]
            placeholders = ",".join(["?"] * len(excess_rowids))
            conn.execute(
                f"UPDATE decision_ledger SET decision='WATCH' WHERE rowid IN ({placeholders})",
                excess_rowids,
            )
            print(f"  Year {year}: {len(exe_rows)} EXECUTE -> kept {budget}, converted {len(excess_rowids)} to WATCH")
    conn.commit()
    conn.close()


def write_ledger_once(all_day_results: list[dict], ledger_db: str, budget: int = 20) -> None:
    """Ghi toàn bộ rows từ In-Memory Queue vào ledger bằng 1 executemany().

    Không có file .db trung gian — data đi thẳng từ worker → main process.
    Sau ghi, áp budget constraint (giữ N EXECUTE đầu tiên theo ngày).
    """
    conn = sqlite3.connect(ledger_db)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    cols = None
    max_id = conn.execute("SELECT COALESCE(MAX(decision_id), 0) FROM decision_ledger").fetchone()[0]
    total_written = 0

    for st in all_day_results:
        day_cols = st.get("_cols")
        day_rows = st.get("_rows", [])
        if not day_rows:
            continue
        if cols is None:
            cols = day_cols
        # Re-number decision_id to avoid PK conflicts
        id_idx = day_cols.index("decision_id")
        renumbered = []
        for row in day_rows:
            row_list = list(row)
            max_id += 1
            row_list[id_idx] = max_id
            renumbered.append(tuple(row_list))
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        conn.executemany(
            f"INSERT INTO decision_ledger ({col_names}) VALUES ({placeholders})",
            renumbered,
        )
        total_written += len(renumbered)

    conn.commit()
    print(f"  Wrote {total_written} rows to {ledger_db} (single executemany, no temp files)")

    # Apply budget constraint: keep only first `budget` EXECUTE per year
    for year in range(2018, 2030):
        exe_rows = conn.execute(
            "SELECT rowid, date FROM decision_ledger WHERE decision='EXECUTE' AND decision_budget_year=? ORDER BY date",
            (year,),
        ).fetchall()
        if len(exe_rows) > budget:
            excess_rowids = [r[0] for r in exe_rows[budget:]]
            placeholders = ",".join(["?"] * len(excess_rowids))
            conn.execute(
                f"UPDATE decision_ledger SET decision='WATCH' WHERE rowid IN ({placeholders})",
                excess_rowids,
            )
            print(f"  Year {year}: {len(exe_rows)} EXECUTE -> kept {budget}, converted {len(excess_rowids)} to WATCH")
    conn.commit()
    conn.close()


def replay(
    start: str,
    end: str,
    ledger_db=None,
    budget: int = 20,
    workers: int = MAX_WORKERS_DEFAULT,
    sample_every: int = 1,
    db_path=None,
) -> dict:
    """Replay toàn bộ window. Returns summary dict."""
    from calibration.evidence_ledger import init_schema

    ledger_db = ledger_db or str(LEDGER_DB)
    init_schema(ledger_db)

    days = get_trading_days(start, end, db_path)
    days = days[::sample_every] if sample_every > 1 else days
    print(f"Trading days: {len(days)} ({start} - {end})")

    # Pre-compute universe per day (single-pass, avoids recompute in workers)
    day_universe = {}
    for d in days:
        day_universe[d] = get_universe_adv20(d, db_path)
    total_calls = sum(len(v) for v in day_universe.values())
    print(f"Total assess calls: {total_calls} (universe ADV>={ADV_MIN})")

    t0 = time.time()
    done = 0
    per_day_stats = []
    day_dbs = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_day, d, day_universe[d], ledger_db, budget): d for d in days}
        for f in as_completed(futures):
            try:
                st = f.result()
                per_day_stats.append(st)
                if "_day_db" in st:
                    day_dbs.append(st["_day_db"])
            except (AttributeError, TypeError, ValueError, sqlite3.Error, KeyError, RuntimeError) as e:
                per_day_stats.append({"date": futures[f], "error": str(e)})
            done += 1
            if done % 25 == 0 or done == len(futures):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  {done}/{len(futures)} days, {rate:.1f} days/s, ETA {(len(futures) - done) / rate:.0f}s", flush=True)

    # In-Memory Queue: ghi toàn bộ rows qua 1 executemany, không có temp file
    if any("_rows" in s for s in per_day_stats):
        write_ledger_once(per_day_stats, ledger_db, budget)

    total = time.time() - t0
    n_sym = sum(s.get("n_symbols", 0) for s in per_day_stats)
    n_rec = sum(s.get("n_recorded", 0) for s in per_day_stats)
    n_exe = sum(s.get("n_execute", 0) for s in per_day_stats)
    n_watch = sum(s.get("n_watch", 0) for s in per_day_stats)
    n_reject = sum(s.get("n_reject", 0) for s in per_day_stats)
    summary = {
        "window": {"start": start, "end": end},
        "n_days": len(days),
        "n_calls": total_calls,
        "n_symbols_processed": n_sym,
        "n_recorded": n_rec,
        "n_execute": n_exe,
        "n_watch": n_watch,
        "n_reject": n_reject,
        "elapsed_seconds": round(total, 1),
        "calls_per_second": round(total_calls / total, 1) if total else 0,
    }
    print("=" * 70)
    print(f"  REPLAY DONE: {total_calls} calls in {total:.1f}s")
    print(f"  EXECUTE={n_exe} WATCH={n_watch} REJECT={n_reject}")
    print("=" * 70)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Decision System Replay → Evidence Ledger")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2022-12-31")
    ap.add_argument("--ledger-db", default=None, help="Ledger DB path (default calibration.db)")
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS_DEFAULT)
    ap.add_argument("--sample-every", type=int, default=1, help="Chỉ chạy mỗi N phiên")
    args = ap.parse_args()
    replay(
        args.start,
        args.end,
        ledger_db=args.ledger_db,
        budget=args.budget,
        workers=args.workers,
        sample_every=args.sample_every,
    )
