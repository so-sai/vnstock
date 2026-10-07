"""test_poison_detector.py — Giải pháp D: Poison Detector Auditor.

Điểm then chốt cần bảo vệ: mã chỉ TRÙNG CLOSE (volume khác) là bình thường →
không được gắn cờ. Chỉ trùng CẢ close VÀ volume mới là dấu vân bản sao.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "backend" / "scripts" / "poison_detector.py"

SCHEMA = """
CREATE TABLE daily_ohlcv (
    symbol TEXT, date TEXT, close REAL, volume REAL,
    is_stale INTEGER DEFAULT 0, source TEXT DEFAULT 'test'
);
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("poison_detector", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    yield c
    c.close()


def _row(c, date, symbol, close, volume):
    c.execute("INSERT INTO daily_ohlcv (symbol,date,close,volume) VALUES (?,?,?,?)", (symbol, date, close, volume))


def _seed_day(c, date, spec):
    """spec: list of (symbol, close, volume)"""
    for s, cl, v in spec:
        _row(c, date, s, cl, v)
    c.commit()


def test_clean_day_khong_gan_co(conn):
    mod = _load_module()
    _seed_day(conn, "2026-10-05", [("AAA", 10000, 1000), ("BBB", 20000, 2000), ("CCC", 30000, 3000)])
    _seed_day(conn, "2026-10-06", [("AAA", 10100, 1100), ("BBB", 19900, 2100), ("CCC", 30500, 3300)])
    vs = mod.audit(conn, min_common=3, min_ratio=0.5)
    assert [v["date"] for v in vs] == ["2026-10-06"]
    assert vs[0]["suspect"] is False
    assert vs[0]["copies"] == 0


def test_ngay_bi_dump_thi_gan_co(conn):
    mod = _load_module()
    # 4 mã; 3 mã là bản sao (close + volume y hệt)
    _seed_day(conn, "2026-09-30", [("AAA", 10000, 1000), ("BBB", 20000, 2000), ("CCC", 30000, 3000), ("DDD", 40000, 4000)])
    _seed_day(conn, "2026-10-01", [("AAA", 10000, 1000), ("BBB", 20000, 2000), ("CCC", 30000, 3000), ("DDD", 41000, 4500)])
    vs = mod.audit(conn, min_common=4, min_ratio=0.5)
    v = vs[0]
    assert v["suspect"] is True
    assert v["copies"] == 3
    assert v["ratio"] == 0.75
    assert set(v["sample"]) == {"AAA", "BBB", "CCC"}


def test_trung_close_nhung_khac_volume_KHONG_gan_co(conn):
    """Bảo vệ chống false-positive: mã đứng giá với KL khác là bình thường."""
    mod = _load_module()
    _seed_day(conn, "2026-10-05", [("AAA", 10000, 1000), ("BBB", 20000, 2000)])
    # close giống hệt, volume KHÁC → không phải bản sao
    _seed_day(conn, "2026-10-06", [("AAA", 10000, 5555), ("BBB", 20000, 9999)])
    vs = mod.audit(conn, min_common=2, min_ratio=0.5)
    assert vs[0]["copies"] == 0
    assert vs[0]["suspect"] is False


def test_duoi_nguong_ma_chung_thi_bo_qua(conn):
    mod = _load_module()
    _seed_day(conn, "2026-10-05", [("AAA", 10000, 1000)])
    _seed_day(conn, "2026-10-06", [("AAA", 10000, 1000)])
    vs = mod.audit(conn, min_common=200, min_ratio=0.5)
    assert vs == []


def test_apply_chi_tag_dung_dong_copy(conn):
    mod = _load_module()
    _seed_day(conn, "2026-09-30", [("AAA", 10000, 1000), ("BBB", 20000, 2000), ("CCC", 30000, 3000), ("DDD", 40000, 4000)])
    _seed_day(conn, "2026-10-01", [("AAA", 10000, 1000), ("BBB", 20000, 2000), ("CCC", 30000, 3000), ("DDD", 41000, 4500)])
    vs = mod.audit(conn, min_common=4, min_ratio=0.5)
    n = mod.apply_quarantine(conn, vs)
    assert n == 3

    rows = dict(
        (s, (st, src))
        for s, st, src in conn.execute(
            "SELECT symbol, is_stale, source FROM daily_ohlcv WHERE date='2026-10-01'"
        )
    )
    for s in ("AAA", "BBB", "CCC"):
        assert rows[s][0] == 1
        assert mod.TAG in rows[s][1]
    assert rows["DDD"][0] == 0, "mã không copy không được tag"
    assert mod.TAG not in rows["DDD"][1]


def test_apply_bao_toan_dong_da_sach(conn):
    """Chạy --apply 2 lần không nhân đôi tag (chỉ tag dòng is_stale=0)."""
    mod = _load_module()
    _seed_day(conn, "2026-09-30", [("AAA", 10000, 1000), ("BBB", 20000, 2000)])
    _seed_day(conn, "2026-10-01", [("AAA", 10000, 1000), ("BBB", 20000, 2000)])
    vs = mod.audit(conn, min_common=2, min_ratio=0.5)
    first = mod.apply_quarantine(conn, vs)
    assert first == 2
    second = mod.apply_quarantine(conn, mod.audit(conn, min_common=2, min_ratio=0.5))
    assert second == 0
    src = conn.execute("SELECT source FROM daily_ohlcv WHERE symbol='AAA' AND date='2026-10-01'").fetchone()[0]
    assert src.count(mod.TAG) == 1
