"""test_backfill_guards.py — TDD cho history-path backfill (P0 2026-10-02).

WHY (silent data poisoning 30/09–01/10/2026): update_market_batch() dùng
price_board() — snapshot real-time không date param — rồi stamp
`date = target_date`. Cron 16:00 (target=today) hên nên đúng; backfill
--date quá khứ dán snapshot live vào ngày cũ → 902/903 mã có
(close, volume) giống hệt nhau qua 30/09↔01/10, PNJ "30/09" = −19.4%
vượt band ±7%. CLI vẫn SUCCESS → không guard nào bắn.

Contract backfill mới (src/services/market_backfill.py):
  G1 DATE_MATCH   — chỉ giữ row có date == target_date.
  G2 BAND_GUARD   — |close/prev_close − 1| > band theo sàn → reject.
  G3 DUP_CANARY   — >90% mã close y hệt phiên trước (n≥50) +
                    VNINDEX dịch chuyển → CRITICAL, không ghi gì.
  STRUCTURAL      — update_market_batch từ chối target != hôm nay
                    (price_board không date-aware → BACKFILL_BLOCKED).
"""

import sqlite3

import pandas as pd
import pytest

from src.services.market_backfill import (
    backfill_market_history,
    normalize_history_df,
)

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
DDL_IPO = """
    CREATE TABLE IF NOT EXISTS ipo_calendar (
        symbol TEXT, listing_date TEXT, exchange TEXT
    )
"""

TARGET = "2026-09-30"
PREV = "2026-09-29"


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "backfill_guards.db"))
    conn.executescript(DDL_DAILY_OHLCV)
    conn.executescript(DDL_IPO)
    conn.commit()
    return conn


def _seed(conn, symbol, date, close, vol=1_000_000, is_stale=0):
    conn.execute(
        "INSERT OR REPLACE INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
        (symbol, date, close, close, close, close, close, vol, "seed", is_stale),
    )
    conn.commit()


def _kbs_row(time_str, close, volume, symbol=None):
    df = pd.DataFrame(
        {
            "time": [pd.Timestamp(time_str)],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [volume],
        }
    )
    return df


class FakeProvider:
    def __init__(self, rows=None, index=None):
        self.rows = rows or {}
        self.index = index
        self.calls = []

    def history(self, symbol, start=None, end=None, **kwargs):
        self.calls.append((symbol, start, end))
        if symbol == "VNINDEX":
            return self.index
        return self.rows.get(symbol)


# ════════════════════════════════════════════════════════════════════════════
# G1 — DATE_MATCH: snapshot trả sai ngày → đứng ngoài
# ════════════════════════════════════════════════════════════════════════════
class TestG1DateMatch:
    def test_wrong_date_returns_none(self):
        raw = _kbs_row("2026-10-01 07:00:00", 20.15, 13_350_300)
        assert normalize_history_df(raw, "HPG", TARGET) is None

    def test_right_date_normalized(self):
        raw = _kbs_row("2026-09-30 07:00:00", 20.15, 13_350_300)
        out = normalize_history_df(raw, "HPG", TARGET)
        assert out is not None
        assert list(out["date"]) == [TARGET]
        assert list(out["symbol"]) == ["HPG"]
        assert list(out["source"]) == ["kbs"]
        assert float(out.iloc[0]["adj_close"]) == pytest.approx(20.15)

    def test_empty_and_none_returns_none(self):
        assert normalize_history_df(None, "HPG", TARGET) is None
        assert normalize_history_df(pd.DataFrame(), "HPG", TARGET) is None


# ════════════════════════════════════════════════════════════════════════════
# G2 — BAND_GUARD: vượt band theo sàn → reject (missing > fake)
# ════════════════════════════════════════════════════════════════════════════
class TestG2BandGuard:
    @pytest.fixture()
    def conn(self, tmp_path):
        c = _db(tmp_path)
        _seed(c, "HPG", "2026-09-28", 20_200.0)
        _seed(c, "HPG", PREV, 20_300.0)
        c.execute(
            "INSERT INTO ipo_calendar VALUES ('HPG', '2011-01-01', 'HOSE')"
        )
        c.commit()
        yield c
        c.close()

    def test_out_of_band_rejected_no_write(self, conn):
        provider = FakeProvider(rows={"HPG": _kbs_row(TARGET, 18.0, 1_000_000)})
        success, failed, skipped = backfill_market_history(
            ["HPG"], TARGET, provider=provider, conn_factory=lambda: conn
        )
        assert success == 0
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE date=?", (TARGET,)
        ).fetchone()[0]
        assert n == 0

    def test_in_band_accepted_scaled_vnd_and_stale_reset(self, conn):
        _seed(conn, "HPG", TARGET, 20_050.0, is_stale=1)
        provider = FakeProvider(rows={"HPG": _kbs_row(TARGET, 20.15, 13_350_300)})
        success, failed, skipped = backfill_market_history(
            ["HPG"], TARGET, provider=provider, conn_factory=lambda: conn
        )
        assert success == 1
        row = conn.execute(
            "SELECT close, volume, is_stale FROM daily_ohlcv "
            "WHERE symbol='HPG' AND date=?",
            (TARGET,),
        ).fetchone()
        assert float(row[0]) == pytest.approx(20_150.0)
        assert int(row[1]) == 13_350_300
        assert int(row[2]) == 0


# ════════════════════════════════════════════════════════════════════════════
# G3 — DUP_CANARY: >90% mã y hệt phiên trước + VNINDEX dịch chuyển → abort
# ════════════════════════════════════════════════════════════════════════════
class TestG3DupCanary:
    def test_identical_day_with_moving_index_aborts(self, tmp_path):
        conn = _db(tmp_path)
        syms = [f"F{i:03d}" for i in range(1, 61)]
        for s in syms:
            _seed(conn, s, PREV, 10_000.0, vol=1_000_000)
        _seed(conn, "VNINDEX", "2026-09-28", 1_780.0)
        _seed(conn, "VNINDEX", PREV, 1_777.73)
        rows = {s: _kbs_row(TARGET, 10.0, 1_000_000) for s in syms}
        index = _kbs_row(TARGET, 1_760.0, 500_000_000)
        provider = FakeProvider(rows=rows, index=index)

        success, failed, skipped = backfill_market_history(
            syms, TARGET, provider=provider, conn_factory=lambda: conn
        )
        assert success == 0
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE date=?", (TARGET,)
        ).fetchone()[0]
        assert n == 0
        conn.close()


# ════════════════════════════════════════════════════════════════════════════
# STRUCTURAL — price_board path BỊ CHẶN cho target != hôm nay
# ════════════════════════════════════════════════════════════════════════════
class TestStructuralBlock:
    def test_update_market_batch_refuses_past_date(self, monkeypatch):
        from src.providers import vnstock_provider

        class _Boom:
            def __init__(self, *args, **kwargs):
                raise AssertionError(
                    "price_board/VnstockProvider must NOT be constructed "
                    "for a past target_date (BACKFILL_BLOCKED)"
                )

        monkeypatch.setattr(vnstock_provider, "VnstockProvider", _Boom)
        from src import daily_updater

        result = daily_updater.update_market_batch(["HPG"], TARGET, armor=None)
        assert result == (0, 1, 0)
