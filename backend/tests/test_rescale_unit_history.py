"""Test cho `backend/scripts/rescale_unit_history.py` — chuẩn hóa đơn vị lịch sử (nghìn → đồng).

[WHY]
  Đo 2026-10-16: 7 mã `PPS MGG VTJ MIE HHG PXA VIN` mang hai đơn vị trong cùng một chuỗi (dải cũ ở
  "nghìn đồng", dải gần đây ở đồng) ⇒ bước nhảy ~1000× giữa hai dải, và momentum là hàm tỷ lệ giá nên
  nó sinh lợi suất giả khổng lồ. Công cụ phải nhân ×1000 dải cũ mà KHÔNG được chạm dải đúng.

[ROOT CAUSE]
  Hai nguồn ghi hai đơn vị cho cùng mã (dải cũ `kbs` ở 6/7 mã, `vci` ở VTJ), rồi đường nạp mới ghi đồng.

[INVARIANT]
  1. Hệ số ĐÚNG 1000, không dùng tỷ lệ đo được (tỷ lệ lẫn biến động giá thật).
  2. G2: sau ×1000, giao thoa ≤20% — vi phạm ⇒ KHÔNG ghi (fail-closed).
  3. G3: sau rescale chuỗi không còn bước nhảy ≥10× — còn ⇒ HUỶ mã, không ghi nửa vời.
  4. Chỉ đụng `is_stale = 0`; idempotent; nhãn theo nguồn thật; mọi cột giá ×1000 (giữ low≤close≤high).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "rescale_unit_history", Path(__file__).resolve().parents[1] / "scripts" / "rescale_unit_history.py"
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
sys.modules["rescale_unit_history"] = mod
_SPEC.loader.exec_module(mod)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "ohlcv.db"))
    c.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,"
        " adj_close REAL, volume REAL, source TEXT, is_stale INTEGER DEFAULT 0, UNIQUE(symbol, date))"
    )
    return c


def _add(conn, sym: str, first: str, n: int, close: float, source: str, stale: int = 0) -> None:
    y, m, d = (int(x) for x in first.split("-"))
    day = date(y, m, d)
    for _ in range(n):
        conn.execute(
            "INSERT INTO daily_ohlcv (symbol,date,open,high,low,close,adj_close,volume,source,is_stale)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sym, day.isoformat(), close, close * 1.01, close * 0.99, close, close, 1000, source, stale),
        )
        day += timedelta(days=1)
    conn.commit()


# [WHY] Ca trung tâm: dải cũ ở nghìn đồng + dải mới ở đồng, giao thoa tự nhiên ⇒ phải rescale ĐÚNG dải
# cũ và GIỮ NGUYÊN dải mới; đồng thời mọi cột giá phải cùng ×1000 để bất biến low ≤ close ≤ high còn đúng.
def test_rescale_dung_dai_cu_va_giu_dai_moi(conn):
    _add(conn, "TST", "2024-01-01", 20, 9.0, "kbs")  # dải cũ (nghìn đồng)
    _add(conn, "TST", "2024-02-01", 10, 9_200.0, "kbs")  # dải mới (đồng)
    rec = mod.analyse_symbol(conn, "TST")
    assert rec["verdict"] == "RESCALE", rec
    assert rec["n_old"] == 20 and rec["n_new"] == 10
    assert mod.apply_symbol(conn, rec) == 20

    old = conn.execute("SELECT close, low, high, adj_close FROM daily_ohlcv WHERE date<'2024-02-01'").fetchall()
    assert all(c == (9000.0, 8910.0, 9090.0, 9000.0) for c in old), old
    new = conn.execute("SELECT close FROM daily_ohlcv WHERE date>='2024-02-01'").fetchall()
    assert {c[0] for c in new} == {9_200.0}, "dải mới bị đụng ⇒ vá quá tay"
    src = conn.execute("SELECT DISTINCT source FROM daily_ohlcv WHERE date<'2024-02-01'").fetchall()
    assert src == [("kbs|rescaled_kbs_historical_x1000",)], src


# [WHY] G2 — guard chủ hệ thống chốt (≤20%): nếu ×1000 tạo bước nhảy phi lý thì 1000× KHÔNG đúng.
# Không có ca này thì công cụ sẽ nhân bừa ở những mã mà "dải thấp" thực ra là giá thật.
def test_g2_giao_thoa_lech_qua_20pct_thi_khong_ghi(conn):
    # Dải: 19 phiên ở 9 (nghìn) + 1 phiên cuối ở 1 ⇒ median dải cũ = 9, tỷ lệ 10.000/9 = 1.111 ∈ G1.
    _add(conn, "TST2", "2024-01-01", 19, 9.0, "kbs")
    _add(conn, "TST2", "2024-01-20", 1, 1.0, "kbs")
    _add(conn, "TST2", "2024-02-01", 10, 10_000.0, "kbs")  # giao thoa 1.000 vs 10.000 = lệch 90%
    rec = mod.analyse_symbol(conn, "TST2")
    assert rec["verdict"] == "HUY", rec
    assert "G2" in rec["reason"], rec
    assert conn.execute("SELECT SUM(is_stale) FROM daily_ohlcv").fetchone()[0] == 0


# [WHY] G1 — dải tỷ lệ: hai dải cách nhau <500× là biến động giá thật, KHÔNG phải sai đơn vị.
def test_g1_ty_le_ngoai_dai_thi_khong_ghi(conn):
    _add(conn, "TST3", "2024-01-01", 20, 1_000.0, "kbs")
    _add(conn, "TST3", "2024-02-01", 10, 8_000.0, "kbs")  # 8× — biến động thật
    rec = mod.analyse_symbol(conn, "TST3")
    assert rec["verdict"] != "RESCALE", rec
    assert "G1" in rec["reason"] or "liền mạch" in rec["reason"], rec


# [WHY] G3 — hậu kiểm bất biến cuối: sau rescale KHÔNG được còn bước nhảy ≥10×. Test ở tầng đơn vị vì
# qua đường công khai G1/G2 luôn bắt trước (đã đo: mọi fixture 3 dải đều trip G1 hoặc G2 trước G3) —
# guard vẫn phải đúng, vì nó là chốt chặn cuối nếu G1/G2 từng bị nới.
def test_g3_phat_hien_buoc_nhay_con_lai_sau_rescale():
    rows = [
        ("2024-01-01", 9.0, "kbs", 0),
        ("2024-01-02", 9_000.0, "kbs", 0),
        ("2024-01-03", 90_000.0, "kbs", 0),  # 9.000 → 90.000: cả hai đều ≥T nên G2 không soi
    ]
    T = 284.6
    leftovers = mod.leftover_jumps(rows, T)
    assert leftovers == [("2024-01-02", 10.0)], leftovers
    assert mod.leftover_jumps([("2024-01-01", 9.0, "kbs", 0), ("2024-01-02", 9_200.0, "kbs", 0)], 284.6) == []


# [WHY] Idempotent + chỉ đụng is_stale=0: cron/re-run phải là no-op, và dòng đã cách ly không bị sửa giá
# (giữ nguyên dấu vết để còn truy vết được vì sao nó bị cách ly).
def test_idempotent_va_khong_dung_dong_cach_ly(conn):
    _add(conn, "TST5", "2024-01-01", 20, 9.0, "kbs")
    _add(conn, "TST5", "2024-02-01", 10, 9_200.0, "kbs")
    _add(conn, "TST5", "2023-12-01", 5, 8.0, "kbs", stale=1)  # dòng đã cách ly nằm trong dải cũ
    rec = mod.analyse_symbol(conn, "TST5")
    assert rec["n_old_stale1"] == 5 and rec["n_old_stale0"] == 20
    assert mod.apply_symbol(conn, rec) == 20
    assert mod.apply_symbol(conn, rec) == 0, "chạy lại phải là no-op"
    assert conn.execute("SELECT close FROM daily_ohlcv WHERE date='2023-12-01'").fetchone()[0] == 8.0
