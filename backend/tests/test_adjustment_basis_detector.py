"""test_adjustment_basis_detector.py — P2: phát hiện lệch cơ sở giá.

Tầng 1 là quét cục bộ (SQL LAG), nên test được hoàn toàn offline và phải bảo vệ:
  - nhảy âm lớn  → SEVERE (>=16%, vượt cả UPCOM ±15%)
  - nhảy nhỏ     → WARN   (>=8%, vượt biên HOSE ±7%)
  - dòng is_stale=1 KHÔNG được tính (chuỗi sạch)
  - LAG phải theo TỪNG mã, không so chéo giữa các mã
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "backend" / "scripts" / "adjustment_basis_detector.py"

SCHEMA = """
CREATE TABLE daily_ohlcv (
    symbol TEXT, date TEXT, close REAL, volume REAL,
    is_stale INTEGER DEFAULT 0, source TEXT DEFAULT 'test'
);
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("adj_detector", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    yield c
    c.close()


def _seed(c, symbol, series, stale_dates=()):
    for d, cl in series:
        c.execute(
            "INSERT INTO daily_ohlcv (symbol,date,close,volume,is_stale) VALUES (?,?,?,?,?)",
            (symbol, d, cl, 1000.0, 1 if d in stale_dates else 0),
        )
    c.commit()


def test_chuoi_binh_thuong_khong_co_co(conn):
    mod = _load_module()
    _seed(conn, "AAA", [("2026-09-01", 10000), ("2026-09-02", 10200), ("2026-09-03", 10100)])
    assert mod.scan_jumps(conn) == []


def test_nhay_am_lon_la_SEVERE(conn):
    mod = _load_module()
    # -20% trong 1 phiên: bất khả thi kể cả UPCOM (±15%) → SEVERE
    _seed(conn, "SSI", [("2026-08-12", 24500), ("2026-08-14", 19600)])
    js = mod.scan_jumps(conn)
    assert len(js) == 1
    assert js[0]["symbol"] == "SSI"
    assert js[0]["severity"] == "SEVERE"
    assert js[0]["pct"] == pytest.approx(-20.0, abs=0.01)


def test_nhay_vua_la_WARN(conn):
    mod = _load_module()
    # +10%: vượt biên HOSE ±7% nhưng trong biên UPCOM ±15% → WARN
    _seed(conn, "BBB", [("2026-09-01", 10000), ("2026-09-02", 11000)])
    js = mod.scan_jumps(conn)
    assert len(js) == 1
    assert js[0]["severity"] == "WARN"


def test_dong_stale_khong_duoc_tinh(conn):
    mod = _load_module()
    # Cú nhảy nằm trên dòng bị cách ly → không tính
    _seed(conn, "CCC", [("2026-09-01", 10000), ("2026-09-02", 5000)], stale_dates={"2026-09-02"})
    assert mod.scan_jumps(conn) == []


def test_lag_theo_tung_ma_khong_so_cheo(conn):
    mod = _load_module()
    # Hai mã có mức giá rất khác nhau: không được sinh cú nhảy giả ở dòng đầu mỗi mã
    _seed(conn, "AAA", [("2026-09-01", 10000), ("2026-09-02", 10100)])
    _seed(conn, "ZZZ", [("2026-09-01", 90000), ("2026-09-02", 90500)])
    assert mod.scan_jumps(conn) == []
