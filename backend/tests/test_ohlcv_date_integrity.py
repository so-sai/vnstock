"""test_ohlcv_date_integrity.py — TDD regression cho lỗi datetime-typed rows trong daily_ohlcv.

WHY (Bug thật phát hiện 06/08/2026):
  Có 59,219 dòng daily_ohlcv lưu date dạng 'YYYY-MM-DD 07:00:00' (nguồn `kbs`), trên 1,324 mã,
  window 2025-12-03 → 2026-04-16. Hệ quả:
    1. `PRIMARY KEY (symbol, date)` xem '2026-04-08' và '2026-04-08 07:00:00' là 2 khóa KHÁC NHAU
       → 56,201 dòng datetime TRÙNG bản plain (giá trị ~1000×, vô hình với exact-match nhưng
       ô nhiễm range-query) + 3,018 dòng gapfill ĐỘC bản (AAH/AAM/AAS... chỉ tồn tại ở dạng
       datetime, vô hình với exact-match `_get_close`).
    2. Gốc rễ: `save_data_upsert` (db_core.py) chạy `df["date"].astype(str)` trên cột
       pandas datetime64[ns] → sinh chuỗi 'YYYY-MM-DD HH:MM:SS'.

Các test này chốt chặn 3 lớp:
  A. Write-time guard: save_data_upsert phải chuẩn hóa date về 'YYYY-MM-DD'.
  B. Scan-time guard: phát hiện + sanitize dòng datetime (giữ gapfill, xóa trùng).
  C. Read-time guard: `_get_close` normalize <1000 → ×1000 (scale liên tục).
"""

import sqlite3

import pandas as pd
from conftest import TEST_SYMBOL

DDL_DAILY_OHLCV = """
    CREATE TABLE IF NOT EXISTS daily_ohlcv (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, adj_close REAL,
        volume INTEGER CHECK(volume >= 0),
        source TEXT,
        PRIMARY KEY (symbol, date)
    )
"""


def _make_ohlcv_db(tmp_path):
    db = tmp_path / "ohlcv_test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(DDL_DAILY_OHLCV)
    conn.commit()
    return db, conn


def _row_dates(conn, symbol):
    return {r[0] for r in conn.execute("SELECT date FROM daily_ohlcv WHERE symbol=?", (symbol,))}


# ════════════════════════════════════════════════════════════════════════════
# LỚP A — Write-time guard: save_data_upsert chuẩn hóa date
# ════════════════════════════════════════════════════════════════════════════
class TestSaveDataUpsertDateNormalization:
    def test_datetime_column_written_as_date_only(self, tmp_path):
        """datetime64[ns] → phải lưu 'YYYY-MM-DD', KHÔNG 'YYYY-MM-DD 07:00:00'."""
        db, conn = _make_ohlcv_db(tmp_path)
        df = pd.DataFrame(
            {
                "symbol": [TEST_SYMBOL, TEST_SYMBOL],
                "date": pd.to_datetime(["2026-04-08 07:00:00", "2026-04-09 07:00:00"]),
                "open": [10000.0, 10100.0],
                "high": [10200.0, 10300.0],
                "low": [9900.0, 10000.0],
                "close": [10100.0, 10200.0],
                "adj_close": [10100.0, 10200.0],
                "volume": [1_000_000, 1_100_000],
                "source": ["kbs", "kbs"],
            }
        )
        from src.database.db_core import save_data_upsert

        save_data_upsert("daily_ohlcv", df, conn)

        dates = _row_dates(conn, TEST_SYMBOL)
        assert dates == {"2026-04-08", "2026-04-09"}, f"got {dates}"

    def test_plain_date_strings_unchanged(self, tmp_path):
        """Date đã đúng định dạng 'YYYY-MM-DD' → không đổi."""
        db, conn = _make_ohlcv_db(tmp_path)
        df = pd.DataFrame(
            {
                "symbol": [TEST_SYMBOL],
                "date": ["2026-04-08"],
                "open": [10000.0],
                "high": [10200.0],
                "low": [9900.0],
                "close": [10100.0],
                "adj_close": [10100.0],
                "volume": [1_000_000],
                "source": ["kbs"],
            }
        )
        from src.database.db_core import save_data_upsert

        save_data_upsert("daily_ohlcv", df, conn)

        assert _row_dates(conn, TEST_SYMBOL) == {"2026-04-08"}

    def test_no_datetime_rows_after_upsert(self, tmp_path):
        """Sau upsert với cột datetime, không còn dòng nào chứa khoảng trắng+giờ trong date."""
        db, conn = _make_ohlcv_db(tmp_path)
        df = pd.DataFrame(
            {
                "symbol": [TEST_SYMBOL],
                "date": pd.to_datetime(["2026-04-08 07:00:00"]),
                "open": [10000.0],
                "high": [10200.0],
                "low": [9900.0],
                "close": [10100.0],
                "adj_close": [10100.0],
                "volume": [1_000_000],
                "source": ["kbs"],
            }
        )
        from src.database.db_core import save_data_upsert

        save_data_upsert("daily_ohlcv", df, conn)

        bad = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT GLOB '????-??-??'").fetchone()[0]
        assert bad == 0


# ════════════════════════════════════════════════════════════════════════════
# LỚP B — Scan-time guard: sanitize dòng datetime (giữ gapfill, xóa trùng)
# ════════════════════════════════════════════════════════════════════════════
class TestSanitizeDatetimeRows:
    def test_detect_counts_datetime_rows(self, tmp_path):
        """detect_datetime_rows phải đếm đúng dòng date chứa giờ."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.commit()
        from src.database.data_integrity import detect_datetime_rows

        result = detect_datetime_rows(conn)
        assert result["count"] == 1

    def test_sanitize_removes_duplicate_keeps_plain(self, tmp_path):
        """Dòng datetime TRÙNG bản plain (cùng symbol,date sau cắt giờ) → bị xóa."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.commit()
        from src.database.data_integrity import sanitize_datetime_rows

        result = sanitize_datetime_rows(conn, dry_run=False)

        assert result["removed_duplicates"] == 1
        dates = _row_dates(conn, TEST_SYMBOL)
        assert dates == {"2026-04-08"}

    def test_sanitize_preserves_gapfill_dates(self, tmp_path):
        """Dòng datetime ĐỘC bản (gapfill, không có bản plain) → date được cắt giờ, dòng giữ."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-13 07:00:00", 3200.0, 3400.0, 3100.0, 3300.0, 3300.0, 50_000, "kbs"),
        )
        conn.commit()
        from src.database.data_integrity import sanitize_datetime_rows

        result = sanitize_datetime_rows(conn, dry_run=False)

        assert result["preserved_gapfills"] == 1
        dates = _row_dates(conn, TEST_SYMBOL)
        assert dates == {"2026-04-13"}
        close = conn.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?",
            (TEST_SYMBOL, "2026-04-13"),
        ).fetchone()[0]
        assert close == 3300.0

    def test_sanitize_no_datetime_rows_remaining(self, tmp_path):
        """Sau sanitize: 0 dòng datetime + 0 nhóm (symbol,date) trùng."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-13 07:00:00", 3200.0, 3400.0, 3100.0, 3300.0, 3300.0, 50_000, "kbs"),
        )
        conn.commit()
        from src.database.data_integrity import sanitize_datetime_rows

        sanitize_datetime_rows(conn, dry_run=False)

        bad = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT GLOB '????-??-??'").fetchone()[0]
        dup = conn.execute(
            "SELECT COUNT(*) FROM (  SELECT symbol, date FROM daily_ohlcv   GROUP BY symbol, date HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        assert bad == 0
        assert dup == 0

    def test_dry_run_makes_no_changes(self, tmp_path):
        """dry_run chỉ báo cáo, không sửa DB."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
        )
        conn.commit()
        from src.database.data_integrity import sanitize_datetime_rows

        result = sanitize_datetime_rows(conn, dry_run=True)

        assert result["dry_run"] is True
        assert _row_dates(conn, TEST_SYMBOL) == {"2026-04-08 07:00:00"}


# ════════════════════════════════════════════════════════════════════════════
# LỚP C — Read-time guard: `_get_close` normalize scale <1000 → ×1000
# ════════════════════════════════════════════════════════════════════════════
class TestGetCloseScaleNormalization:
    def test_sub1000_close_scaled_to_vnd(self, tmp_path):
        """close=3300 -> giữ nguyên; close<1000 (per-1000-share) → ×1000."""
        db, conn = _make_ohlcv_db(tmp_path)
        for d, c in [("2026-04-08", 10100.0), ("2026-04-09", 9.5), ("2026-04-10", 3300.0)]:
            conn.execute(
                "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
                (TEST_SYMBOL, d, c, c + 100, c - 100, c, c, 1_000_000, "kbs"),
            )
        conn.commit()
        from backtest.unified_system_replay import _get_close

        assert _get_close(conn, TEST_SYMBOL, "2026-04-08") == 10100.0
        assert _get_close(conn, TEST_SYMBOL, "2026-04-09") == 9500.0
        assert _get_close(conn, TEST_SYMBOL, "2026-04-10") == 3300.0

    def test_missing_date_returns_none(self, tmp_path):
        """Exact-match date đúng định dạng → None nếu không có (gapfill trước đây vô hình)."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            (TEST_SYMBOL, "2026-04-13", 3200.0, 3400.0, 3100.0, 3300.0, 3300.0, 50_000, "kbs"),
        )
        conn.commit()
        from backtest.unified_system_replay import _get_close

        assert _get_close(conn, TEST_SYMBOL, "2026-04-13") == 3300.0
        assert _get_close(conn, TEST_SYMBOL, "2026-04-14") is None
