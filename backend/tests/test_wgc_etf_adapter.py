"""test_wgc_etf_adapter.py — Unit tests WGC ETF adapter (fixture JSON, no network).

Kiểm tra:
  - parse_snapshot: Monthly, region riêng, bỏ Gold Price/Total, obs = cuối tháng.
  - Đúng semantics: tonnes → ETF_DEMAND_TONNES, usd → ETF_FLOWS_USD.
  - build_vintages: pub từ bên ngoài (fetch date), provenance giữ asOfDate.
  - ingest idempotent (UNIQUE index) + tạo vintage ladder forward.
  - KHÔNG lấy asOfDate làm publication_date (guardrail PIT).
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.gold_h2_series import init_table
from src.research.wgc_etf_adapter import (
    _SKIP_NAMES,
    SERIES_DEMAND,
    SERIES_FLOWS,
    build_vintages,
    ingest,
    parse_snapshot,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

def _mk_flow_series(name, data):
    return {"name": name, "data": data}


# 2 obs: 2026-06-30 (ts 1782777600000), 2026-07-31 (ts 1785456000000) — timestamp
# thật từ API (decode đã xác minh)
TS_JUN = 1782777600000
TS_JUL = 1785456000000

SNAP = {
    "asOfDate": "2026-08-07",
    "data": {
        "Monthly": {
            "series": {
                "tonnes": [
                    _mk_flow_series("North America", [[TS_JUN, 0.28], [TS_JUL, 0.29]]),
                    _mk_flow_series("Europe", [[TS_JUN, 1.21], [TS_JUL, 17.34]]),
                    _mk_flow_series("Gold Price (rhs)", [[TS_JUN, 4000.0], [TS_JUL, 4073.9]]),
                ],
                "usd": [
                    _mk_flow_series("North America", [[TS_JUN, 70635015.5], [TS_JUL, 1e8]]),
                    _mk_flow_series("Europe", [[TS_JUN, 1.2e8], [TS_JUL, 2.1e9]]),
                    _mk_flow_series("Gold Price (rhs)", [[TS_JUN, 4000.0], [TS_JUL, 4073.9]]),
                ],
            }
        }
    },
}


def test_parse_snapshot_monthly_regions_both_series():
    rows = parse_snapshot(SNAP)
    # 2 regions x 2 series x 2 obs = 8
    assert len(rows) == 8
    demand = [r for r in rows if r["series"] == SERIES_DEMAND]
    flows = [r for r in rows if r["series"] == SERIES_FLOWS]
    assert len(demand) == 4 and len(flows) == 4
    # entity region riêng, không có Gold Price
    entities = {r["entity"] for r in rows}
    assert entities == {"North America", "Europe"}
    assert "Gold Price (rhs)" not in entities


def test_parse_snapshot_obs_month_end_and_value():
    rows = parse_snapshot(SNAP)
    eur = [r for r in rows if r["entity"] == "Europe" and r["series"] == SERIES_DEMAND]
    assert sorted(r["observation_date"] for r in eur) == ["2026-06-30", "2026-07-31"]
    jul = next(r for r in eur if r["observation_date"] == "2026-07-31")
    assert abs(jul["value"] - 17.34) < 1e-9


def test_parse_snapshot_skips_total_metadata_names():
    snap = json.loads(json.dumps(SNAP))
    snap["data"]["Monthly"]["series"]["tonnes"].append(
        _mk_flow_series("Total", [[TS_JUN, 99.0], [TS_JUL, 100.0]])
    )
    snap["data"]["Monthly"]["series"]["tonnes"].append(
        _mk_flow_series("Global inflows / Positive Demand", [[TS_JUN, 5.0], [TS_JUL, 6.0]])
    )
    rows = parse_snapshot(snap)
    assert "Total" in _SKIP_NAMES
    assert "Global inflows / Positive Demand" in _SKIP_NAMES
    entities = {r["entity"] for r in rows}
    assert "Total" not in entities
    assert "Global inflows / Positive Demand" not in entities


def test_build_vintages_uses_external_pub_not_asof():
    rows = parse_snapshot(SNAP)
    v = build_vintages([("2026-08-16", "2026-08-07", rows)])
    assert v, "phải có vintage rows"
    pubs = {r["publication_date"] for r in v}
    assert pubs == {"2026-08-16"}  # pub = fetch date, KHÔNG phải asOfDate
    prov = next(r["provenance"] for r in v)
    assert "as_of=2026-08-07" in prov


def test_ingest_two_snapshots_forward_ladder():
    """2 snapshot khác ngày → 2 vintage riêng cho cùng obs (forward ladder)."""
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    rows1 = parse_snapshot(SNAP)
    # snapshot thứ 2: revision giá trị
    snap2 = json.loads(json.dumps(SNAP))
    snap2["asOfDate"] = "2026-08-14"
    for s in snap2["data"]["Monthly"]["series"]["tonnes"]:
        if s["name"] == "Europe":
            s["data"][1][1] = 18.9  # revision
    rows2 = parse_snapshot(snap2)
    v = build_vintages(
        [("2026-08-16", "2026-08-07", rows1), ("2026-08-23", "2026-08-14", rows2)]
    )
    n = ingest(conn, v)
    assert n == 16  # 8 x 2 snapshots
    # cùng obs Europe demand có 2 vintage (revision bảo toàn)
    n_eu = conn.execute(
        "SELECT COUNT(*) FROM gold_h2_series WHERE entity='Europe' AND series=? AND observation_date='2026-07-31'",
        (SERIES_DEMAND,),
    ).fetchone()[0]
    assert n_eu == 2
    conn.close()


def test_ingest_idempotent_same_vintage():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    rows = parse_snapshot(SNAP)
    v = build_vintages([("2026-08-16", "2026-08-07", rows)])
    n1 = ingest(conn, v)
    ingest(conn, v)  # cùng pub → UNIQUE index → REPLACE, không tăng
    total = conn.execute("SELECT COUNT(*) FROM gold_h2_series").fetchone()[0]
    assert n1 == 8
    assert total == 8
    conn.close()


def test_no_forward_fill_missing_obs():
    """Region thiếu obs → không sinh row ảo."""
    snap = json.loads(json.dumps(SNAP))
    # Asia chỉ có 1 obs
    snap["data"]["Monthly"]["series"]["tonnes"].append(
        _mk_flow_series("Asia", [[TS_JUL, 4.8]])
    )
    rows = parse_snapshot(snap)
    asia = [r for r in rows if r["entity"] == "Asia"]
    assert len(asia) == 1
    assert asia[0]["observation_date"] == "2026-07-31"
