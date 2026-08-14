"""test_ingestion_scale_guard.py — TDD cho Ingestion Scale Guard tại biên nạp OHLCV.

WHY (Bug thật đợt 2, 2026-08-14): 8 mã UNIVERSE (HPG/SSI/MBB/STB/TCB/VIB/MWG/VNM)
có TOÀN BỘ lịch sử max(close)<1000 nên migration đợt 1 (điều kiện max>=1000) bỏ sót;
nguồn KBS/VCI trả "nghìn đồng" (HPG=22.1) được lưu nguyên như penny. Guard đặt tại
`save_data_upsert` (db_core.py) chặn tại biên nạp — Data Contract VND-scale bắt buộc.

Quy tắc bất biến (Ingestion Invariant):
    Tự động ×1000 ⟺ (close < 1000) ∧ (
        symbol ∈ ACTIVE_UNIVERSE | DB-history≥1000 | ADV_20d ≥ 50k )
    KNOWN_PENNY (toàn bộ lịch sử <1000) + ADV<50k → KHÔNG nhân.

Chống False Positive: không nhân nhầm penny 800→800,000.
Chống False Negative: trụ thị giá <100k vẫn bị bắt (UNIVERSE là lớp quy tắc cứng).
"""

import sqlite3

import pandas as pd
import pytest

from database import db_core

DDL_DAILY_OHLCV = """
    CREATE TABLE IF NOT EXISTS daily_ohlcv (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, adj_close REAL,
        volume INTEGER CHECK(volume >= 0),
        source TEXT,
        is_stale INTEGER DEFAULT 0,
        PRIMARY KEY (symbol, date)
    )
"""


def _make_ohlcv_db(tmp_path):
    db = tmp_path / "ohlcv_scale_test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(DDL_DAILY_OHLCV)
    conn.commit()
    return db, conn


def _seed_history(conn, symbol, closes, vol=1_000_000):
    for i, c in enumerate(closes):
        d = f"2026-01-{i + 1:02d}"
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol, d, c, c + 100, c - 100, c, c, vol, "seed", 0),
        )
    conn.commit()


def _ohlcv_df(symbol, dates, closes, vols=None):
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(dates),
            "date": dates,
            "open": closes,
            "high": [c + 10 for c in closes],
            "low": [c - 10 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": vols or [1_000_000] * len(dates),
            "source": ["kbs"] * len(dates),
        }
    )


# ════════════════════════════════════════════════════════════════════════════
# INVARIANT — UNIVERSE là lớp quy tắc cứng (False Negative guard)
# ════════════════════════════════════════════════════════════════════════════
class TestUniverseHardRule:
    def test_universe_symbol_thousand_scale_scaled_to_vnd(self, tmp_path):
        """HPG nguồn trả nghìn đồng (close=22.05) → DB history VND → ×1000."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "HPG", [22000.0, 22100.0])  # DB history VND-scale
        df = _ohlcv_df("HPG", ["2026-08-14"], [22.05])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(22050.0)
        assert out["open"].iloc[0] == pytest.approx(22050.0)
        assert out["high"].iloc[0] == pytest.approx(32050.0)

    def test_universe_symbol_vnd_input_untouched(self, tmp_path):
        """close đã đúng VND (22050) → KHÔNG nhân lần nữa."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "TCB", [32000.0])
        df = _ohlcv_df("TCB", ["2026-08-14"], [32000.0])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(32000.0)


# ════════════════════════════════════════════════════════════════════════════
# INVARIANT — DB history VND-scale (chống False Negative cho mã mới)
# ════════════════════════════════════════════════════════════════════════════
class TestDbHistoryRule:
    def test_db_history_vnd_scales_new_thousand_input(self, tmp_path):
        """Mã có history VND (close 33,600) nhận dữ liệu nghìn đồng (33.6) → ×1000."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "STB", [33600.0])
        df = _ohlcv_df("STB", ["2026-08-14"], [33.6])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(33600.0)


# ════════════════════════════════════════════════════════════════════════════
# INVARIANT — KNOWN_PENNY không bị nhân (False Positive guard)
# ════════════════════════════════════════════════════════════════════════════
class TestKnownPennyNoScale:
    def test_penny_full_history_below_1000_untouched(self, tmp_path):
        """Mã toàn lịch sử <1000 (penny thật 800 VND) + ADV thấp → KHÔNG nhân."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "A32", [700.0, 750.0], vol=100)  # ADV~100 cp
        df = _ohlcv_df("A32", ["2026-08-14"], [800.0], vols=[100])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(800.0)

    def test_penny_high_volume_still_untouched(self, tmp_path):
        """Penny 500 VND nhưng volume cao (ACM: 229k cp) → KHÔNG nhân (giá trị thật)."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "ACM", [400.0, 500.0], vol=229_500)
        df = _ohlcv_df("ACM", ["2026-08-14"], [500.0], vols=[229_500])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(500.0)

    def test_high_price_low_volume_not_scaled(self, tmp_path):
        """Close≥1000 nhưng volume mỏng (A32: 100 cp, giá 30k) → thiếu provenance → KHÔNG nhân."""
        _, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "A32", [29000.0, 29700.0], vol=200)
        df = _ohlcv_df("A32", ["2026-08-14"], [5.0], vols=[200])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(5.0)


# ════════════════════════════════════════════════════════════════════════════
# INVARIANT — ADV_20d ≥ 50k (liquidity ≠ penny)
# ════════════════════════════════════════════════════════════════════════════
class TestAdvLiquidityRule:
    def test_unknown_symbol_high_adv_scaled(self, tmp_path):
        """Mã không rõ nhưng ADV20 khổng lồ (15M cp) → không thể penny → ×1000."""
        _, conn = _make_ohlcv_db(tmp_path)  # không seed history
        df = _ohlcv_df("ZZZZ", ["2026-08-14"], [30.0], vols=[15_000_000])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(30000.0)

    def test_unknown_symbol_low_adv_untouched(self, tmp_path):
        """Mã không rõ + ADV thấp → thận trọng, KHÔNG nhân (thiếu provenance)."""
        _, conn = _make_ohlcv_db(tmp_path)
        df = _ohlcv_df("QXXX", ["2026-08-14"], [900.0], vols=[1_000])
        out = db_core._ingestion_scale_guard(df, conn)
        assert out["close"].iloc[0] == pytest.approx(900.0)


# ════════════════════════════════════════════════════════════════════════════
# WRITE-TIME INTEGRATION — guard gắn trong save_data_upsert
# ════════════════════════════════════════════════════════════════════════════
class TestSaveDataUpsertIntegration:
    def test_upsert_writes_vnd_scale(self, tmp_path):
        """save_data_upsert('daily_ohlcv') phải ghi VND-scale, không ghi nghìn đồng."""
        db, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "MBB", [20000.0])
        df = _ohlcv_df("MBB", ["2026-08-14"], [20.0])
        db_core.save_data_upsert("daily_ohlcv", df, conn)
        row = conn.execute(
            "SELECT close, open, high, low, adj_close FROM daily_ohlcv WHERE symbol='MBB' AND date='2026-08-14'"
        ).fetchone()
        assert row[0] == pytest.approx(20000.0)
        assert row[1] == pytest.approx(20000.0)
        assert row[2] == pytest.approx(30000.0)
        assert row[3] == pytest.approx(10000.0)
        assert row[4] == pytest.approx(20000.0)

    def test_upsert_penny_untouched(self, tmp_path):
        """save_data_upsert KHÔNG nhân penny thật (close 500 VND)."""
        db, conn = _make_ohlcv_db(tmp_path)
        _seed_history(conn, "QST", [450.0], vol=500)
        df = _ohlcv_df("QST", ["2026-08-14"], [500.0], vols=[500])
        db_core.save_data_upsert("daily_ohlcv", df, conn)
        row = conn.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol='QST' AND date='2026-08-14'"
        ).fetchone()
        assert row[0] == pytest.approx(500.0)

    def test_non_ohlcv_table_not_touched(self, tmp_path):
        """Guard chỉ áp dụng cho daily_ohlcv — macro_history không bị nhân."""
        db, conn = _make_ohlcv_db(tmp_path)
        conn.execute("CREATE TABLE IF NOT EXISTS macro_history (variable TEXT, date TEXT, value REAL)")
        conn.commit()
        df = pd.DataFrame({"variable": ["VGB10Y"], "date": ["2026-08-14"], "value": [1.5]})
        db_core.save_data_upsert("macro_history", df, conn)
        row = conn.execute("SELECT value FROM macro_history WHERE variable='VGB10Y'").fetchone()
        assert row[0] == pytest.approx(1.5)
