"""gold_h2_series.py — H2 (Reserve) data-contract layer cho Gold Forecast Engine.

Vì sao là LAYER MỚI, không phải bảng macro_history mở rộng:

  macro_history / macro_history_v2 dùng MỘT cột `date` vừa là observation vừa
  là availability — hợp lệ cho daily price (DXY/GOLD) nhưng SAI với CB gold
  purchases / ETF flows (monthly, công bố trễ nhiều tuần, có revision/vintage).

  Nếu nhét H2 vào schema cũ → tái tạo đúng loại look-ahead mà equity pipeline
  vừa phát hiện trong valuation (zone_ts / PIT). Đây là data-contract layer mới.

INVARIANT CHÍNH:
      feature usable at t  <=>  publication_date <= t
      KHÔNG dùng observation_date <= t để xác định PIT.

Guardrail:
  1. publication_date NULLABLE nhưng KHÔNG tự suy diễn:
       - nếu nguồn không cung cấp -> NULL -> KHÔNG dùng trong PIT backtest.
       - không lấy ingested_at thay thế.
  2. publication_date >= observation_date (nếu cả hai có giá trị).
  3. Revision/vintage được BẢO TOÀN: mỗi (series, entity, observation_date,
     source, publication_date) là một dòng riêng. Backtest tại t chỉ nhìn thấy
     vintage có publication_date <= t (latest per observation_date).
  4. duplicate (series, entity, observation_date, source) bị chặn bởi UNIQUE
     index (kể cả khi publication_date NULL qua COALESCE).

READ-ONLY đối với macro_history / macro_history_v2 / Governor / Selection Layer.
Module tự tạo bảng trong DB được truyền vào (mặc định KHÔNG đụng screener_cache
trừ khi --db trỏ tới).

Usage (từ project root):
  python -X utf8 backend/src/research/gold_h2_series.py --init --db <path>
  python -X utf8 backend/src/research/gold_h2_series.py --validate --db <path>
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# ── Path hydration (Sentinel v2.1 Anchor) ──────────────────────────────────
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "AGENTS.md").exists() and (PROJECT_ROOT / "backend").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

TABLE_NAME = "gold_h2_series"

SCHEMA_H2 = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    series            TEXT NOT NULL,          -- 'CB_GOLD_PURCHASES' / 'ETF_FLOWS'
    entity            TEXT NOT NULL,          -- country / fund / 'GLOBAL'
    observation_date  TEXT NOT NULL,          -- kỳ dữ liệu (YYYY-MM-DD)
    publication_date  TEXT,                   -- ngày công bố thực tế (nullable)
    value             REAL NOT NULL,
    unit              TEXT NOT NULL,
    source            TEXT NOT NULL,
    provenance        TEXT NOT NULL,
    ingested_at       TEXT DEFAULT (datetime('now')),
    metadata          TEXT,
    CHECK (publication_date IS NULL OR publication_date >= observation_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gold_h2_vintage
    ON {TABLE_NAME} (series, entity, observation_date,
                     COALESCE(publication_date, ''), source);
"""

# Invariants kiểm tra được qua SQL (returned from validate_contract)
INVARIANTS = (
    "publication_date IS NULL OR publication_date >= observation_date",
    "value IS NOT NULL",
    "series != '' AND entity != ''",
)


def init_table(conn: sqlite3.Connection) -> None:
    """Tạo bảng gold_h2_series + unique index (idempotent)."""
    conn.executescript(SCHEMA_H2)
    conn.commit()


def insert_vintage(
    conn: sqlite3.Connection,
    series: str,
    entity: str,
    observation_date: str,
    value: float,
    unit: str,
    source: str,
    provenance: str,
    publication_date: str | None = None,
    metadata: str | None = None,
) -> None:
    """INSERT một vintage. Duplicate theo UNIQUE index bị SQLite reject.

    Nếu (series, entity, observation_date, source, publication_date) đã tồn tại:
    bản ghi được REPLACE (revision của cùng vintage). Không bao giờ overwrite
    vintage khác — mỗi publication_date là một dòng riêng.
    """
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLE_NAME} "
        "(series, entity, observation_date, publication_date, value, unit, "
        " source, provenance, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        (series, entity, observation_date, publication_date, value, unit, source, provenance, metadata),
    )
    conn.commit()


def resolve_pit(conn: sqlite3.Connection, series: str, entity: str, as_of_date: str) -> dict[str, tuple[str, float]]:
    """PIT lookup: state of knowledge tại as_of_date.

    Chỉ các dòng có publication_date IS NOT NULL AND publication_date <= t.
    Với mỗi observation_date lấy LATEST vintage (max publication_date).
    Trả về {observation_date: (publication_date, value)}.

    Không bao giờ dùng ingested_at / observation_date <= t cho PIT.
    """
    rows = conn.execute(
        f"SELECT observation_date, publication_date, value "
        f"FROM {TABLE_NAME} "
        f"WHERE series=? AND entity=? "
        f"  AND publication_date IS NOT NULL AND publication_date <= ? "
        f"ORDER BY observation_date, publication_date",
        (series, entity, as_of_date),
    ).fetchall()
    result: dict[str, tuple[str, float]] = {}
    for obs, pub, val in rows:
        # latest vintage per observation_date — dòng cuối (pub lớn nhất)
        result[obs] = (pub, float(val))
    return result


def latest_value_at(conn: sqlite3.Connection, series: str, entity: str, as_of_date: str) -> tuple[str, float] | None:
    """Giá trị MỚI NHẤT có thể nhìn thấy tại t (cho feature builder).

    Chọn observation_date lớn nhất trong resolve_pit(t).
    Trả về (observation_date, value) hoặc None nếu chưa có dữ liệu công bố.
    """
    pit = resolve_pit(conn, series, entity, as_of_date)
    if not pit:
        return None
    last_obs = max(pit)
    pub, val = pit[last_obs]
    return last_obs, val


def validate_contract(conn: sqlite3.Connection) -> dict:
    """Chạy toàn bộ invariant check. Trả về dict {check: (ok, detail)}."""
    checks: dict[str, tuple[bool, str]] = {}

    # 1. Bảng tồn tại
    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE_NAME,),
    ).fetchone()
    if not tbl:
        return {"table_exists": (False, "bảng chưa được tạo")}
    checks["table_exists"] = (True, "bảng tồn tại")

    # 2. publication_date >= observation_date (SQL CHECK + double-check)
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE publication_date IS NOT NULL AND publication_date < observation_date"
    ).fetchone()[0]
    checks["pub_ge_obs"] = (bad == 0, f"{bad} violation(s)")

    # 3. value / series / entity NOT NULL & NOT EMPTY
    bad = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE value IS NULL OR series='' OR entity=''").fetchone()[0]
    checks["nonempty"] = (bad == 0, f"{bad} violation(s)")

    # 4. source & provenance mandatory
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE source IS NULL OR source='' OR provenance IS NULL OR provenance=''"
    ).fetchone()[0]
    checks["source_provenance"] = (bad == 0, f"{bad} violation(s)")

    # 5. Duplicate (series, entity, obs, source, publication) — chặn từ UNIQUE
    #    index. Vintage ladder (nhiều publication_date cho cùng obs) là HỢP LỆ
    #    (guardrail 3: revision bảo toàn) → group theo COALESCE(pub,'').
    dup = conn.execute(
        f"SELECT series, entity, observation_date, source, "
        f"COALESCE(publication_date, ''), COUNT(*) c "
        f"FROM {TABLE_NAME} "
        f"GROUP BY series, entity, observation_date, source, "
        f"COALESCE(publication_date, '') "
        f"HAVING c > 1"
    ).fetchall()
    checks["no_dup_obs_source"] = (len(dup) == 0, f"{len(dup)} duplicate group(s)")

    # 6. publication_date NULL — đếm để báo cáo (được phép nhưng KHÔNG dùng PIT)
    n_null = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE publication_date IS NULL").fetchone()[0]
    checks["pub_nullable"] = (True, f"{n_null} row(s) NULL (bị loại khỏi PIT backtest)")

    # 7. Không dùng ingested_at làm PIT — chỉ audit (không có cách test trực tiếp,
    #    đảm bảo bằng thiết kế resolver chỉ lọc theo publication_date).
    checks["pit_lookup_by_publication_only"] = (True, "resolver lọc theo publication_date <= t")

    return checks


def _print_checks(checks: dict) -> None:
    for name, (ok, detail) in checks.items():
        tag = "PASS" if ok else "FAIL"
        print(f"    [{tag}] {name:<34s} {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold H2 data-contract layer")
    parser.add_argument("--init", action="store_true", help="tạo bảng gold_h2_series")
    parser.add_argument("--validate", action="store_true", help="chạy invariant checks")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"), help="đường dẫn DB (mặc định backend/data/gold_h2.db)")
    parser.add_argument("--series", "--s", default=None)
    parser.add_argument("--entity", "--e", default=None)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.init:
        init_table(conn)
        print(f"  [OK] Bảng {TABLE_NAME} sẵn sàng tại {args.db}")
    if args.validate:
        checks = validate_contract(conn)
        print(f"\n  CONTRACT VALIDATION — {TABLE_NAME} ({args.db})")
        _print_checks(checks)
        all_ok = all(ok for ok, _ in checks.values())
        print(f"\n  Kết luận: {'PASS' if all_ok else 'FAIL'}")
    if args.series and args.entity and args.as_of:
        pit = resolve_pit(conn, args.series, args.entity, args.as_of)
        print(f"\n  PIT state at {args.as_of} ({args.series} / {args.entity}):")
        if pit:
            for obs in sorted(pit):
                pub, val = pit[obs]
                print(f"    {obs}  pub={pub}  value={val}")
        else:
            print("    (không có dữ liệu công bố tại thời điểm này)")
    conn.close()


if __name__ == "__main__":
    main()
