"""Tests cho TWAPExecutor (Phase 4.3) — plan, resume, circuit breaker, liquidity strike."""
import time
import pytest
from src.execution.twap_executor import TWAPExecutor, BrokerAPI, Slice, SlicePlan
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


def make_mgr(**kw):
    return StalePositionManager(**kw)


class TestBrokerAPI:
    def test_ping_fails_locally(self):
        """Preflight ping thất bại khi không có broker (timeout nhanh)."""
        b = BrokerAPI()
        result = b.ping(timeout=0.001)
        assert result is False

    def test_place_and_query_order(self):
        b = BrokerAPI()
        oid = b.place_limit_order("STOCK", "SELL", 100, 95.5)
        q = b.query_order(oid)
        assert q["status"] == "PENDING"
        assert q["quantity"] == 100
        assert q["price"] == 95.5

    def test_cancel_order(self):
        b = BrokerAPI()
        oid = b.place_limit_order("STOCK", "SELL", 100, 95.5)
        assert b.cancel_order(oid) is True
        q = b.query_order(oid)
        assert q["status"] == "CANCELED"

    def test_cancel_nonexistent(self):
        b = BrokerAPI()
        assert b.cancel_order("FAKE") is False

    def test_fill_order(self):
        b = BrokerAPI()
        oid = b.place_limit_order("STOCK", "SELL", 100, 95.5)
        b.fill_order(oid, 60, 95.0)
        q = b.query_order(oid)
        assert q["status"] == "PARTIAL_FILLED"
        assert q["filled_qty"] == 60


class TestTWAPExecutor:
    def test_build_plan_creates_slices(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.13)
        exe = TWAPExecutor(mgr)
        plan = exe.build_plan(n_slices=5)
        assert plan.n_slices == 5
        assert len(plan.slices) == 5
        assert all(s.status == "PENDING" for s in plan.slices)
        assert plan.total_amount > 0

    def test_execute_slice_submits_order(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.build_plan(n_slices=5)
        # Mock ping thành công
        exe.broker.ping = lambda: True
        r = exe.execute_slice(1, price=100.0)
        assert r["success"] is True
        assert r["broker_order_id"] is not None
        assert exe.plan.slices[0].status == "SUBMITTED"

    def test_execute_slice_fails_ping(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.build_plan(n_slices=5)
        exe.broker.ping = lambda: False
        # Circuit breaker sau 3 lần
        with pytest.raises(RuntimeError, match="CIRCUIT_BREAKER"):
            for _ in range(3):
                try:
                    exe.execute_slice(1, price=100.0)
                except RuntimeError:
                    pass
            exe.execute_slice(1, price=100.0)

    def test_circuit_breaker_resets_after_success(self, tmp_paths):
        """Sau 2 ping fail → lần 3 success → CB reset, execution OK."""
        from src.portfolio.system_state import update_twap_context, get_twap_context
        # Reset CB counter
        ctx = get_twap_context()
        ctx["circuit_breaker_trips"] = 2  # simulate 2 prior failures
        update_twap_context(ctx)

        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True  # ping succeeds → reset CB
        exe.build_plan(n_slices=3)
        r = exe.execute_slice(1, price=100.0)
        assert r["success"] is True
        ctx = get_twap_context()
        assert ctx["circuit_breaker_trips"] == 0  # đã reset

    def test_resume_with_pending_order(self, tmp_paths):
        """Resume phát hiện order PENDING → cancel + requeue."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=100.0)
        # Mô phỏng resume
        r = exe.resume()
        assert r["reason"] == "ORDER_STILL_PENDING"
        assert r["action"] == "cancel_and_requeue"

    def test_resume_filled_order(self, tmp_paths):
        """Resume phát hiện order FILLED → advance cursor."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=100.0)
        oid = exe.plan.slices[0].broker_order_id
        qty = exe.plan.slices[0].amount  # real quantity from plan
        exe.broker.fill_order(oid, qty, 99.5)  # fill 100%
        r = exe.resume()
        assert r["success"] is True
        assert r["action"] == "filled_advance"

    def test_handle_liquidity_strike_defers_after_max(self, tmp_paths):
        """Liquidity strike → requeue → max requeues → DEFERRED."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=3)
        exe.execute_slice(1, price=100.0)
        r = exe.handle_liquidity_strike(1, requeue_count=3)
        assert r["success"] is True
        assert r["action"] == "DEFERRED"

    def test_handle_liquidity_strike_requeues(self, tmp_paths):
        """Liquidity strike → requeue với adaptive price."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=3)
        exe.plan.slices[0].fill_price = 100.0
        r = exe.handle_liquidity_strike(1, requeue_count=0)
        assert r["action"] == "REQUEUED"
        assert r["adjusted_price"] == 100.5  # 100 * 1.005

    def test_compute_interval_dynamic(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        exe = TWAPExecutor(mgr)
        # High slippage → giãn
        hi = exe.compute_interval(0.005, 5, 600)
        # Low slippage → thu hẹp
        lo = exe.compute_interval(0.0005, 5, 600)
        assert hi > lo or hi == pytest.approx(lo * 2.5, rel=0.5)

    def test_compute_interval_safety_stop(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        exe = TWAPExecutor(mgr)
        interval = exe.compute_interval(0.001, 5, 60)  # 1 phút < 15 phút safety
        assert interval == 0.0

    def test_complete_plan(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr._layers[0].quantity = 100
        mgr._layers[0].current_price = 50
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=1)
        exe.execute_slice(1, price=100.0)
        oid = exe.plan.slices[0].broker_order_id
        exe.broker.fill_order(oid, 100, 99.5)
        r = exe.complete_plan()
        assert r["success"] is True
        assert r["plan_status"] == "COMPLETED"

    def test_partial_fill_resume(self, tmp_paths):
        """Resume sau partial fill → requeue phần còn lại."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=2)
        exe.execute_slice(1, price=100.0)
        oid = exe.plan.slices[0].broker_order_id
        exe.broker.fill_order(oid, 40, 99.0)  # partial fill
        r = exe.resume()
        assert r["action"] == "partial_fill_requeue"
        assert r["remaining"] > 0

    def test_plan_status_output(self, tmp_paths):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        exe = TWAPExecutor(mgr)
        exe.broker.ping = lambda: True
        exe.build_plan(n_slices=3)
        s = exe.status()
        assert s["plan_status"] == "PENDING"
        assert s["total_slices"] == 3
