"""wgc_etf_adapter.py — Adapter ingest WGC Gold ETF flows → gold_h2_series.

Nguồn: JSON API công khai `https://fsapi.gold.org/api/v11/charts/etfv2/revised/`.

PHÁT HIỆN SOURCE (source discovery):
  - File xlsx WGC **private** (cần login WGC account) → KHÔNG dùng.
  - JSON API public: `flows-chart2` / `holdings-chart2` / `archive-tablegroup/all`
    (200 OK, không cần login, ~240KB).
  - API chỉ trả **1 snapshot hiện tại** (asOfDate). KHÔNG có historical vintage
    parameter; Wayback không capture file xlsx ETF.
  - → Chiến lược PIT: ingest snapshot hiện tại làm baseline (publication_date =
    fetch date), rồi **forward crawl** hàng tuần/tháng để tự dựng vintage ladder
    từ giờ trở đi (giống IFS forward).
  - Historical obs 2003-2026 chỉ "biết" từ fetch date → trong backtest chỉ usable
    từ ngày crawl baseline, không có PIT thật cho quá khứ.

SEMANTICS (đã xác minh từ table JSON + methodology):
  - `ETF_DEMAND_TONNES`  = Δ holdings (physical demand, tonnes).
  - `ETF_FLOWS_USD`      = net money flow (USD) — KHÁC demand do FX-hedged funds.
  - Hai series RIÊNG BIỆT. Entity = region (North America/Europe/Asia/Other),
    KHÔNG cộng thành global trong ingestion (derived ở feature layer).

GUARDRAIL PIT:
  - publication_date = ngày fetch thực tế (bên ngoài truyền vào), KHÔNG lấy
    asOfDate (data cutoff) làm pub — tránh look-ahead.
  - Không suy diễn; thiếu dữ liệu → skip (không forward-fill).

Usage (từ backend/):
  python -X utf8 src/research/wgc_etf_adapter.py --fetch --db data/gold_h2.db
  python -X utf8 src/research/wgc_etf_adapter.py --ingest --db data/gold_h2.db --pub-date 2026-08-16
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path


# ── Sentinel v2.1 (Anchor Fix) ──────────────────────────────────────────────
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
CACHE_DIR = DATA_DIR / "cache" / "wgc_etf"
REPORT_DIR = DATA_DIR / "reports"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

# Series (riêng biệt, không merge)
SERIES_DEMAND = "ETF_DEMAND_TONNES"
SERIES_FLOWS = "ETF_FLOWS_USD"
UNIT_DEMAND = "tonnes"
UNIT_FLOWS = "usd"
SOURCE = "WGC_ETF_API"

ENDPOINTS = {
    "flows": "https://fsapi.gold.org/api/v11/charts/etfv2/revised/flows-chart2",
    "holdings": "https://fsapi.gold.org/api/v11/charts/etfv2/revised/holdings-chart2",
    "table": "https://fsapi.gold.org/api/v11/charts/etfv2/revised/archive-tablegroup/all",
}

# Series cần loại (metadata, không phải region)
_SKIP_NAMES = {"Gold Price (rhs)", "Gold Price", "Total", "Global inflows / Positive Demand"}

_REGIONS = ("North America", "Europe", "Asia", "Other")


def fetch_snapshot(endpoint: str = ENDPOINTS["flows"]) -> tuple[str, dict]:
    """GET JSON snapshot → (asOfDate, parsed). KHÔNG lưu vào cache."""
    req = urllib.request.Request(
        endpoint,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.gold.org/"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cd = data.get("chartData", {})
    as_of = cd.get("asOfDate")
    if not as_of:
        raise ValueError("chartData.asOfDate missing — không phải snapshot hợp lệ")
    return as_of, cd


def _ts_to_date(ts: int) -> str:
    """ms epoch → 'YYYY-MM-DD' (UTC)."""
    return datetime.datetime.fromtimestamp(ts / 1000, datetime.UTC).strftime("%Y-%m-%d")


def parse_snapshot(cd: dict) -> list[dict]:
    """Snapshot flows JSON → list rows: {series, entity, obs, value}.

    Chỉ Monthly (obs = cuối tháng). Bỏ series Gold Price. Region riêng biệt.
    """
    rows: list[dict] = []
    monthly = cd.get("data", {}).get("Monthly", {}).get("series", {})
    for unit_key, series_key in (("tonnes", SERIES_DEMAND), ("usd", SERIES_FLOWS)):
        series = monthly.get(unit_key, [])
        for s in series:
            name = s["name"]
            if name in _SKIP_NAMES or name not in _REGIONS:
                continue
            for ts, val in s["data"]:
                if val is None:
                    continue
                obs = _ts_to_date(ts)
                rows.append(
                    {
                        "series": series_key,
                        "entity": name,
                        "observation_date": obs,
                        "value": float(val),
                    }
                )
    return rows


def build_vintages(snapshots: list[tuple[str, str, list[dict]]]) -> list[dict]:
    """Snapshots [(pub_date, as_of, rows)] → rows kèm publication_date.

    Mỗi snapshot là 1 vintage của các obs (forward ladder). PUB do bên ngoài
    cung cấp (fetch date) — KHÔNG suy diễn từ asOfDate.
    """
    out = []
    for pub, _as_of, rows in snapshots:
        for r in rows:
            out.append(
                {
                    "series": r["series"],
                    "entity": r["entity"],
                    "observation_date": r["observation_date"],
                    "publication_date": pub,
                    "value": r["value"],
                    "unit": UNIT_DEMAND if r["series"] == SERIES_DEMAND else UNIT_FLOWS,
                    "source": SOURCE,
                    "provenance": f"WGC_ETF_API as_of={_as_of}",
                }
            )
    return out


def ingest(conn: sqlite3.Connection, vintage_rows: list[dict]) -> int:
    """INSERT các vintage vào gold_h2_series (idempotent qua UNIQUE index)."""
    from src.research.gold_h2_series import insert_vintage

    n = 0
    for r in vintage_rows:
        insert_vintage(
            conn,
            series=r["series"],
            entity=r["entity"],
            observation_date=r["observation_date"],
            publication_date=r["publication_date"],
            value=r["value"],
            unit=r["unit"],
            source=r["source"],
            provenance=r["provenance"],
        )
        n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="WGC Gold ETF flows adapter")
    parser.add_argument("--fetch", action="store_true", help="fetch snapshot từ API")
    parser.add_argument("--ingest", action="store_true", help="ingest từ cache JSON")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--pub-date", default=None, help="publication_date thật (fetch date, YYYY-MM-DD). MẶC ĐỊNH = hôm nay")
    parser.add_argument("--json", default=None, help="đường dẫn JSON snapshot (thay fetch)")
    args = parser.parse_args()

    if args.fetch and args.json:
        print("Chọn 1 trong --fetch hoặc --json")
        sys.exit(1)
    if not args.fetch and not args.ingest:
        parser.print_help()
        sys.exit(1)

    if args.fetch or args.json:
        if args.json:
            cd = json.loads(Path(args.json).read_text(encoding="utf-8")).get("chartData", {})
            as_of = cd.get("asOfDate", "unknown")
        else:
            as_of, cd = fetch_snapshot()
        rows = parse_snapshot(cd)
        pub = args.pub_date or datetime.date.today().isoformat()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        snap_file = CACHE_DIR / f"snapshot_{as_of}.json"
        snap_file.write_text(
            json.dumps({"asOfDate": as_of, "chartData": cd}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"  [FETCH] as_of={as_of} rows={len(rows)} pub={pub}")
        print(f"  [SAVE]  {snap_file}")
        print("  → chạy --ingest --pub-date <fetch date> để ghi DB")

    if args.ingest:
        pub = args.pub_date or datetime.date.today().isoformat()
        # nạp mọi snapshot trong cache → vintage ladder
        snapshots: list[tuple[str, str, list[dict]]] = []
        for f in sorted(CACHE_DIR.glob("snapshot_*.json")):
            j = json.loads(f.read_text(encoding="utf-8"))
            as_of = j.get("asOfDate", "")
            cd = j.get("chartData", {})
            snapshots.append((pub, as_of, parse_snapshot(cd)))
        if not snapshots:
            print("Không có snapshot nào trong cache. Chạy --fetch trước.")
            sys.exit(1)
        rows = build_vintages(snapshots)
        conn = sqlite3.connect(args.db)
        n = ingest(conn, rows)
        conn.close()
        print(f"  [INGEST] {n} vintage rows (series {SERIES_DEMAND} + {SERIES_FLOWS})")
        print(f"  [PUB]    {pub}")


if __name__ == "__main__":
    main()
