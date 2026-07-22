"""test_catchup_execution.py — Catch-up Execution Rule (Transpose + Kill-switch).

Hiệu chỉnh quyết liệt về định tuyến lệnh bù: "Tín hiệu thuộc về quá khứ, nhưng
thanh khoản phải thuộc về hiện tại". Kiểm chứng:

  - Enqueue: lệnh bù nạp hàng đợi bền vững, KHÔNG khớp giá lịch sử, KHÔNG khóa tiền.
  - Transpose: ép khớp tại open_{T+k} (giá phục hồi), không phải close_T.
  - Kill-switch: decay > 3% → REJECTED_SIGNAL_DECAY (không truy giá).
  - Sizing reconciliation: qty tính lại theo open_{T+k} + buying_power hiện tại.
  - Cash consistency: KHÔNG cần reclaim — lệnh hủy không giam tiền; lệnh khớp
    trừ settled_cash → buying_power tự nhất quán.
  - Idempotency: chạy lại process_catchup_queue không khớp lại lệnh đã FILLED.

Run: python -m pytest backend/tests/test_catchup_execution.py -v
"""
import pytest

from conftest import TEST_PORTFOLIO, TEST_SYMBOL, TEST_DATES


def _insert_ohlcv(symbol, date, open_, close):
    """Chèn 1 bar OHLCV test với open/close chỉ định."""
    from src.database.db_core import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_ohlcv "
            "(symbol, date, open, high, low, close, adj_close, volume, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (symbol, date, open_, max(open_, close) + 100, min(open_, close) - 100,
             close, close, 5_000_000, "TEST"))
        conn.commit()


@pytest.fixture
def engine(seed_test_ohlcv):
    """PaperTradingEngine cách ly + queue sạch."""
    from src.engine.paper_trading_engine import PaperTradingEngine
    from src.database.db_core import get_connection
    eng = PaperTradingEngine(portfolio_id=TEST_PORTFOLIO,
                             initial_capital=1_000_000_000.0)
    with get_connection() as conn:
        conn.execute("DELETE FROM paper_catchup_queue WHERE portfolio_id=?",
                     (TEST_PORTFOLIO,))
        conn.commit()
    return eng


# ==================================================== ENQUEUE (no reservation)
class TestEnqueue:
    def test_enqueue_creates_pending_row(self, engine):
        enq = engine._enqueue_catchup_order(
            TEST_SYMBOL, "BUY", 1000, 10000.0, TEST_DATES[0])
        assert enq["status"] == "QUEUED_CATCHUP"
        from src.database.db_core import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT status, target_qty, target_price FROM paper_catchup_queue "
                "WHERE trade_id=?", (enq["trade_id"],)).fetchone()
        assert row[0] == "PENDING"
        assert row[1] == 1000
        assert row[2] == pytest.approx(10000.0)

    def test_enqueue_does_not_lock_cash(self, engine):
        """Nạp hàng đợi KHÔNG trừ settled_cash, KHÔNG giảm buying_power."""
        bp_before = engine.mtm.get_buying_power(TEST_DATES[0], 0.0)
        cash_before = engine.mtm._get_state()["settled_cash"]
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0,
                                      TEST_DATES[0])
        bp_after = engine.mtm.get_buying_power(TEST_DATES[0], 0.0)
        cash_after = engine.mtm._get_state()["settled_cash"]
        assert bp_after == pytest.approx(bp_before)  # KHÔNG khóa sức mua
        assert cash_after == pytest.approx(cash_before)

    def test_enqueue_idempotent(self, engine):
        """Nạp cùng (date,symbol,side) 2 lần → chỉ 1 hàng (PK idempotent)."""
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0,
                                      TEST_DATES[0])
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 999, 10500.0,
                                      TEST_DATES[0])
        from src.database.db_core import get_connection
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM paper_catchup_queue WHERE portfolio_id=? "
                "AND decision_date=? AND symbol=?",
                (TEST_PORTFOLIO, TEST_DATES[0], TEST_SYMBOL)).fetchone()[0]
        assert n == 1


# ==================================================== TRANSPOSE
class TestPriceTransposition:
    def test_fill_at_open_of_recovery_day(self, engine):
        """Lệnh bù khớp @ open_{T+k}, KHÔNG phải close_T (chống lookback)."""
        # T = DATES[0], close_T seeded = 10000. Recovery = DATES[2].
        T = TEST_DATES[0]
        recovery = TEST_DATES[2]
        close_T = 10000.0
        open_recovery = 10150.0  # +1.5% — trong ngưỡng 3%
        _insert_ohlcv(TEST_SYMBOL, recovery, open_recovery, 10200.0)
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, close_T, T)

        rep = engine.process_catchup_queue(recovery)
        assert rep["processed"] == 1
        assert len(rep["filled"]) == 1
        f = rep["filled"][0]
        # Giá khớp = open ngày phục hồi, KHÔNG phải close_T
        assert f["exec_price"] == pytest.approx(open_recovery)
        assert f["exec_price"] != pytest.approx(close_T)

    def test_lot_created_at_transposed_price(self, engine):
        """Cost basis của lot phản ánh open_{T+k} (đã gồm phí), không close_T."""
        from src.database.db_core import get_connection
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 10150.0, 10200.0)
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        engine.process_catchup_queue(recovery)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT cost_basis, open_date FROM paper_lots "
                "WHERE portfolio_id=? AND symbol=? AND is_closed=0",
                (TEST_PORTFOLIO, TEST_SYMBOL)).fetchone()
        assert row is not None
        # cost_basis ~ open_recovery * (1 + fee) ≈ 10150 * 1.0015
        assert row[0] == pytest.approx(10150.0 * 1.0015, rel=1e-3)
        assert row[1] == recovery  # fill_date = ngày phục hồi


# ==================================================== KILL-SWITCH
class TestSlippageKillSwitch:
    def test_decay_above_threshold_rejected(self, engine):
        """open_{T+k} lệch > 3% so với close_T → REJECTED_SIGNAL_DECAY."""
        from src.database.db_core import get_connection
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        close_T = 10000.0
        _insert_ohlcv(TEST_SYMBOL, recovery, 10400.0, 10500.0)  # +4% > 3%
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, close_T, T)
        rep = engine.process_catchup_queue(recovery)
        assert len(rep["rejected_decay"]) == 1
        assert rep["rejected_decay"][0]["decay_pct"] == pytest.approx(4.0, abs=0.01)
        with get_connection() as conn:
            st = conn.execute(
                "SELECT status FROM paper_catchup_queue WHERE portfolio_id=? "
                "AND decision_date=?", (TEST_PORTFOLIO, T)).fetchone()
        assert st[0] == "REJECTED_SIGNAL_DECAY"

    def test_decay_within_threshold_filled(self, engine):
        """Lệch đúng dưới ngưỡng 3% (vd 2.9%) → vẫn khớp."""
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 10290.0, 10300.0)  # +2.9%
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        rep = engine.process_catchup_queue(recovery)
        assert len(rep["filled"]) == 1
        assert rep["rejected_decay"] == []

    def test_kill_switch_no_cash_movement(self, engine):
        """Lệnh bị KILL-SWITCH KHÔNG làm dịch chuyển tiền (không cần reclaim)."""
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 11000.0, 11000.0)  # +10% > 3%
        cash_before = engine.mtm._get_state()["settled_cash"]
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        engine.process_catchup_queue(recovery)
        cash_after = engine.mtm._get_state()["settled_cash"]
        # Tiền nguyên vẹn — lệnh treo chưa từng khóa tiền → không có gì để reclaim
        assert cash_after == pytest.approx(cash_before)


# ============================================ CASH CONSISTENCY (deep-dive answer)
class TestCashConsistency:
    def test_filled_order_reduces_buying_power(self, engine):
        """Lệnh bù KHỚP trừ settled_cash → buying_power ngày T+k tự giảm đúng."""
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 10100.0, 10200.0)
        bp_before = engine.mtm.get_buying_power(recovery, 0.0)
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        rep = engine.process_catchup_queue(recovery)
        filled_qty = rep["filled"][0]["qty"]
        bp_after = engine.mtm.get_buying_power(recovery, 0.0)
        # buying_power giảm ~ chi phí lệnh đã khớp
        cost = filled_qty * 10100.0 * 1.0015
        assert bp_after == pytest.approx(bp_before - cost, rel=1e-3)

    def test_rejected_order_preserves_full_buying_power(self, engine):
        """Lệnh bù bị hủy (kill-switch) → buying_power T+k giữ NGUYÊN (no reclaim)."""
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 11000.0, 11000.0)  # decay 10% → hủy
        bp_before = engine.mtm.get_buying_power(recovery, 0.0)
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        engine.process_catchup_queue(recovery)
        bp_after = engine.mtm.get_buying_power(recovery, 0.0)
        assert bp_after == pytest.approx(bp_before)  # đủ sức mua cho tín hiệu mới T+k


# ==================================================== SIZING RECONCILIATION
class TestSizingReconciliation:
    def test_qty_capped_by_buying_power_at_higher_price(self, engine):
        """Giá tăng tại T+k → qty khớp bị giảm để không vượt sức mua."""
        # Vốn nhỏ để ép ràng buộc sức mua rõ ràng
        from src.engine.paper_trading_engine import PaperTradingEngine
        from src.database.db_core import get_connection
        small_pf = TEST_PORTFOLIO
        with get_connection() as conn:
            conn.execute("UPDATE paper_portfolio_state SET settled_cash=?, "
                         "initial_capital=? WHERE portfolio_id=?",
                         (10_000_000.0, 10_000_000.0, small_pf))
            conn.commit()
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        # target qty tính tại close_T=10000 → 1000 CP (=10tr). Nhưng open_Tk=10200
        _insert_ohlcv(TEST_SYMBOL, recovery, 10200.0, 10300.0)  # +2% < 3%
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        rep = engine.process_catchup_queue(recovery)
        assert len(rep["filled"]) == 1
        qty = rep["filled"][0]["qty"]
        # qty phải <= sức mua / (open_Tk*(1+fee)), làm tròn lô 100 → < 1000
        max_qty = int((10_000_000.0 // (10200.0 * 1.0015)) // 100 * 100)
        assert qty == max_qty
        assert qty < 1000


# ==================================================== IDEMPOTENCY
class TestQueueIdempotency:
    def test_filled_not_refilled_on_rerun(self, engine):
        """Chạy lại process_catchup_queue → lệnh FILLED không khớp lại."""
        from src.database.db_core import get_connection
        T, recovery = TEST_DATES[0], TEST_DATES[2]
        _insert_ohlcv(TEST_SYMBOL, recovery, 10100.0, 10200.0)
        engine._enqueue_catchup_order(TEST_SYMBOL, "BUY", 1000, 10000.0, T)
        engine.process_catchup_queue(recovery)
        with get_connection() as conn:
            n_lots_1 = conn.execute(
                "SELECT COUNT(*) FROM paper_lots WHERE portfolio_id=?",
                (TEST_PORTFOLIO,)).fetchone()[0]
        rep2 = engine.process_catchup_queue(recovery)
        with get_connection() as conn:
            n_lots_2 = conn.execute(
                "SELECT COUNT(*) FROM paper_lots WHERE portfolio_id=?",
                (TEST_PORTFOLIO,)).fetchone()[0]
        assert rep2["processed"] == 0  # không còn PENDING
        assert n_lots_2 == n_lots_1    # không tạo lot trùng
