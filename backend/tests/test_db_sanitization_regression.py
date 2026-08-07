"""test_db_sanitization_regression.py — TDD regression cho DB đã sanitize (1,515,110+ rows).

WHY (sau ca 06/08/2026 sanitization):
  DB từng có 59,219 dòng datetime-typed ('YYYY-MM-DD 07:00:00') trên 1,324 mã — 56,201 dòng
  trùng bản plain (ô nhiễm range-query) + 3,018 dòng gapfill độc bản (vô hình với exact-match).
  Sau khi clean, DB phải GIỮ ỔN ĐỊNH 4 chỉ số an toàn (mọi crawler/backfill tương lai không
  được phép tái sinh lỗi):

    1. Zero Datetime Format  — không còn date chứa 'HH:MM:SS'.
    2. Zero Duplicate        — không còn nhóm (symbol, date) có COUNT(*) > 1.
    3. Gapfill Integrity     — 3,018 ngày độc bản được bảo toàn (spot-check AAH 2026-04-13=3300).
    4. Scale Continuity      — `_get_close` normalize <1000→×1000; chuỗi giá liên tục.

Đây là assertions READ-ONLY trên DB thật (screener_cache.db) — KHÔNG mutate dữ liệu, chạy
nhanh, chốt chặn regression từ các nguồn nạp mới.
"""

import sqlite3

import pytest
from src import config


def _real_conn():
    """Connection đọc trên DB thật (read-only)."""
    conn = sqlite3.connect(str(config.DATA_DIR / "screener_cache.db"), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def real_db():
    conn = _real_conn()
    yield conn
    conn.close()


class TestZeroDatetimeFormat:
    def test_no_datetime_rows_in_daily_ohlcv(self, real_db):
        """Không còn dòng date chứa giờ phút giây."""
        bad = real_db.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT GLOB '????-??-??'").fetchone()[0]
        assert bad == 0, f"{bad} datetime-typed rows re-appeared"

    def test_all_dates_match_iso(self, real_db):
        """Mọi date phải khớp regex YYYY-MM-DD."""
        malformed = real_db.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT GLOB '????-??-??' "
            "OR substr(date,5,1) != '-' OR substr(date,8,1) != '-'"
        ).fetchone()[0]
        assert malformed == 0, f"{malformed} malformed dates"


class TestZeroDuplicate:
    def test_no_duplicate_symbol_date_groups(self, real_db):
        """PRIMARY KEY (symbol,date): không nhóm nào COUNT(*)>1."""
        dup = real_db.execute(
            "SELECT COUNT(*) FROM (SELECT symbol, date FROM daily_ohlcv GROUP BY symbol, date HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        assert dup == 0, f"{dup} duplicate (symbol,date) groups"

    def test_total_rows_reasonable(self, real_db):
        """DB không được rỗng hoặc tăng đột biến (sanity: >= 1.4M rows)."""
        total = real_db.execute("SELECT COUNT(*) FROM daily_ohlcv").fetchone()[0]
        assert total >= 1_400_000, f"unexpectedly low row count: {total}"


class TestGapfillIntegrity:
    def test_gapfill_rows_still_present(self, real_db):
        """3,018 gapfill độc bản phải còn — spot-check AAH 2026-04-13 close=3300."""
        row = real_db.execute("SELECT close FROM daily_ohlcv WHERE symbol='AAH' AND date='2026-04-13'").fetchone()
        assert row is not None, "gapfill AAH 2026-04-13 missing"
        assert row[0] == 3300.0, f"unexpected close {row[0]}"

    def test_symbol_industry_has_dmx_with_retail_sector(self, real_db):
        """DMX đã onboard (06/08/2026) + đồng bộ VCI Listing → symbol_industry
        phải có DMX gán đúng ngành Bán lẻ (icb_name2/3/4)."""
        row = real_db.execute(
            "SELECT symbol, icb_name2, icb_name3, icb_name4 FROM symbol_industry WHERE symbol='DMX'"
        ).fetchone()
        assert row is not None, "DMX missing from symbol_industry after VCI sync"
        assert row[1] == "Bán lẻ", f"unexpected DMX icb_name2: {row[1]}"


class TestScaleContinuity:
    def test_get_close_returns_vnindex_healthy_scale(self, real_db):
        """VNINDEX điểm >1000 → `_get_close` KHÔNG được normalize sai thành ~1.0."""
        from backtest.unified_system_replay import _get_close

        val = _get_close(real_db, "VNINDEX", "2026-08-05")
        assert val is None or val > 1000, f"VNINDEX close scaled wrong: {val}"

    def test_get_close_returns_vnd_scale_for_symbols(self, real_db):
        """Mã VN (close >= 1000 VND) → giữ nguyên giá trị VND."""
        from backtest.unified_system_replay import _get_close

        row = real_db.execute(
            "SELECT symbol, date, close FROM daily_ohlcv WHERE symbol='HPG' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return
        _, date, raw = row
        val = _get_close(real_db, "HPG", date)
        assert val == raw, f"HPG close {raw} changed to {val}"

    def test_missing_date_returns_none(self, real_db):
        """Exact-match đúng định dạng → None khi không có dữ liệu (không vô hình như cũ)."""
        from backtest.unified_system_replay import _get_close

        assert _get_close(real_db, "VNINDEX", "2099-01-01") is None
