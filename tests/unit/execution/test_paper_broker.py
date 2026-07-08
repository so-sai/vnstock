"""Tests cho Phase 5 — PaperBroker, OrderBook, StreamingFeed, Hard Disconnect."""

import time
import pytest
from src.execution.paper_broker import (
    PaperBroker, OrderBook, Level, StreamingFeed,
)
from src.execution.twap_executor import TWAPExecutor, SlicePlan
from src.portfolio.stale_manager import StalePositionManager, _set_stale_path
from src.portfolio.system_state import set_lock_path, clear_twap_context
from pathlib import Path


@pytest.fixture
def tmp_paths(tmp_path):
    sp = tmp_path / "system_state.json"
    st = tmp_path / "stale_positions.json"
    set_lock_path(sp)
    _set_stale_path(st)
    clear_twap_context()
    yield
    set_lock_path(Path("backend/data/system_state.json"))
    _set_stale_path(Path("backend/data/stale_positions.json"))


# ── OrderBook ──────────────────────────────────────────────

class TestOrderBook:
    def test_build_symmetric(self):
        book = OrderBook.build("TEST", mid=100.0, spread=0.5, depth_per_level=5000, n_levels=5)
        assert len(book.bids) == 5
        assert len(book.asks) == 5
        assert book.best_bid() == pytest.approx(99.75, rel=0.02)
        assert book.best_ask() == pytest.approx(100.25, rel=0.02)
        assert book.mid_price() == pytest.approx(100.0, rel=0.1)
        assert book.spread() >= 0.4

    def test_mid_price_from_best(self):
        book = OrderBook.build("TEST")
        mid = book.mid_price()
        bb = book.best_bid()
        ba = book.best_ask()
        assert mid == (bb + ba) / 2

    def test_best_bid_zero_when_empty(self):
        book = OrderBook(symbol="TEST", bids=[], asks=[Level(100.5, 1000)])
        assert book.best_bid() == 0.0

    def test_best_ask_inf_when_empty(self):
        book = OrderBook(symbol="TEST", bids=[Level(99.5, 1000)], asks=[])
        assert book.best_ask() == float("inf")

    def test_depth_for_sell(self):
        book = OrderBook.build("TEST", mid=100)
        depth = book.depth_for_sell(99.5)
        assert len(depth) > 0
        assert all(l.price >= 99.5 for l in depth)

    def test_depth_for_sell_empty_when_too_high(self):
        book = OrderBook.build("TEST", mid=100)
        depth = book.depth_for_sell(999.0)
        assert len(depth) == 0

    def test_random_walk_changes_price(self):
        book = OrderBook.build("TEST", mid=100.0, volatility=0.50)
        orig_mid = book.mid_price()
        for _ in range(200):
            book.random_walk()
        new_mid = book.mid_price()
        assert new_mid != orig_mid

    def test_drain_bids(self):
        book = OrderBook.build("TEST")
        book.drain_bids()
        assert all(l.volume == 0 for l in book.bids)
        assert book.best_bid() > 0  # prices unchanged, only volumes

    def test_drain_asks(self):
        book = OrderBook.build("TEST")
        book.drain_asks()
        assert all(l.volume == 0 for l in book.asks)

    def test_restore_depth(self):
        book = OrderBook.build("TEST")
        book.drain_bids()
        book.drain_asks()
        book.restore_depth(8000)
        assert all(l.volume > 0 for l in book.bids)
        assert all(l.volume > 0 for l in book.asks)
        assert book.bids[0].volume == 8000


# ── PaperBroker ────────────────────────────────────────────

class TestPaperBrokerBasic:
    def test_ping_default_ok(self):
        broker = PaperBroker()
        assert broker.ping() is True

    def test_ping_network_failure(self):
        broker = PaperBroker()
        broker.set_network_failure(True)
        assert broker.ping() is False
        broker.set_network_failure(False)
        assert broker.ping() is True

    def test_get_best_bid_from_book(self):
        book = OrderBook.build("TEST", mid=95.0)
        broker = PaperBroker(book=book)
        assert broker.get_best_bid("TEST") == book.best_bid()

    def test_place_order_returns_oid(self):
        broker = PaperBroker()
        oid = broker.place_limit_order("TEST", "SELL", 100, 99.5)
        assert oid.startswith("PAPER_")
        assert broker.query_order(oid)["status"] == "FILLED"  # depth available

    def test_cancel_passive_order(self):
        """Cancel order with passive price (no auto-fill)."""
        broker = PaperBroker()
        # Use extreme price so order stays PENDING
        oid = broker.place_limit_order("TEST", "SELL", 100, 999.0)
        o = broker.query_order(oid)
        assert o["status"] == "PENDING"
        assert broker.cancel_order(oid) is True
        assert broker.query_order(oid)["status"] == "CANCELED"


class TestPaperBrokerFillSimulation:
    def test_sell_full_fill_when_depth_sufficient(self):
        """Đủ depth ở bid → FILLED 100%."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=100_000)
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "SELL", 1000, 99.5)
        o = broker.query_order(oid)
        assert o["status"] == "FILLED"
        assert o["filled_qty"] == 1000

    def test_sell_partial_fill_when_depth_insufficient(self):
        """Không đủ depth → PARTIAL_FILLED, phần còn lại mất."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=100)
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "SELL", 1000, 99.5)
        o = broker.query_order(oid)
        assert o["status"] == "PARTIAL_FILLED"
        assert o["filled_qty"] < 1000

    def test_sell_pending_when_no_bid_depth(self):
        """Buyer Strike → PENDING (không fill được)."""
        book = OrderBook.build("TEST", mid=100.0)
        book.drain_bids()
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "SELL", 1000, 99.5)
        o = broker.query_order(oid)
        assert o["status"] == "PENDING"
        assert o["filled_qty"] == 0

    def test_buy_full_fill(self):
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=100_000)
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "BUY", 1000, 100.5)
        o = broker.query_order(oid)
        assert o["status"] == "FILLED"
        assert o["filled_qty"] == 1000

    def test_buy_pending_when_no_ask_depth(self):
        """Seller Strike → PENDING."""
        book = OrderBook.build("TEST", mid=100.0)
        book.drain_asks()
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "BUY", 1000, 100.5)
        o = broker.query_order(oid)
        assert o["status"] == "PENDING"

    def test_limit_price_too_passive_no_fill(self):
        """SELL limit > best_bid → PENDING (chờ requeue)."""
        book = OrderBook.build("TEST", mid=100.0)
        broker = PaperBroker(book=book)
        oid = broker.place_limit_order("TEST", "SELL", 1000, 999.0)
        o = broker.query_order(oid)
        assert o["status"] == "PENDING"

    def test_slippage_tracked(self):
        """Large order → measurable slippage."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=500)
        broker = PaperBroker(book=book)
        broker.place_limit_order("TEST", "SELL", 2000, 99.5)
        report = broker.slippage_report()
        assert report["n"] >= 1
        assert report["mean"] > 0

    def test_expected_impact_formula(self):
        broker = PaperBroker()
        impact = broker.expected_impact(10_000)
        assert impact > 0
        # Larger order → larger impact
        big_impact = broker.expected_impact(100_000)
        assert big_impact > impact


# ── PaperBroker + TWAPExecutor end-to-end ─────────────────

class TestPaperBrokerIntegration:
    def test_twap_executor_with_paper_broker(self, tmp_paths):
        """TWAPExecutor + PaperBroker: plan → execute → fill."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=100_000)
        broker = PaperBroker(book=book)
        mgr = StalePositionManager(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)

        exe = TWAPExecutor(mgr, broker=broker)
        exe.build_plan(n_slices=3)
        r = exe.execute_slice(1, price=99.5)
        assert r["success"] is True

        # Broker tự động fill slice
        slice_obj = exe.plan.slices[0]
        assert slice_obj.status == "SUBMITTED"
        o = broker.query_order(slice_obj.broker_order_id)
        assert o["status"] == "FILLED"

    def test_twap_executor_buyer_strike(self, tmp_paths):
        """Buyer Strike → PENDING → liquidity strike → DEFERRED."""
        book = OrderBook.build("TEST", mid=100.0)
        book.drain_bids()
        broker = PaperBroker(book=book)
        mgr = StalePositionManager(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)

        exe = TWAPExecutor(mgr, broker=broker)
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=99.5)
        # Order stays PENDING → trigger liquidity strike
        r = exe.handle_liquidity_strike(1, requeue_count=0)
        assert r["action"] == "REQUEUED"
        # Even after requeue, book still empty → PENDING again
        o = broker.query_order(exe.plan.slices[0].broker_order_id)
        assert o["status"] == "PENDING"

    def test_twap_executor_buyer_strike_deferred(self, tmp_paths):
        """Buyer Strike kéo dài → DEFERRED sau MAX_REQUEUES."""
        book = OrderBook.build("TEST", mid=100.0)
        book.drain_bids()
        broker = PaperBroker(book=book)
        mgr = StalePositionManager(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)

        exe = TWAPExecutor(mgr, broker=broker)
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=99.5)
        # Simulate MAX_REQUEUES failures
        for i in range(3):
            broker.place_limit_order("TEST", "SELL", 100, 99.5)  # still pending
        r = exe.handle_liquidity_strike(1, requeue_count=3)
        assert r["action"] == "DEFERRED"

    def test_hard_disconnect_resume(self, tmp_paths):
        """Hard disconnect → slice fails → reconnect → resume advances cursor."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=100_000)
        broker = PaperBroker(book=book)
        mgr = StalePositionManager(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)

        exe = TWAPExecutor(mgr, broker=broker)
        exe.build_plan(n_slices=3)

        # Execute slice 1 normally
        r1 = exe.execute_slice(1, price=99.5)
        assert r1["success"] is True
        oid1 = r1["broker_order_id"]
        broker.fill_order(oid1, 100, 99.5)  # manually fill

        # Kill network, try slice 2 → fails
        broker.set_network_failure(True)
        with pytest.raises(RuntimeError, match="CIRCUIT_BREAKER"):
            for _ in range(4):
                try:
                    exe.execute_slice(2, price=99.5)
                except RuntimeError:
                    pass
            exe.execute_slice(2, price=99.5)

        # Reconnect
        broker.set_network_failure(False)

        # Advance cursor manually (simulate partial progress)
        from src.portfolio.system_state import update_twap_context
        ctx = {
            "twap_resume_cursor": 2,
            "circuit_breaker_trips": 0,
            "last_known_good_network": "2026-07-08T12:00:00",
        }
        update_twap_context(ctx)

        r = exe.resume()
        # Should advance cursor past slice 2's pending status
        assert r["success"] is True

    def test_slippage_increases_with_order_size(self):
        """Larger orders produce higher slippage (depth-weighted)."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=2000)
        broker = PaperBroker(book=book)

        broker.place_limit_order("TEST", "SELL", 1000, 99.5)
        small_slip = broker.slippage_report()["mean"]

        broker2 = PaperBroker(book=OrderBook.build("TEST", mid=100.0, depth_per_level=2000))
        broker2.place_limit_order("TEST", "SELL", 10000, 99.5)
        large_slip = broker2.slippage_report()["mean"]

        assert large_slip >= small_slip

    def test_adaptive_price_recovers_after_strike(self, tmp_paths):
        """Buyer Strike → adaptive price → depth restored → fill succeeds."""
        book = OrderBook.build("TEST", mid=100.0)
        book.drain_bids()
        broker = PaperBroker(book=book)
        mgr = StalePositionManager(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)

        exe = TWAPExecutor(mgr, broker=broker)
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=99.5)

        # Restore depth (simulating market returning)
        book.restore_depth(100_000)

        # Adaptive requeue — now should fill
        r = exe.handle_liquidity_strike(1, requeue_count=0)
        assert r["action"] == "REQUEUED"
        o = broker.query_order(exe.plan.slices[0].broker_order_id)
        assert o["status"] == "FILLED"


# ── StreamingFeed ──────────────────────────────────────────

class TestStreamingFeed:
    def test_tick_changes_book(self):
        feed = StreamingFeed("TEST", base_price=100.0, volatility=0.50)
        before = feed.book.mid_price()
        for _ in range(200):
            feed.tick()
        after = feed.book.mid_price()
        assert after != before

    def test_drain_and_restore(self):
        feed = StreamingFeed("TEST")
        feed.drain("SELL")
        assert all(l.volume == 0 for l in feed.book.bids)
        feed.restore(5000)
        assert feed.book.bids[0].volume > 0

    def test_tick_count_increments(self):
        feed = StreamingFeed("TEST")
        assert feed.tick_count == 0
        feed.tick()
        feed.tick()
        feed.tick()
        assert feed.tick_count == 3


# ── Almgren-Chriss verification ────────────────────────────

class TestAlmgrenChriss:
    def test_slippage_within_expected_bounds(self):
        """Observed slippage should be within a reasonable factor of theoretical."""
        book = OrderBook.build("TEST", mid=100.0, depth_per_level=10_000, volatility=0.02)
        broker = PaperBroker(book=book, impact_coeff=0.3, hourly_volume=1_000_000)
        broker.place_limit_order("TEST", "SELL", 50_000, 99.5)
        report = broker.slippage_report()
        theoretical = broker.expected_impact(50_000)
        # Observed should be within 5x of theoretical (model vs simulation)
        assert report["mean"] <= theoretical * 5, f"obs={report['mean']:.6f} theo={theoretical:.6f}"

    def test_impact_scale_sublinear(self):
        """Impact scales as sqrt(Q), not linear."""
        q_small = 1_000
        q_large = 100_000
        broker = PaperBroker(hourly_volume=1_000_000)
        i_small = broker.expected_impact(q_small)
        i_large = broker.expected_impact(q_large)
        ratio = i_large / i_small
        q_ratio = q_large / q_small
        # sqrt(100) = 10, so ratio should be ≈10, not 100
        assert ratio < q_ratio * 0.5  # well below linear
