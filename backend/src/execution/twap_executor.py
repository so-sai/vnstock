"""TWAPExecutor — Order Execution Layer (Phase 4.3).

Middleware giữa CLI và StalePositionManager.
Preflight ping, Idempotent Resume, Circuit Breaker, Liquidity Strike handling.
"""

import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from src.portfolio.system_state import (
    update_twap_context, get_twap_context, clear_twap_context,
    is_locked, lock as _lock_state,
)
from src.portfolio.stale_manager import StalePositionManager

PING_TIMEOUT_S = 2
MAX_CIRCUIT_BREAKER_TRIPS = 3
MAX_REQUEUES = 3
BASE_SLICE_INTERVAL_S = 60
SLIPPAGE_THRESHOLD = 0.003   # 0.3%
INTERVAL_CEILING_S = 1200    # 20 phút
INTERVAL_FLOOR_S = 30        # 30 giây
SAFETY_STOP_MINUTES = 15


@dataclass
class Slice:
    index: int
    amount: float
    status: str = "PENDING"   # PENDING | SUBMITTED | PARTIAL_FILLED | FILLED | CANCELED | DEFERRED
    broker_order_id: Optional[str] = None
    submitted_at: Optional[str] = None
    fill_price: Optional[float] = None
    filled_qty: Optional[float] = None
    slippage: Optional[float] = None


@dataclass
class SlicePlan:
    stale_campaign_id: str
    total_amount: float
    n_slices: int
    slices: list[Slice] = field(default_factory=list)
    status: str = "PENDING"   # PENDING | ACTIVE | PAUSED | COMPLETED | FAILED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class BrokerAPI:
    """Simulated Broker API — pluggable cho single-operator laptop."""

    def __init__(self):
        self._orders: dict[str, dict] = {}
        self._next_id = 0

    def ping(self, timeout: float = PING_TIMEOUT_S) -> bool:
        try:
            socket.setdefaulttimeout(timeout)
            socket.gethostbyname("api.broker.local")
            return True
        except (socket.gaierror, socket.timeout, OSError):
            return False

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> str:
        self._next_id += 1
        oid = f"BROKER_{self._next_id:06d}"
        self._orders[oid] = {
            "order_id": oid, "symbol": symbol, "side": side,
            "quantity": quantity, "filled_qty": 0.0, "price": price,
            "status": "PENDING", "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return oid

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order or order["status"] == "FILLED":
            return False
        order["status"] = "CANCELED"
        return True

    def query_order(self, order_id: str) -> dict:
        order = self._orders.get(order_id)
        if not order:
            return {"status": "NOT_FOUND"}
        return dict(order)

    def fill_order(self, order_id: str, fill_qty: float, fill_price: float):
        order = self._orders.get(order_id)
        if not order:
            return
        order["filled_qty"] = fill_qty
        order["fill_price"] = fill_price
        order["status"] = "PARTIAL_FILLED" if fill_qty < order["quantity"] else "FILLED"


class TWAPExecutor:
    """TWAP Execution Engine — thanh lý stale positions qua nhiều slice.

    Chịu trách nhiệm I/O thực tế (broker API).
    StalePositionManager chỉ là sổ cái thụ động.
    """

    def __init__(self, stale_manager: StalePositionManager, broker: Optional[BrokerAPI] = None,
                 symbol: str = "STOCK", side: str = "SELL"):
        self.sm = stale_manager
        self.broker = broker or BrokerAPI()
        self.symbol = symbol
        self.side = side
        self.plan: Optional[SlicePlan] = None

    # ── Plan ─────────────────────────────────────────────

    def build_plan(self, n_slices: int = 5) -> SlicePlan:
        """Xây dựng kế hoạch TWAP từ stale layers."""
        if not self.sm.stale_pcts:
            raise ValueError("Không có stale positions để thanh lý")
        total_risk = sum(self.sm.stale_pcts) * self.sm.total_capital
        slice_size = round(total_risk / n_slices, 2)
        slices = [Slice(index=i + 1, amount=slice_size) for i in range(n_slices)]
        plan = SlicePlan(
            stale_campaign_id=self.sm._layers[-1].campaign_id if self.sm._layers else "unknown",
            total_amount=total_risk,
            n_slices=n_slices,
            slices=slices,
            status="PENDING",
        )
        self.plan = plan
        self._persist()
        return plan

    # ── Execution ────────────────────────────────────────

    def preflight_ping(self) -> bool:
        """Pre-flight ping với hard timeout 2s."""
        ok = self.broker.ping()
        ctx = get_twap_context()
        if ok:
            ctx["last_known_good_network"] = datetime.now(timezone.utc).isoformat()
            ctx["circuit_breaker_trips"] = 0
        else:
            ctx["circuit_breaker_trips"] = ctx.get("circuit_breaker_trips", 0) + 1
            cb_trips = ctx["circuit_breaker_trips"]
            if cb_trips >= MAX_CIRCUIT_BREAKER_TRIPS:
                ctx["twap_resume_cursor"] = None  # reset
                update_twap_context(ctx)
                raise RuntimeError(f"CIRCUIT_BREAKER — {cb_trips} consecutive timeouts")
        update_twap_context(ctx)
        return ok

    def execute_slice(self, index: int, price: float) -> dict:
        """Thực thi một slice duy nhất."""
        if not self.plan:
            raise RuntimeError("Chưa có plan — gọi build_plan() trước")
        self.preflight_ping()
        slice_obj = self._find_slice(index)
        if not slice_obj or slice_obj.status != "PENDING":
            return {"success": False, "reason": f"SLICE_{slice_obj.status if slice_obj else 'NOT_FOUND'}"}

        self.plan.status = "ACTIVE"
        self.plan.started_at = self.plan.started_at or datetime.now(timezone.utc).isoformat()

        oid = self.broker.place_limit_order(
            symbol=self.symbol, side=self.side,
            quantity=slice_obj.amount, price=price,
        )
        slice_obj.broker_order_id = oid
        slice_obj.status = "SUBMITTED"
        slice_obj.submitted_at = datetime.now(timezone.utc).isoformat()

        self._persist()
        return {
            "success": True,
            "index": index,
            "amount": slice_obj.amount,
            "broker_order_id": oid,
            "price": price,
        }

    # ── Resume (Idempotent) ──────────────────────────────

    def resume(self) -> dict:
        """Resume từ system_state — query broker trước khi hành động."""
        ctx = get_twap_context()
        cursor = ctx.get("twap_resume_cursor")
        if not cursor or not self.plan:
            return {"success": False, "reason": "NO_RESUME_CURSOR"}

        active_oid = ctx.get("active_broker_order_id")
        if active_oid:
            broker_status = self.broker.query_order(active_oid)
            bstatus = broker_status.get("status", "NOT_FOUND")

            # Tìm slice bằng broker_order_id, KHÔNG bằng cursor
            slice_obj = self._find_slice_by_oid(active_oid) or self._find_slice(cursor)

            if bstatus == "PENDING":
                self.broker.cancel_order(active_oid)
                if slice_obj:
                    slice_obj.status = "CANCELED"
                return {"success": False, "reason": "ORDER_STILL_PENDING", "action": "cancel_and_requeue"}

            elif bstatus == "PARTIAL_FILLED":
                fill_qty = broker_status.get("filled_qty", 0)
                fill_price = broker_status.get("fill_price", 0)
                remaining = broker_status.get("quantity", 0) - fill_qty
                if slice_obj:
                    slice_obj.fill_price = fill_price
                    slice_obj.filled_qty = fill_qty
                    slice_obj.status = "PARTIAL_FILLED"
                    if remaining > 0:
                        slice_obj.amount = remaining
                        slice_obj.status = "PENDING"
                self._persist()
                return {"success": True, "action": "partial_fill_requeue", "remaining": remaining}

            elif bstatus == "FILLED":
                if slice_obj:
                    slice_obj.status = "FILLED"
                    slice_obj.fill_price = broker_status.get("fill_price")
                    slice_obj.filled_qty = broker_status.get("filled_qty")
                self._persist()
                ctx["twap_resume_cursor"] = cursor + 1 if cursor < self.plan.n_slices else None
                update_twap_context(ctx)
                return {"success": True, "action": "filled_advance", "next_cursor": ctx["twap_resume_cursor"]}

        # Normal resume: advance cursor
        return {"success": True, "action": "cursor_advance", "cursor": cursor}

    # ── Liquidity Strike Handling ────────────────────────

    def handle_liquidity_strike(self, index: int, requeue_count: int = 0) -> dict:
        """Xử lý khi bid depth = 0 (Pending/Unfilled pending).

        Cancel → adaptive re-queue (max MAX_REQUEUES) → DEFERRED.
        """
        slice_obj = self._find_slice(index)
        if not slice_obj:
            return {"success": False, "reason": "SLICE_NOT_FOUND"}

        if requeue_count >= MAX_REQUEUES:
            slice_obj.status = "DEFERRED"
            self._persist()
            return {
                "success": True,
                "action": "DEFERRED",
                "reason": f"Max requeues ({MAX_REQUEUES}) reached",
                "index": index,
            }

        # Cancel old order
        if slice_obj.broker_order_id:
            self.broker.cancel_order(slice_obj.broker_order_id)

        # Adaptive price: +0.5% từ giá cuối
        adjusted_price = (slice_obj.fill_price or 100.0) * 1.005
        oid = self.broker.place_limit_order(
            symbol=self.symbol, side=self.side,
            quantity=slice_obj.amount, price=round(adjusted_price, 2),
        )
        slice_obj.broker_order_id = oid
        slice_obj.status = "SUBMITTED"
        slice_obj.submitted_at = datetime.now(timezone.utc).isoformat()
        self._persist()

        return {
            "success": True,
            "action": "REQUEUED",
            "requeue_count": requeue_count + 1,
            "adjusted_price": round(adjusted_price, 2),
            "index": index,
        }

    # ── Interval Adjustment ──────────────────────────────

    def compute_interval(self, last_slippage: float, slices_remaining: int,
                         session_remaining_s: float) -> float:
        """Dynamic interval: giãn khi slippage cao, thu hẹp khi thấp."""
        base = min(session_remaining_s / max(slices_remaining, 1), BASE_SLICE_INTERVAL_S)
        if last_slippage > SLIPPAGE_THRESHOLD:
            interval = base * 2.0
        elif last_slippage < SLIPPAGE_THRESHOLD * 0.5:
            interval = base * 0.8
        else:
            interval = base
        # Safety: hủy nếu còn < 15 phút
        if session_remaining_s < (SAFETY_STOP_MINUTES * 60):
            return 0.0  # signal stop
        return max(INTERVAL_FLOOR_S, min(interval, INTERVAL_CEILING_S))

    # ── Complete ─────────────────────────────────────────

    def complete_plan(self) -> dict:
        """Kết thúc plan: ghi nhận write-off vào stale_manager."""
        if not self.plan:
            return {"success": False, "reason": "NO_PLAN"}
        filled_amount = sum(
            s.amount for s in self.plan.slices
            if s.status in ("FILLED", "PARTIAL_FILLED")
        )
        self.plan.status = "COMPLETED"
        self.plan.completed_at = datetime.now(timezone.utc).isoformat()

        # Ghi nhận write-off trên sổ cái
        result = self.sm.writeoff_lifo()
        clear_twap_context()
        return {
            "success": True,
            "plan_status": "COMPLETED",
            "filled_amount": round(filled_amount, 2),
            "total_amount": self.plan.total_amount,
            "writeoff": result,
        }

    # ── Status ───────────────────────────────────────────

    def status(self) -> dict:
        ctx = get_twap_context()
        plan_status = self.plan.status if self.plan else "NO_PLAN"
        filled = sum(s.amount for s in (self.plan.slices if self.plan else []) if s.status == "FILLED")
        return {
            "plan_status": plan_status,
            "total_slices": self.plan.n_slices if self.plan else 0,
            "filled_amount": round(filled, 2),
            "cursor": ctx.get("twap_resume_cursor"),
            "circuit_breaker_trips": ctx.get("circuit_breaker_trips", 0),
            "last_known_good_network": ctx.get("last_known_good_network"),
        }

    # ── Internal ─────────────────────────────────────────

    def _find_slice(self, index: int) -> Optional[Slice]:
        if not self.plan:
            return None
        for s in self.plan.slices:
            if s.index == index:
                return s
        return None

    def _find_slice_by_oid(self, oid: str) -> Optional[Slice]:
        if not self.plan:
            return None
        for s in self.plan.slices:
            if s.broker_order_id == oid:
                return s
        return None

    def _persist(self):
        if not self.plan:
            return
        ctx = {
            "twap_resume_cursor": self._current_cursor(),
            "pending_slices_count": sum(1 for s in self.plan.slices if s.status == "PENDING"),
            "active_broker_order_id": self._active_order_id(),
            "slice_history": [
                {"index": s.index, "status": s.status, "broker_order_id": s.broker_order_id}
                for s in self.plan.slices
            ],
        }
        update_twap_context(ctx)

    def _current_cursor(self) -> Optional[int]:
        if not self.plan:
            return None
        for s in self.plan.slices:
            if s.status == "PENDING":
                return s.index
        return None

    def _active_order_id(self) -> Optional[str]:
        if not self.plan:
            return None
        for s in self.plan.slices:
            if s.broker_order_id and s.status == "SUBMITTED":
                return s.broker_order_id
        return None
