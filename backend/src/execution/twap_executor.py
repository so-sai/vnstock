"""TWAPExecutor — Order Execution Layer (Phase 4.3).

Middleware giữa CLI và StalePositionManager.
Preflight ping, Idempotent Resume, Circuit Breaker, Liquidity Strike handling.
Asia Circuit Breaker (Intraday Governor Override) — KOSPI 13:30 canary.
"""

import logging
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

logger = logging.getLogger(__name__)

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

    def get_open_orders(self, symbol: str) -> list[dict]:
        """Return all non-terminal orders for a given symbol."""
        return [
            dict(o) for o in self._orders.values()
            if o["symbol"] == symbol and o["status"] in ("PENDING", "PARTIAL_FILLED")
        ]

    def get_best_bid(self, symbol: str) -> float:
        """Current best bid from order book (simulated)."""
        return 99.5

    def get_best_ask(self, symbol: str) -> float:
        """Current best ask from order book (simulated)."""
        return 100.5


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

    # ── Asia Circuit Breaker (Intraday Governor Override v2.1) ──
    #
    # Multi-tiered Adaptive Breaker (2026-07-10 hard-lock):
    #   Tier 1 (Absolute Veto):  KOSPI intraday drop > 1.5% → halt unconditionally
    #   Tier 2 (Confirmed Canary): KOSPI intraday drop > 1.0% AND VN-Index intraday return < −0.5%
    #
    # Data: yfinance 1m candles for KOSPI futures (KM=F), scanned 13:15–13:31 KST
    #       during the window 13:31–13:50 VN time (KRX data latency tolerance).
    #
    #   θ (eigenvector rotation) REMOVED — replaced by direct VN-Index impulse confirmation.
    #

    ASIA_HALT_KOSPI_TIER2_DROP = 0.01        # 1.0%
    ASIA_HALT_KOSPI_TIER1_DROP = 0.015       # 1.5% — absolute veto
    ASIA_HALT_VN_CONFIRM_DROP  = -0.005       # -0.5%

    KOSPI_INTRADAY_TICKER = "KM=F"
    SCAN_WINDOW_START_KST  = "13:15"
    SCAN_WINDOW_END_KST    = "13:31"
    SCAN_RETRY_SEC         = 45               # check every 45 s between 13:31–13:50 VNT

    @staticmethod
    def _fetch_kospi_intraday() -> Optional[float]:
        """Fetch KOSPI futures (KM=F) 1m candles, return drop % within scan window.

        Scans 13:15–13:31 KST to capture last ~15 minutes of KRX continuous trading.
        Returns (latest - open_within_window) / open_within_window as a signed float,
        or None on failure / stale data.
        """
        try:
            import yfinance as yf
            import pandas as pd

            ticker = yf.Ticker(TWAPExecutor.KOSPI_INTRADAY_TICKER)
            df = ticker.history(period="1d", interval="1m")
            if df is None or df.empty:
                logger.warning("KOSPI intraday: no 1m data returned")
                return None

            # Filter to scan window (KST = UTC+9)
            now_kst = pd.Timestamp.now(tz="Asia/Seoul")
            scan_start = now_kst.normalize() + pd.Timedelta(TWAPExecutor.SCAN_WINDOW_START_KST)
            scan_end   = now_kst.normalize() + pd.Timedelta(TWAPExecutor.SCAN_WINDOW_END_KST)

            window = df[df.index >= scan_start.tz_localize(None)]
            if window.empty:
                logger.warning("KOSPI intraday: scan window empty (market not yet closed?)")
                # Fallback: use earliest 1m candle of the day as reference
                if len(df) < 2:
                    return None
                first = float(df["Close"].iloc[0])
                last  = float(df["Close"].iloc[-1])
                if first <= 0:
                    return None
                return (last - first) / first

            first_price = float(window["Open"].iloc[0])
            last_close  = float(window["Close"].iloc[-1])
            if first_price <= 0:
                return None
            pct = (last_close - first_price) / first_price
            logger.debug("KOSPI intraday scan: first=%.2f last=%.2f Δ=%.4f%%",
                         first_price, last_close, pct * 100)
            return pct

        except Exception as exc:
            logger.warning("KOSPI intraday fetch failed: %s", exc)
            return None

    @staticmethod
    def _fetch_vnindex_intraday() -> Optional[float]:
        """Fetch VN-Index intraday return from daily_ohlcv (latest session close).

        Returns % change of the most recent VNINDEX close versus previous session close,
        or None on failure.
        """
        try:
            from src.database.db_core import get_connection

            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT date, close FROM daily_ohlcv "
                    "WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT 2"
                ).fetchall()
            if len(rows) < 2:
                return None
            latest = float(rows[0][1])
            prev   = float(rows[1][1])
            if prev <= 0:
                return None
            return (latest - prev) / prev
        except Exception as exc:
            logger.warning("VNINDEX intraday fetch failed: %s", exc)
            return None

    def _asia_canary_check(self) -> Optional[str]:
        """Multi-tiered Adaptive Breaker trigger.

        Returns halt reason string or None.
        """
        kospi_pct = self._fetch_kospi_intraday()
        if kospi_pct is None:
            logger.warning("Asia canary: KOSPI data unavailable, skipping check")
            return None

        # Tier 1 — Absolute Veto Gate (bypass all other conditions)
        if kospi_pct <= -self.ASIA_HALT_KOSPI_TIER1_DROP:
            reason = (
                f"ASIA_TIER1_VETO: KOSPI {kospi_pct*100:+.2f}% | "
                f"breached 1.5% absolute threshold — emergency halt"
            )
            logger.warning("🛑 %s", reason)
            return reason

        # Tier 2 -- Confirmed Canary (KOSPI + VN-Index confirmation)
        if kospi_pct > -self.ASIA_HALT_KOSPI_TIER2_DROP:
            return None  # KOSPI not in panic territory

        vn_pct = self._fetch_vnindex_intraday()
        if vn_pct is None:
            logger.warning("Asia canary Tier 2: VN-Index data unavailable -> fallback halt")
            reason = (
                f"ASIA_TIER2_NO_VN: KOSPI {kospi_pct*100:+.2f}% | "
                f"VN-Index data missing -- precautionary halt"
            )
            return reason

        if vn_pct >= self.ASIA_HALT_VN_CONFIRM_DROP:
            return None  # KOSPI dropped but VN hasn't confirmed contagion

        reason = (
            f"ASIA_TIER2_HALT: KOSPI {kospi_pct*100:+.2f}% | "
            f"VN {vn_pct*100:+.2f}% | confirmed contagion"
        )
        logger.warning("🛑 %s", reason)
        return reason

    def emergency_halt(self, reason: str) -> None:
        """Absolute Kill-Switch — cancel ALL active orders on exchange + future slices.

        Three-step sequence:
          1. Cancel all PENDING slices (internal ledger)
          2. Force-cancel every open broker order for the target symbol (exchange-level)
          3. Mark plan FAILED and persist
        """
        if not self.plan:
            logger.warning("emergency_halt: no active plan")
            return

        logger.warning("🛑 EMERGENCY HALT — %s", reason)

        # Step 1: Cancel future slices (internal)
        remaining = [s for s in self.plan.slices if s.status == "PENDING"]
        for s in remaining:
            s.status = "CANCELED"
            logger.info("  Slice %d → CANCELED", s.index)

        # Step 2: Force-cancel ALL active orders on the exchange
        active_orders = self.broker.get_open_orders(self.symbol)
        for order in active_orders:
            oid = order.get("order_id", "")
            ok = self.broker.cancel_order(oid)
            logger.info("  Broker order %s → %s", oid, "CANCELED" if ok else "CANCEL_FAILED")

        # Step 3: Record final state
        self.plan.status = "FAILED"
        self._persist()
        logger.warning("TWAP plan %s → FAILED (%s)", self.plan.stale_campaign_id[:8], reason)

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

        # Intraday Governor Override: Asia canary check
        halt_reason = self._asia_canary_check()
        if halt_reason:
            self.emergency_halt(halt_reason)
            return {"success": False, "reason": halt_reason}

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

        # Adaptive price from broker's real-time best bid/ask
        if self.side.upper() == "SELL":
            adjusted_price = self.broker.get_best_bid(self.symbol)
        else:
            adjusted_price = self.broker.get_best_ask(self.symbol)
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

    # ── DEFERRED Rollover ────────────────────────────────

    def rollover_deferred(self) -> dict:
        """Amortized DEFERRED rollover — phân bổ đều, không cộng dồn thô.

        Đọc market_impact_threshold từ params_registry.json.
        Mỗi tail slice ≤ threshold × total_capital.
        Nếu slice gốc vượt threshold → tự động bẻ nhỏ.
        """
        if not self.plan:
            return {"success": False, "reason": "NO_PLAN"}

        deferred = [s for s in self.plan.slices if s.status == "DEFERRED"]
        if not deferred:
            return {"success": False, "reason": "NO_DEFERRED"}

        deferred_amount = sum(s.amount for s in deferred)
        last_index = max(s.index for s in self.plan.slices)

        # Đọc Market Impact Threshold từ params_registry
        from src.portfolio.params_registry import load_or_build
        registry = load_or_build()
        threshold = registry.get("market_impact_threshold", 0.1)
        max_slice_amount = threshold * self.sm.total_capital

        # Reference slice size
        original_slice_size = deferred[0].amount
        amortized_size = min(original_slice_size, max_slice_amount)

        # Tạo tail slices amortized
        new_slices = []
        remaining = deferred_amount
        while remaining > 0:
            last_index += 1
            sz = min(amortized_size, remaining)
            new_slices.append(Slice(index=last_index, amount=round(sz, 2)))
            remaining -= sz

        self.plan.slices.extend(new_slices)
        self.plan.n_slices = len(self.plan.slices)
        self._persist()

        return {
            "success": True,
            "deferred_amount": round(deferred_amount, 2),
            "new_tail_slices": len(new_slices),
            "amortized_size": round(amortized_size, 2),
            "threshold": threshold,
            "total_slices": self.plan.n_slices,
        }

    # ── Complete ─────────────────────────────────────────

    def complete_plan(self) -> dict:
        """Kết thúc plan: ghi nhận write-off vào stale_manager.

        Nếu còn DEFERRED → rollover trước khi complete.
        """
        if not self.plan:
            return {"success": False, "reason": "NO_PLAN"}

        # Rollover bất kỳ DEFERRED nào trước khi complete
        deferred = [s for s in self.plan.slices if s.status == "DEFERRED"]
        if deferred:
            return self.rollover_deferred()

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
