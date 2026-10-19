"""Test cửa đọc DUY NHẤT của `flow_forecast_history` — chặn đọc forecast cũ (Tầng 1).

[WHY]
  Đo 2026-10-19: `flow_forecast_history` dừng ở **2026-10-08**, thiếu 7 phiên (09→19/10) và **không có
  cron nào sinh bảng này**. Bảng này trước đó **không có consumer nào đọc** (grep toàn `backend/src` chỉ
  thấy engine TỰ GHI) ⇒ nguy cơ thật là: consumer đầu tiên sẽ đọc "dòng mới nhất" và áp số của 08/10
  cho phiên 19/10 mà không biết nó đã cũ 7 phiên (đúng Luật A: *không biết ≠ nhãn lành*).

[ROOT CAUSE]
  Không có API đọc nào kiểm tra độ tươi; bảng tồn tại như một khối dữ liệu "có gì đọc nấy".

[INVARIANT]
  1. Mọi consumer PHẢI đi qua `read_latest_forecast()` — cửa duy nhất, có kiểm tra độ trễ.
  2. Trễ **> 1 phiên** so với phiên liền trước `target_date` ⇒ `(None, "FLOW_FORECAST_STALE")`.
     Trễ đúng 1 phiên (forecast của phiên trước) vẫn dùng được — đó là nhịp vận hành bình thường.
  3. Bảng rỗng ⇒ `FLOW_FORECAST_MISSING`; không có lịch phiên ⇒ `FLOW_FORECAST_NO_CALENDAR` (fail-closed).
  4. Không file nào khác được `SELECT ... FROM flow_forecast_history` (test "một cửa").
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.capital_flow_forecasting_engine import (
    FLOW_FORECAST_MISSING,
    FLOW_FORECAST_NO_CALENDAR,
    FLOW_FORECAST_STALE,
    read_latest_forecast,
)

COLS = (
    "date, projected_regime, regime_confidence, regime_prob_trending, regime_prob_ranging,"
    " regime_prob_crisis, flow_velocity, rotation_velocity, flow_dispersion, projection_summary,"
    " sector_forecasts, leading_sectors, lagging_sectors"
)
SESSIONS = ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19")


@pytest.fixture()
def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL)")
    for d in SESSIONS:
        c.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX', ?, 1800, 1000)", (d,))
    c.execute(
        "CREATE TABLE flow_forecast_history ("
        "date TEXT PRIMARY KEY, projected_regime TEXT, regime_confidence TEXT,"
        " regime_prob_trending REAL, regime_prob_ranging REAL, regime_prob_crisis REAL,"
        " flow_velocity REAL, rotation_velocity REAL, flow_dispersion REAL,"
        " projection_summary TEXT, sector_forecasts TEXT, leading_sectors TEXT, lagging_sectors TEXT)"
    )
    return c


def _forecast(c: sqlite3.Connection, d: str, regime: str = "CRISIS") -> None:
    c.execute(
        f"INSERT INTO flow_forecast_history ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d, regime, "HIGH", 0.05, 0.09, 0.85, 1.0, 1.1, 0.9, "{}", "[]", "SEC", "CONSUMER"),
    )
    c.commit()


# [WHY] Ca thật của hôm nay: bảng dừng 08/10, target 19/10 ⇒ phải trả None + nhãn STALE. Nếu test này đỏ
# thì hệ thống lại có thể lấy forecast 7 phiên trước để phân bổ dòng tiền hôm nay.
def test_forecast_cu_7_phien_tra_none_va_nhan_stale(db):
    _forecast(db, "2026-10-08")
    rec, flag = read_latest_forecast(db, "2026-10-19")
    assert rec is None
    assert flag == FLOW_FORECAST_STALE


# [WHY] Nhịp vận hành bình thường: EOD 14:50 sinh forecast của phiên hôm nay; sáng hôm sau đọc bản của
# phiên trước là HỢP LỆ (trễ 1 phiên). Nếu chặn ca này thì mọi consumer sẽ luôn thấy None trước 14:50.
def test_forecast_tre_dung_1_phien_van_dung_duoc(db):
    _forecast(db, "2026-10-16")
    rec, flag = read_latest_forecast(db, "2026-10-19")
    assert flag is None
    assert rec is not None and rec["date"] == "2026-10-16"


# [WHY] Cùng phiên ⇒ dùng được, và các cột phải trả về đủ (consumer cần `sector_forecasts`).
def test_forecast_cung_phien_tra_day_du_cot(db):
    _forecast(db, "2026-10-19")
    rec, flag = read_latest_forecast(db, "2026-10-19")
    assert flag is None and rec["projected_regime"] == "CRISIS"
    assert set(rec) >= {"date", "projected_regime", "sector_forecasts", "leading_sectors"}


# [WHY] Bảng rỗng phải phân biệt với "cũ" — hai cơ chế mất phủ khác nhau, cấm gộp một nhãn mờ nghĩa.
def test_bang_rong_tra_missing(db):
    rec, flag = read_latest_forecast(db, "2026-10-19")
    assert rec is None and flag == FLOW_FORECAST_MISSING


# [WHY] Không có lịch phiên ⇒ không thể phán "trễ" hay "đủ" ⇒ fail-closed, không đoán.
def test_khong_co_lich_phien_tra_no_calendar():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL)")
    c.execute(
        "CREATE TABLE flow_forecast_history (date TEXT PRIMARY KEY, projected_regime TEXT,"
        " sector_forecasts TEXT, leading_sectors TEXT, lagging_sectors TEXT)"
    )
    rec, flag = read_latest_forecast(c, "2026-10-19")
    assert rec is None and flag == FLOW_FORECAST_NO_CALENDAR


# [WHY] Bất biến "MỘT CỬA": nếu một module khác tự SELECT bảng này, guard độ tươi bị đi vòng —
# đúng lớp lỗi "lưới cách ly chỉ mạnh bằng đường đọc yếu nhất" (đã gặp ở `valuation_engine`).
def test_chi_co_mot_cua_doc_flow_forecast():
    src_root = Path(__file__).resolve().parents[1] / "src"
    offenders = []
    for p in src_root.rglob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "FROM flow_forecast_history" in txt and p.name != "capital_flow_forecasting_engine.py":
            offenders.append(str(p.relative_to(src_root)))
    assert not offenders, f"đọc thẳng flow_forecast_history (bỏ qua guard): {offenders}"
