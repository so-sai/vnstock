"""Test cho `backend/scripts/quarantine_unit_mix.py` — cách ly dòng trộn đơn vị giá.

[WHY]
  Đo 2026-10-16 trên `screener_cache.db`: 452 cặp ngày liên tiếp nhảy ≥100× trên dòng HỢP LỆ vị từ
  (92 mã, ~100% `is_stale = 0`) — bất khả thi theo luật biên độ sàn VN, và `valuation_scores` đã
  hấp thụ hậu quả (BII 2024Q2 ⇒ P/S 25.584; DDG 2026Q2 ⇒ P/B 226). Công cụ cách ly sinh ra để
  nhốt nhánh phantom mà KHÔNG đụng nhánh thật.

[ROOT CAUSE]
  Một script ad-hoc (không còn trong repo — `grep rescaled_vci` = 0 kết quả) đã nhân ×1000 lên chuỗi
  vốn ĐÃ ở đơn vị đồng, ghi dấu vào `source` (`vci|rescaled_vci_x1000`, 4.633 dòng / 171 mã).

[INVARIANT]
  1. Chỉ cách ly khi phân xử được ĐÚNG BÊN: có neo ngoài. 92 mã không neo ⇒ chỉ khai báo, cấm đụng
     (đã đo: hai bản dry-run trước gắn cờ oan nhánh ĐÚNG của DDG/BII).
  2. Chỉ cách ly theo NGƯỠNG GIÁ `close >= 100 × cận trên neo`, không theo cửa sổ ngày — vì hai thang
     XEN KẼ trong cùng khoảng thời gian (BII xen 800.000 với 1.000).
  3. Additive + idempotent: `is_stale = 1` + nhãn; giá trị giá KHÔNG bị sửa; chạy lại là no-op.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Cho phép trỏ sang bản script khác (dùng để chứng minh RED với thuật toán cũ).
SCRIPT = Path(
    os.environ.get(
        "QUARANTINE_SCRIPT", str(Path(__file__).resolve().parents[1] / "scripts" / "quarantine_unit_mix.py")
    )
)


def _load_module():
    spec = importlib.util.spec_from_file_location("quarantine_unit_mix", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["quarantine_unit_mix"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load_module()


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "ohlcv_test.db"))
    c.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,"
        " adj_close REAL, volume REAL, source TEXT, is_stale INTEGER DEFAULT 0, UNIQUE(symbol, date))"
    )
    return c


def _add(conn, symbol: str, first: str, n: int, close: float, source: str = "vci") -> None:
    """Thêm n phiên liên tiếp, giá phẳng = close (đủ hợp lệ vị từ: low ≤ close ≤ high, volume ≥ 0)."""
    from datetime import date, timedelta

    y, m, d = (int(x) for x in first.split("-"))
    day = date(y, m, d)
    for _ in range(n):
        conn.execute(
            "INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, adj_close, volume, source,"
            " is_stale) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (symbol, day.isoformat(), close, close, close, close, close, 10_000, source),
        )
        day += timedelta(days=1)
    conn.commit()


# [WHY] Ca hồi quy trung tâm: nhánh phantom PHẢI bị cách ly, còn nhánh thật — kể cả nhánh giá CAO
# hợp lệ trong quá khứ — PHẢI được giữ. Hai bản dry-run đầu đã gắn cờ oan chính những dòng này,
# nên ca này là chốt chặn chống vá quá tay.
def test_chi_cach_ly_nhanh_phantom_giu_nguyen_nhanh_that(mod, conn, monkeypatch):
    monkeypatch.setitem(mod.EXTERNAL_ANCHOR, "TST1", (800.0, 1100.0))
    _add(conn, "TST1", "2023-01-02", 40, 17_160.0)  # nhánh "cao" HỢP LỆ (17× cận trên neo)
    _add(conn, "TST1", "2024-01-02", 10, 950.0)  # nhánh đúng hiện tại (trong neo)
    _add(conn, "TST1", "2024-03-01", 5, 900_000.0)  # phantom (818× neo)
    _add(conn, "TST1", "2024-04-01", 8, 1_000.0)  # nhánh đúng trở lại
    _add(conn, "TST1", "2024-06-01", 3, 800_000.0)  # phantom lần hai

    quarantine, _ = mod.analyse(conn)
    assert [r["symbol"] for r in quarantine] == ["TST1", "TST1"]
    assert sum(r["n_rows"] for r in quarantine) == 8
    assert all(r["median"] >= 110_000 for r in quarantine)

    assert mod.apply_quarantine(conn, quarantine) == 8
    kept = conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='TST1' AND is_stale=0"
    ).fetchone()[0]
    assert kept == 40 + 10 + 8, "nhánh hợp lệ (17.160đ và 950–1.000đ) bị đụng ⇒ vá quá tay"
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE is_stale=1 AND close=900000.0"
    ).fetchone()[0] == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE is_stale=1 AND close=800000.0"
    ).fetchone()[0] == 3


# [WHY] Luật "thà thiếu còn hơn fake": mã KHÔNG có neo thì cấu trúc chuỗi không đủ để phân xử bên nào
# sai (đã chứng minh bằng thực nghiệm) ⇒ phải khai báo chứ TUYỆT ĐỐI không ghi.
def test_ma_khong_neo_thi_chi_khai_bao_khong_dung_du_lieu(mod, conn):
    _add(conn, "TST2", "2024-01-02", 20, 1_200.0)
    _add(conn, "TST2", "2024-03-01", 20, 900_000.0)

    quarantine, unresolved = mod.analyse(conn)
    assert quarantine == []
    assert [r["symbol"] for r in unresolved] == ["TST2"]
    assert mod.apply_quarantine(conn, quarantine) == 0
    assert conn.execute("SELECT SUM(is_stale) FROM daily_ohlcv").fetchone()[0] == 0


# [WHY] BII xen dòng 1.000 vào giữa các đoạn 800.000 trong CÙNG khoảng thời gian ⇒ lọc theo cửa sổ
# ngày sẽ cách ly oan dòng thật; bộ lọc phải theo NGƯỠNG GIÁ.
def test_dong_that_xen_ke_trong_cua_so_phantom_khong_bi_cach_ly(mod, conn, monkeypatch):
    monkeypatch.setitem(mod.EXTERNAL_ANCHOR, "TST3", (300.0, 500.0))
    _add(conn, "TST3", "2024-01-02", 6, 900_000.0)  # đoạn phantom
    _add(conn, "TST3", "2024-01-08", 1, 1_000.0)  # dòng THẬT xen kẽ
    _add(conn, "TST3", "2024-01-09", 6, 800_000.0)  # đoạn phantom kế tiếp
    _add(conn, "TST3", "2024-02-01", 10, 400.0)  # nhánh đúng (trong neo)

    quarantine, _ = mod.analyse(conn)
    n = mod.apply_quarantine(conn, quarantine)
    assert n == 12, "chỉ 12 dòng phantom được gắn cờ"
    assert conn.execute(
        "SELECT is_stale FROM daily_ohlcv WHERE symbol='TST3' AND date='2024-01-08'"
    ).fetchone()[0] == 0, "dòng thật xen kẽ bị cách ly oan"
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='TST3' AND is_stale=0"
    ).fetchone()[0] == 11


# [WHY] Idempotent — chạy lại công cụ (thủ công hoặc trong cron) không được ghi thêm hoặc gắn nhãn chồng.
def test_chay_lai_la_no_op_va_nhan_khong_chong(mod, conn, monkeypatch):
    monkeypatch.setitem(mod.EXTERNAL_ANCHOR, "TST4", (800.0, 1100.0))
    _add(conn, "TST4", "2024-01-02", 5, 1_000.0)
    _add(conn, "TST4", "2024-02-01", 4, 900_000.0)

    quarantine, _ = mod.analyse(conn)
    assert mod.apply_quarantine(conn, quarantine) == 4
    assert mod.apply_quarantine(conn, quarantine) == 0, "chạy lại phải là no-op"

    src = conn.execute("SELECT DISTINCT source FROM daily_ohlcv WHERE is_stale=1").fetchall()
    assert src == [("vci|" + mod.TAG,)], f"nhãn bị gắn chồng: {src}"
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE close=900000.0 AND is_stale=1"
    ).fetchone()[0] == 4, "giá trị giá không được sửa (additive)"


# [WHY] Dòng đã bị cách ly/gắn nhãn từ nguồn khác (ví dụ `rescaled_vci_x1000`) vẫn phải qua được
# công cụ mà không sinh lỗi, và việc đếm dòng phải nhìn thấy `source` NULL.
def test_source_null_va_nhan_san_co_khong_lam_sap(mod, conn, monkeypatch):
    monkeypatch.setitem(mod.EXTERNAL_ANCHOR, "TST5", (300.0, 500.0))
    _add(conn, "TST5", "2024-01-02", 6, 400.0, source="vci|rescaled_vci_x1000")
    conn.execute(
        "INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, adj_close, volume, source,"
        " is_stale) VALUES ('TST5','2024-03-01',900000,900000,900000,900000,900000,1000,NULL,0)"
    )
    conn.execute(
        "INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, adj_close, volume, source,"
        " is_stale) VALUES ('TST5','2024-03-02',900000,900000,900000,900000,900000,1000,NULL,0)"
    )
    conn.commit()

    quarantine, _ = mod.analyse(conn)
    assert mod.apply_quarantine(conn, quarantine) == 2
    assert conn.execute(
        "SELECT source FROM daily_ohlcv WHERE date='2024-03-01'"
    ).fetchone()[0] == "|" + mod.TAG, "source NULL phải được xử lý, không làm sập"
