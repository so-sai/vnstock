# -*- coding: utf-8 -*-
"""Hợp đồng EOD ↔ độ rộng thị trường (NQ-14, 03/11/2026).

[WHY]
    `eod-run` là đường chạy EOD duy nhất của hệ thống, nhưng từ 23/10/2026 bảng
    `market_wide_breadth_daily` đóng băng 6 phiên liền (26/10 → 02/11) và
    `regime_history.is_degraded` thành NULL đúng 6 phiên đó. Hậu quả: mọi consumer
    lọc `is_degraded = 0` (14 điểm đọc LIVE/SIGNAL) lặng lẽ coi phiên đó là "không
    lành", còn `breadth_degraded_flag()` trả `None` mà không ai báo động.
    Bộ test này khoá lại hai hợp đồng của đường ghi bảng độ rộng:
      1. Sau khi EOD chạy, bảng độ rộng PHẢI có dòng của phiên mới nhất, và cờ suy
         giảm PHẢI là 0/1 (không bao giờ NULL) — trước khi `regime_history` được ghi.
      2. Bù dữ liệu KHÔNG được viết lại lịch sử đã có (provenance `created_at`).

[ROOT CAUSE]
    Bước ghi `market_wide_breadth_daily` chỉ tồn tại trong `daily_closer` (lệnh
    `close`), không nằm trong `run_post_update_engines()` mà `eod-run` gọi.

[INVARIANT]
    (a) Không ghi `regime_history` cho một phiên khi chưa đo được cờ suy giảm;
    (b) không ghi đè dòng breadth lịch sử (giữ nguyên `created_at` = provenance);
    (c) không ghi dòng nào ngoài cửa sổ `start..end` (buffer 10 ngày chỉ để tính prev).
"""

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

# ── Path Setup (đồng bộ với conftest.py) ────────────────────────────────────
PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


DATES = ("2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02")


def _seed_daily_ohlcv(db_path: Path, n_symbols: int, dates: tuple[str, ...] = DATES) -> None:
    """Seed daily_ohlcv tối thiểu nhưng HỢP LỆ theo VALID_ROW_SQL.

    [WHY] breadth module lọc `VALID_ROW_SQL` + `STOCK_ONLY_SQL`; nếu seed cẩu thả
    (close=0, low>close) thì số mã tụt dưới 750 và test sẽ đo nhầm cổng coverage
    thay vì đo hành vi ghi bảng.
    """
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS daily_ohlcv ("
        "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
        "adj_close REAL, volume REAL, source TEXT, is_stale INTEGER DEFAULT 0, UNIQUE(symbol, date))"
    )
    rows = []
    for i in range(n_symbols):
        sym = f"S{i:04d}"
        base = 10000.0 + i * 10.0
        for j, d in enumerate(dates):
            # nửa số mã tăng, nửa giảm mỗi phiên; biên độ nhỏ để không chạm trần/sàn
            step = 1 if (i + j) % 2 == 0 else -1
            close = base + step * 50.0 * j
            rows.append((sym, d, close, close, close, close, close, 1_000_000.0, "TEST", 0))
    con.executemany("INSERT OR REPLACE INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


@pytest.fixture()
def breadth_db(tmp_path, monkeypatch):
    """DB tạm + trỏ `market_wide_breadth.DB` sang đó (module đọc biến này lúc gọi)."""
    from src.analysis import market_wide_breadth as mwb

    db = tmp_path / "breadth_test.db"
    _seed_daily_ohlcv(db, n_symbols=760)
    monkeypatch.setattr(mwb, "DB", db)
    return db


# ===================================================================
# 1. Ghi bảng độ rộng: chỉ phần THIẾU + phiên mới nhất
# ===================================================================
def test_sync_missing_inserts_latest_and_preserves_history_provenance(breadth_db):
    """[WHY] Bù dữ liệu KHÔNG được phép viết lại lịch sử: dòng cũ phải giữ nguyên
    `created_at` (provenance), chỉ phiên thiếu + phiên mới nhất được ghi.
    Nếu ai đó "dọn dẹp" thành `backfill(toàn chuỗi)`, test này đỏ ngay.
    """
    from src.analysis.market_wide_breadth import sync_missing

    con = sqlite3.connect(str(breadth_db))
    con.execute(
        "CREATE TABLE market_wide_breadth_daily (date TEXT PRIMARY KEY, label TEXT NOT NULL, "
        "total_active INTEGER, advances INTEGER, declines INTEGER, unchanged INTEGER, ceiling INTEGER, "
        "floor INTEGER, breadth_pct REAL, ad_ratio REAL, degraded INTEGER, created_at TEXT)"
    )
    # dòng lịch sử đã có, mang mốc thời gian gốc
    con.execute(
        "INSERT INTO market_wide_breadth_daily VALUES "
        "('2026-10-28','ALL_EXCHANGES_UNWEIGHTED',760,1,1,758,0,0,0.0,1.0,0,'2026-01-01T00:00:00')"
    )
    con.commit()
    con.close()

    out = sync_missing(end="2026-11-02")

    assert out["end"] == "2026-11-02"
    assert out["inserted"] == 3, "chỉ 3 phiên thiếu (29/10, 30/10, 02/11)"
    con = sqlite3.connect(str(breadth_db))
    created_hist = con.execute(
        "SELECT created_at FROM market_wide_breadth_daily WHERE date='2026-10-28'"
    ).fetchone()[0]
    row = con.execute(
        "SELECT total_active, advances, declines, degraded FROM market_wide_breadth_daily WHERE date='2026-11-02'"
    ).fetchone()
    con.close()
    assert created_hist == "2026-01-01T00:00:00", "dòng lịch sử bị ghi đè ⇒ mất provenance"
    assert row is not None, "phiên mới nhất BẮT BUỘC phải có dòng"
    assert row[0] == 760
    assert row[3] in (0, 1), f"degraded phải là 0/1, nhận {row[3]!r}"


def test_degraded_flag_is_decision_ready_after_sync(breadth_db):
    """[WHY] Đây chính là lỗi (d): `breadth_degraded_flag` trả None vì bảng trống.
    Sau khi (b) được sửa, seam này phải trả 0/1 để `daily_updater` ghi được
    `regime_history.is_degraded` mà không rơi vào NULL.
    """
    from src.analysis.market_wide_breadth import sync_missing
    from src.database.series_quality import breadth_degraded_flag

    sync_missing(end="2026-11-02")

    con = sqlite3.connect(str(breadth_db))
    try:
        flag = breadth_degraded_flag(con, "2026-11-02")
    finally:
        con.close()
    assert flag in (0, 1), f"phải xác định (0/1), nhận {flag!r} — NULL = 'không biết'"


def test_sync_missing_fail_closed_when_coverage_below_threshold(tmp_path, monkeypatch):
    """[WHY] Chống vá quá tay: nếu phiên mới chỉ có 700 mã, module PHẢI ném
    PanelCoverageError và KHÔNG ghi dòng nào — thà thiếu còn hơn ghi độ rộng
    suy diễn từ panel khuyết (invariant Missing > Fake).
    """
    from src.analysis import market_wide_breadth as mwb
    from src.database.panel_quality import PanelCoverageError

    db = tmp_path / "thin.db"
    _seed_daily_ohlcv(db, n_symbols=700)
    monkeypatch.setattr(mwb, "DB", db)

    with pytest.raises(PanelCoverageError):
        mwb.sync_missing(end="2026-11-02")

    con = sqlite3.connect(str(db))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    n = con.execute("SELECT COUNT(*) FROM market_wide_breadth_daily").fetchone()[0] if (
        "market_wide_breadth_daily" in tables
    ) else 0
    con.close()
    assert n == 0, "cổng coverage ném lỗi thì không được để lại dòng nào"


def test_sync_missing_does_not_write_before_start_window(breadth_db):
    """[WHY] Bộ đệm 10 ngày trước `start` chỉ để có `prev`; nếu ghi ra ngoài cửa sổ thì
    bảng độ rộng bị mở rộng lịch sử ngoài ý muốn (dry-run 03/11/2026 lộ 7 ngày 2019).
    """
    from src.analysis.market_wide_breadth import sync_missing

    con = sqlite3.connect(str(breadth_db))
    con.executemany(
        "INSERT OR REPLACE INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(f"S{i:04d}", "2019-12-31", 9000.0, 9000.0, 9000.0, 9000.0, 9000.0, 1e6, "TEST", 0) for i in range(760)],
    )
    con.commit()
    con.close()

    out = sync_missing(start="2020-01-01", end="2026-11-02")
    assert out["end"] == "2026-11-02"

    con = sqlite3.connect(str(breadth_db))
    old = con.execute("SELECT COUNT(*) FROM market_wide_breadth_daily WHERE date < '2020-01-01'").fetchone()[0]
    con.close()
    assert old == 0, "không được ghi dòng ngoài cửa sổ start..end"


# ===================================================================
# 2. Hợp đồng pipeline: EOD phải refresh breadth TRƯỚC khi ghi regime
# ===================================================================
def test_eod_engine_chain_refreshes_breadth_before_regime_persist():
    """[WHY] Test cấu trúc (precedent: Mục 1 quét writer `regime_history`) vì
    `run_post_update_engines()` gọi >10 engine mạng/DB, không thể chạy trong
    unit test zero-network. Điều cần khoá là THỨ TỰ: nếu ai đảo thứ tự, cờ suy
    giảm lại NULL đúng như 6 phiên vừa rồi.
    """
    from src import daily_updater

    src = inspect.getsource(daily_updater.run_post_update_engines)
    assert "sync_missing" in src, "EOD phải gọi sync_missing (nguồn của market_wide_breadth_daily)"
    assert src.index("sync_missing") < src.index("detect_regime("), (
        "phải refresh breadth TRƯỚC khi detect_regime/persist regime_history"
    )


def test_regime_writer_refuses_null_degraded():
    """[WHY] `is_degraded = NULL` vừa xảy ra 6 phiên. Writer phải fail-closed
    (không ghi regime) thay vì ghi NULL — nếu ai bỏ guard, test này đỏ.
    """
    from src import daily_updater

    src = inspect.getsource(daily_updater.run_post_update_engines)
    assert "breadth_degraded_flag" in src
    assert "_flag is None" in src and "raise" in src, (
        "writer phải raise khi không đo được cờ suy giảm (không ghi NULL)"
    )


# ===================================================================
# 3. Hợp đồng pipeline: EOD phải refresh breadth TRƯỚC khi ghi regime
# ===================================================================
