"""test_catchup.py — Test Suite cho cơ chế Giao dịch bù (Catch-up Execution).

Trả lời câu hỏi khai thác sâu: khi chu kỳ EOD ngày T thất bại hoàn toàn (sập
mạng kéo dài), ngày T+1 phải TỰ ĐỘNG phát hiện ngày nợ & bù tuần tự, với
STALE-SIGNAL GUARD chống backfill bias.

Bao phủ:
  - eod_run_ledger: ghi/đọc trạng thái bền vững.
  - Gap Detection: phát hiện ngày nợ (chưa SUCCESS).
  - Sequential Backfill: bù đúng THỨ TỰ THỜI GIAN.
  - Stale-signal guard: trong cửa sổ → bù đầy đủ; quá cũ → chỉ MtM.
  - Anti-lookahead: bù ngày cũ dùng as_of=ngày đó (không giá tương lai).

Run: python -m pytest backend/tests/test_catchup.py -v
"""
import pytest

from conftest import TEST_PORTFOLIO, TEST_SYMBOL, TEST_DATES


@pytest.fixture
def seeded(seed_test_ohlcv):
    """OHLCV test đã seed + ledger sạch cho TEST_PORTFOLIO."""
    from src.database.db_core import get_connection
    from src.engine.eod_runner import _ensure_ledger_schema
    _ensure_ledger_schema()
    with get_connection() as conn:
        conn.execute("DELETE FROM eod_run_ledger WHERE portfolio_id=?",
                     (TEST_PORTFOLIO,))
        conn.commit()
    return seed_test_ohlcv


# ==================================================== LEDGER
class TestLedger:
    def test_record_and_read_status(self, seeded):
        from src.engine.eod_runner import (_record_ledger, _ledger_succeeded,
                                           STATUS_SUCCESS, STATUS_FAILED)
        d = TEST_DATES[0]
        assert _ledger_succeeded(d, TEST_PORTFOLIO) is False
        _record_ledger(d, TEST_PORTFOLIO, STATUS_SUCCESS)
        assert _ledger_succeeded(d, TEST_PORTFOLIO) is True

    def test_failed_status_not_counted_as_done(self, seeded):
        from src.engine.eod_runner import (_record_ledger, _ledger_succeeded,
                                           STATUS_FAILED)
        d = TEST_DATES[1]
        _record_ledger(d, TEST_PORTFOLIO, STATUS_FAILED, last_error="net down")
        assert _ledger_succeeded(d, TEST_PORTFOLIO) is False

    def test_upsert_updates_status(self, seeded):
        from src.database.db_core import get_connection
        from src.engine.eod_runner import (_record_ledger, STATUS_FAILED,
                                           STATUS_SUCCESS)
        d = TEST_DATES[2]
        _record_ledger(d, TEST_PORTFOLIO, STATUS_FAILED, attempts=3)
        _record_ledger(d, TEST_PORTFOLIO, STATUS_SUCCESS, attempts=1)
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT status, attempts FROM eod_run_ledger "
                "WHERE as_of_date=? AND portfolio_id=?",
                (d, TEST_PORTFOLIO)).fetchall()
        assert len(rows) == 1  # UPSERT, không nhân đôi
        assert rows[0][0] == STATUS_SUCCESS
        assert rows[0][1] == 1


# ==================================================== GAP DETECTION
class TestGapDetection:
    def test_detects_unprocessed_days_before_as_of(self, seeded):
        """Các phiên test TRƯỚC as_of chưa SUCCESS → đều là gap."""
        from src.engine.eod_runner import detect_gap_days
        as_of = TEST_DATES[3]
        gaps = detect_gap_days(as_of, TEST_PORTFOLIO, scan_limit=100)
        # 3 phiên test trước as_of phải nằm trong danh sách gap
        expected = set(TEST_DATES[:3])
        assert expected.issubset(set(gaps))
        # as_of và ngày sau KHÔNG được coi là gap
        assert as_of not in gaps
        assert TEST_DATES[4] not in gaps

    def test_gaps_sorted_ascending(self, seeded):
        """Ngày nợ trả về TĂNG DẦN (để bù đúng trình tự thời gian)."""
        from src.engine.eod_runner import detect_gap_days
        gaps = detect_gap_days(TEST_DATES[5], TEST_PORTFOLIO, scan_limit=100)
        test_gaps = [g for g in gaps if g in TEST_DATES]
        assert test_gaps == sorted(test_gaps)

    def test_succeeded_day_not_a_gap(self, seeded):
        """Ngày đã ghi SUCCESS trong ledger KHÔNG còn là gap."""
        from src.engine.eod_runner import (detect_gap_days, _record_ledger,
                                           STATUS_SUCCESS)
        d = TEST_DATES[1]
        _record_ledger(d, TEST_PORTFOLIO, STATUS_SUCCESS)
        gaps = detect_gap_days(TEST_DATES[4], TEST_PORTFOLIO, scan_limit=100)
        assert d not in gaps

    def test_legacy_trades_backfill_ledger(self, seeded):
        """Ngày có trade nhưng ledger trống → tự đồng bộ ledger, không là gap."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import (detect_gap_days, _ledger_succeeded)
        from src.engine.paper_trading_engine import PaperTradingEngine
        PaperTradingEngine(portfolio_id=TEST_PORTFOLIO)
        d = TEST_DATES[0]
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO paper_trades_log "
                "(trade_id, portfolio_id, decision_date, symbol, side, quantity, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (f"LEG_{d}", TEST_PORTFOLIO, d, TEST_SYMBOL, "BUY", 100,
                 "2099-01-01T00:00:00"))
            conn.commit()
        gaps = detect_gap_days(TEST_DATES[4], TEST_PORTFOLIO, scan_limit=100)
        assert d not in gaps
        # ledger đã được đồng bộ
        assert _ledger_succeeded(d, TEST_PORTFOLIO) is True


# ==================================================== STALE-SIGNAL GUARD
class TestStaleSignalGuard:
    def test_mtm_only_creates_no_orders(self, seeded):
        """mtm_only=True → KHÔNG sinh lệnh, vẫn ghi equity/summary."""
        from src.engine.paper_trading_engine import PaperTradingEngine
        eng = PaperTradingEngine(portfolio_id=TEST_PORTFOLIO)
        res = eng.generate_orders_from_signals(TEST_DATES[2], mtm_only=True)
        assert res["mtm_only"] is True
        assert res["orders"] == []
        assert "mtm" in res

    def test_recent_gap_full_catchup(self, seeded):
        """Ngày nợ TRONG cửa sổ MAX_CATCHUP_DAYS → CATCHUP_FULL."""
        from src.engine.eod_runner import (_catchup_gap_days, MAX_CATCHUP_DAYS)
        from src.database.db_core import get_connection
        # as_of = phiên cuối; các phiên ngay trước nằm trong cửa sổ tươi
        as_of = TEST_DATES[-1]
        report = _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        # Những ngày trong cửa sổ MAX_CATCHUP_DAYS phải được bù đầy đủ
        fresh = set(TEST_DATES[-(MAX_CATCHUP_DAYS + 1):-1])
        assert fresh.issubset(set(report["caught_up"]))

    def test_old_gap_mtm_only(self, seeded):
        """Ngày nợ QUÁ CŨ (ngoài cửa sổ) → CATCHUP_MTM_ONLY, không phát lệnh."""
        from src.engine.eod_runner import _catchup_gap_days, MAX_CATCHUP_DAYS
        as_of = TEST_DATES[-1]
        report = _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        # Phiên đầu tiên (xa nhất) phải nằm trong nhóm mtm_only
        oldest = TEST_DATES[0]
        # oldest ngoài cửa sổ tươi (danh sách test đủ dài > MAX_CATCHUP_DAYS+1)
        assert len(TEST_DATES) > MAX_CATCHUP_DAYS + 1
        assert oldest in report["mtm_only"]
        assert oldest not in report["caught_up"]

    def test_stale_day_writes_no_trades(self, seeded):
        """Ngày bù STALE tuyệt đối KHÔNG có trade nào được ghi."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import _catchup_gap_days
        as_of = TEST_DATES[-1]
        _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        oldest = TEST_DATES[0]
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM paper_trades_log "
                "WHERE portfolio_id=? AND decision_date=? AND is_rejected=0",
                (TEST_PORTFOLIO, oldest)).fetchone()[0]
        assert n == 0


# ==================================================== SEQUENTIAL & LEDGER STATE
class TestSequentialBackfill:
    def test_all_gaps_recorded_in_ledger(self, seeded):
        """Sau catch-up, mọi ngày nợ đều có bản ghi 'done' trong ledger."""
        from src.database.db_core import get_connection
        from src.engine.eod_runner import (_catchup_gap_days, detect_gap_days)
        as_of = TEST_DATES[-1]
        _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        # Không còn gap test nào sau khi bù
        remaining = [g for g in detect_gap_days(as_of, TEST_PORTFOLIO,
                                                scan_limit=100)
                     if g in TEST_DATES]
        assert remaining == []

    def test_catchup_idempotent_on_second_run(self, seeded):
        """Chạy catch-up lần 2 → không còn ngày nợ (không lặp lại việc bù)."""
        from src.engine.eod_runner import _catchup_gap_days
        as_of = TEST_DATES[-1]
        _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        report2 = _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)
        test_gaps = [g for g in report2["gap_days"] if g in TEST_DATES]
        assert test_gaps == []


# ==================================================== ANTI-LOOKAHEAD
class TestCatchupAntiLookahead:
    def test_full_catchup_uses_historical_price(self, seeded):
        """Bù đầy đủ ngày cũ phải khớp giá của CHÍNH ngày đó (không tương lai).

        seed_test_ohlcv: close tăng 100đ/phiên. Nếu backfill vô tình dùng giá
        as_of (ngày mới) thay vì giá ngày nợ → fill price sẽ cao hơn thực tế.
        """
        from src.database.db_core import get_connection
        from src.engine.eod_runner import _catchup_gap_days, MAX_CATCHUP_DAYS
        closes = seeded  # {date: close}
        as_of = TEST_DATES[-1]
        _catchup_gap_days(as_of, TEST_PORTFOLIO, offline=True)

        # Lấy 1 ngày được bù đầy đủ (trong cửa sổ tươi) có phát lệnh cho TEST_SYMBOL
        fresh_days = TEST_DATES[-(MAX_CATCHUP_DAYS + 1):-1]
        with get_connection() as conn:
            for d in fresh_days:
                row = conn.execute(
                    "SELECT paper_fill_price, decision_price FROM paper_trades_log "
                    "WHERE portfolio_id=? AND decision_date=? AND symbol=? "
                    "AND is_rejected=0",
                    (TEST_PORTFOLIO, d, TEST_SYMBOL)).fetchone()
                if row and row[1] is not None:
                    # decision_price phải ~ close của NGÀY ĐÓ, không phải as_of
                    assert abs(row[1] - closes[d]) < abs(row[1] - closes[as_of]) \
                        or row[1] == pytest.approx(closes[d], rel=0.05)
                    break
