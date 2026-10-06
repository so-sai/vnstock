"""Test forward-collect foreign (snapshot_price_board.write_foreign_to_db + F3 gate).

Bảo vệ: fail-closed. Guard từ chối / phủ < ngưỡng → KHÔNG ghi gì (missing > fake).
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "backend" / "scripts" / "snapshot_price_board.py"

SCHEMA = """
CREATE TABLE market_foreign_history (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    foreign_vol INTEGER, net_vol INTEGER, net_value REAL,
    flow_kind TEXT DEFAULT 'daily',
    source TEXT DEFAULT 'unknown', fetched_at TEXT, is_verified INTEGER DEFAULT 0,
    PRIMARY KEY (symbol, date)
);
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("snapshot_pb", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _records(pairs):
    """pairs: list of (symbol, buy, sell, match_price)"""
    return [
        {
            "symbol": s,
            "foreign_buy_volume": b,
            "foreign_sell_volume": x,
            "match_price": p,
            "accumulated_volume": 1000,
        }
        for s, b, x, p in pairs
    ]


@pytest.fixture()
def mod_and_db(tmp_path, monkeypatch):
    mod = _load_module()
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(mod, "DB_PATH", db)
    return mod, db


def _count(db, date="2026-10-06"):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM market_foreign_history WHERE date=?", (date,)
        ).fetchone()[0]
    finally:
        conn.close()


GOOD = [
    ("AAA", 2_000_000, 500_000, 10_000.0),
    ("BBB", 1_000_000, 1_500_000, 20_000.0),
    ("CCC", 300_000, 100_000, 30_000.0),
]


def test_dry_run_khong_ghi_db(mod_and_db):
    mod, db = mod_and_db
    rc, n = mod.write_foreign_to_db(_records(GOOD), "2026-10-06", min_rows=3, dry_run=True)
    assert (rc, n) == (0, 3)
    assert _count(db) == 0, "dry-run KHÔNG được ghi"


def test_cong_phu_fail_closed(mod_and_db):
    """Phủ < ngưỡng → từ chối ghi, kể cả khi dữ liệu hợp lệ."""
    mod, db = mod_and_db
    rc, n = mod.write_foreign_to_db(_records(GOOD), "2026-10-06", min_rows=1200)
    assert (rc, n) == (1, 0)
    assert _count(db) == 0, "phủ thấp phải KHÔNG ghi"


def test_guard_chan_level_signature(mod_and_db):
    """Σ|net_value| > 50.000 tỷ = chữ ký dữ liệu 'level' → guard từ chối."""
    mod, db = mod_and_db
    # net_vol lớn × giá lớn → net_value ~20.000 tỷ/mã; 4 mã → 80.000 tỷ > 50.000
    heavy = [
        ("X1", 220_000_000, 20_000_000, 100_000.0),
        ("X2", 220_000_000, 20_000_000, 100_000.0),
        ("X3", 220_000_000, 20_000_000, 100_000.0),
        ("X4", 220_000_000, 20_000_000, 100_000.0),
    ]
    rc, n = mod.write_foreign_to_db(_records(heavy), "2026-10-06", min_rows=1)
    assert (rc, n) == (1, 0)
    assert _count(db) == 0


def test_ghi_that_voi_provenance(mod_and_db):
    mod, db = mod_and_db
    rc, n = mod.write_foreign_to_db(_records(GOOD), "2026-10-06", min_rows=3)
    assert (rc, n) == (0, 3)
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT symbol, flow_kind, source, is_verified, fetched_at, net_vol FROM "
            "market_foreign_history WHERE date='2026-10-06' ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    for symbol, flow_kind, source, is_verified, fetched_at, _net_vol in rows:
        assert flow_kind == "daily"
        assert source == mod.FOREIGN_SOURCE
        assert is_verified == 1
        assert fetched_at, "phải có fetched_at (provenance)"
    # net_vol = buy - sell ; BBB là bán ròng
    assert dict((r[0], r[5]) for r in rows)["BBB"] == -500_000


def test_idempotent_insert_or_replace(mod_and_db):
    """Chạy 2 lần cùng phiên không nhân đôi dòng (PK symbol+date)."""
    mod, db = mod_and_db
    mod.write_foreign_to_db(_records(GOOD), "2026-10-06", min_rows=3)
    mod.write_foreign_to_db(_records(GOOD), "2026-10-06", min_rows=3)
    assert _count(db) == 3


def test_bo_ma_thieu_cap_ngoai_gia(mod_and_db):
    mod, db = mod_and_db
    recs = _records(GOOD) + [{"symbol": "DDD", "foreign_buy_volume": None,
                              "foreign_sell_volume": None, "match_price": 1000.0}]
    rc, n = mod.write_foreign_to_db(recs, "2026-10-06", min_rows=3)
    assert (rc, n) == (0, 3), "mã thiếu cặp ngoại/giá phải bị loại, không làm hỏng lượt"
