"""Sandbox API — Phase 5 Paper Trading Dashboard endpoints."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import APIRouter, Query
from src.execution.paper_broker import PaperBroker, OrderBook, StreamingFeed, Level
from src.execution.twap_executor import TWAPExecutor
from src.portfolio.stale_manager import StalePositionManager

router = APIRouter()

_book: OrderBook | None = None
_broker: PaperBroker | None = None
_feed: StreamingFeed | None = None
_exe: TWAPExecutor | None = None
_mgr: StalePositionManager | None = None
_n_slices: int = 5


def _ensure():
    global _book, _broker, _feed, _exe, _mgr, _n_slices
    if _broker is None:
        _book = OrderBook.build("SANDBOX", mid=100.0, depth_per_level=5000, n_levels=5)
        _broker = PaperBroker(book=_book)
        _feed = StreamingFeed("SANDBOX", base_price=100.0)
        _mgr = StalePositionManager(total_capital=1_000_000)
        _mgr.ingest_stale("v1", 0.13)
        _mgr.ingest_stale("v2", 0.10)
        _exe = TWAPExecutor(_mgr, broker=_broker)


def _dict_level(lvl: Level) -> dict:
    return {"price": lvl.price, "volume": lvl.volume}


@router.get("/orderbook")
def get_orderbook():
    _ensure()
    return {
        "symbol": _book.symbol,
        "bids": [_dict_level(l) for l in _book.bids],
        "asks": [_dict_level(l) for l in _book.asks],
        "mid_price": _book.mid_price(),
        "spread": _book.spread(),
        "last_price": _book.last_price,
        "volatility": _book.volatility,
        "is_halted": _broker.is_trading_halt(),
    }


@router.get("/twap-status")
def get_twap_status():
    _ensure()
    s = _exe.status()
    slices = []
    if _exe.plan:
        for sl in _exe.plan.slices:
            slices.append({
                "index": sl.index,
                "amount": sl.amount,
                "status": sl.status,
                "fill_price": sl.fill_price,
                "filled_qty": sl.filled_qty,
                "slippage": sl.slippage,
                "broker_order_id": sl.broker_order_id,
            })
    return {
        "stale_campaign_id": _exe.plan.stale_campaign_id if _exe.plan else "",
        "total_amount": _exe.plan.total_amount if _exe.plan else 0,
        "n_slices": _exe.plan.n_slices if _exe.plan else 0,
        "slices": slices,
        "status": s["plan_status"],
    }


@router.get("/slippage")
def get_slippage():
    _ensure()
    return _broker.slippage_report()


@router.get("/portfolio")
def get_portfolio():
    _ensure()
    return {
        "total_capital": _mgr.total_capital,
        "stale_pct": sum(_mgr.stale_pcts) if _mgr.stale_pcts else 0,
        "escrow_balance": 0.0,
        "is_locked": False,
    }


@router.get("/halt-status")
def get_halt_status():
    _ensure()
    return {
        "is_halted": _broker.is_trading_halt(),
        "duration": 0.0,
    }


@router.get("/thinning")
def get_thinning():
    _ensure()
    remaining = sum(l.volume for l in _book.bids)
    initial = 5000 * 5 * 0.8
    return {
        "remaining": remaining,
        "thinning_pct": 100 * (1 - remaining / max(initial, 1)),
    }


@router.post("/start")
def start_sandbox(
    n_slices: int = Query(5),
    depth: float = Query(5000),
    price: float = Query(100.0),
    capital: float = Query(1_000_000),
):
    global _book, _broker, _feed, _exe, _mgr, _n_slices
    _n_slices = n_slices
    _book = OrderBook.build("SANDBOX", mid=price, depth_per_level=depth, n_levels=5)
    _broker = PaperBroker(book=_book)
    _feed = StreamingFeed("SANDBOX", base_price=price)
    _mgr = StalePositionManager(total_capital=capital)
    _mgr.ingest_stale("v1", 0.13)
    _mgr.ingest_stale("v2", 0.10)
    _exe = TWAPExecutor(_mgr, broker=_broker)
    _exe.build_plan(n_slices=n_slices)
    return {"status": "started", "n_slices": n_slices, "depth": depth, "price": price}


@router.post("/execute-next")
def execute_next():
    _ensure()
    if not _exe.plan:
        return {"success": False, "reason": "NO_PLAN"}
    next_idx = None
    for sl in _exe.plan.slices:
        if sl.status == "PENDING":
            next_idx = sl.index
            break
    if next_idx is None:
        return {"success": False, "reason": "NO_PENDING_SLICE"}
    bid = _broker.get_best_bid("SANDBOX")
    if bid <= 0:
        r = _exe.handle_liquidity_strike(next_idx, requeue_count=0)
        if r.get("action") == "DEFERRED":
            _exe.rollover_deferred()
        return {"success": True, "action": r.get("action", "REQUEUED"), "slice": next_idx}
    r = _exe.execute_slice(next_idx, price=bid)
    return r


@router.post("/halt")
def trigger_halt():
    _ensure()
    _broker.set_trading_halt(True)
    return {"halted": True}


@router.post("/resume")
def resume_trading():
    _ensure()
    _broker.set_trading_halt(False)
    return {"halted": False}
