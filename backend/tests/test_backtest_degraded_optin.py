"""Hợp đồng 2 tầng cho reader backtest/nghiên cứu (quyết định 2026-10-19).

QUYẾT ĐỊNH (user phê chuẩn):
  • Tầng LIVE/SIGNAL: BẮT BUỘC lọc 100% `is_degraded = 0` (14 điểm đã vá ở `fc038ca`).
  • Tầng BACKTEST/REPLAY: **KHÔNG lọc cứng mặc định** — phải tái lập được baseline lịch sử đã công bố;
    chỉ lọc khi người dùng chủ động xin (`exclude_degraded=True`) cho phân tích độ nhạy / stress-test.
  • Không ai được lật cờ ÂM THẦM: mặc định phải là `False`, và test này canh đúng điều đó.
"""

from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import src.backtest.decompose_replay_by_regime as dr
import src.backtest.trend_loop as tl
from src.database.series_quality import LIVE_REGIME_SQL, regime_reader_sql


def test_vi_tu_helper_dung_hai_che_do():
    assert regime_reader_sql() == "", "mặc định KHÔNG được lọc (tái lập lịch sử)"
    assert regime_reader_sql(False) == ""
    assert LIVE_REGIME_SQL in regime_reader_sql(True)
    assert regime_reader_sql(True).strip().startswith("AND")


def test_load_regime_mac_dinh_KHONG_loc_va_bat_duoc_cong_tac():
    sig = inspect.signature(tl.load_regime)
    assert "exclude_degraded" in sig.parameters, "thiếu tham số opt-in"
    assert sig.parameters["exclude_degraded"].default is False, "mặc định phải là False (không lật âm thầm)"


def test_load_regimes_mac_dinh_KHONG_loc():
    sig = inspect.signature(dr.load_regimes)
    assert sig.parameters["exclude_degraded"].default is False


def test_kernel_HSR_co_tham_so_va_mac_dinh_False():
    import src.backtest.hsr_kernel as hk

    found = [
        cls
        for cls in vars(hk).values()
        if isinstance(cls, type) and "exclude_degraded" in inspect.signature(cls.__init__).parameters
    ]
    assert found, "không có lớp kernel nào nhận `exclude_degraded`"
    for cls in found:
        assert inspect.signature(cls.__init__).parameters["exclude_degraded"].default is False


def _tmp_store(path: Path) -> Path:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE regime_history (date TEXT, status TEXT, is_degraded INTEGER)")
    con.executemany(
        "INSERT INTO regime_history VALUES (?,?,?)",
        [("2026-01-02", "RANGING", 0), ("2026-01-05", "CRISIS", 1), ("2026-01-06", "RANGING", 0)],
    )
    con.commit()
    con.close()
    return path


def test_trend_loop_doc_du_3_phien_mac_dinh_va_2_khi_opt_in(tmp_path, monkeypatch):
    db = _tmp_store(tmp_path / "screener_cache.db")
    monkeypatch.setattr(tl, "DB", db)
    assert len(tl.load_regime("2026-01-01", "2026-01-31")) == 3, "mặc định phải thấy CẢ phiên degraded"
    giu_lai = tl.load_regime("2026-01-01", "2026-01-31", exclude_degraded=True)
    assert len(giu_lai) == 2 and "2026-01-05" not in giu_lai, giu_lai


def test_decompose_doc_du_mac_dinh_va_loc_khi_opt_in(tmp_path, monkeypatch):
    _tmp_store(tmp_path / "screener_cache.db")
    monkeypatch.setattr(dr, "DATA_DIR", tmp_path)
    assert len(dr.load_regimes()) == 3
    assert len(dr.load_regimes(exclude_degraded=True)) == 2
