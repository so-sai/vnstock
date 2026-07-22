"""PaperBroker — Sandbox API cho Phase 5 UAT & Paper Trading Simulation.

Mô hình fill Depth-Weighted + Slippage Almgren-Chriss (square-root impact).
Streaming L1/L2, network failure injection, empty book scenarios.
"""

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from typing import Optional

from src.execution.twap_executor import BrokerAPI


# ── Order Book ─────────────────────────────────────────────

@dataclass
class Level:
    price: float
    volume: float


@dataclass
class OrderBook:
    """L1/L2 order book with configurable depth.

    bids: sorted descending (highest bid first)
    asks: sorted ascending (lowest ask first)
    """
    symbol: str
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    last_price: float = 100.0
    volatility: float = 0.02

    @classmethod
    def build(cls, symbol: str, mid: float = 100.0, spread: float = 0.5,
              depth_per_level: float = 5000, n_levels: int = 5,
              volatility: float = 0.02):
        """Build a symmetric order book."""
        bids = []
        asks = []
        for i in range(n_levels):
            offset = spread / 2 + i * 0.1
            bids.append(Level(price=round(mid - offset - i * 0.05, 2),
                              volume=depth_per_level * (1 - i * 0.1)))
            asks.append(Level(price=round(mid + offset + i * 0.05, 2),
                              volume=depth_per_level * (1 - i * 0.1)))
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        return cls(symbol=symbol, bids=bids, asks=asks,
                   last_price=mid, volatility=volatility)

    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else float("inf")

    def mid_price(self) -> float:
        bb = self.best_bid()
        ba = self.best_ask()
        if bb > 0 and ba < float("inf"):
            return round((bb + ba) / 2, 2)
        return self.last_price

    def spread(self) -> float:
        bb = self.best_bid()
        ba = self.best_ask()
        if bb > 0 and ba < float("inf"):
            return round(ba - bb, 2)
        return 0.0

    def depth_for_sell(self, limit_price: float) -> list:
        """All bid levels at or above limit_price (available to hit)."""
        return [l for l in self.bids if l.price >= limit_price]

    def depth_for_buy(self, limit_price: float) -> list:
        """All ask levels at or below limit_price (available to lift)."""
        return [l for l in self.asks if l.price <= limit_price]

    def consume(self, side: str, limit_price: float, qty: float) -> dict:
        """Destructively consume depth — Order Book Thinning simulation.

        Reduces volume at consumed levels. Fully drained levels stay at 0
        so subsequent orders see thinner book.
        Returns: {filled_qty, avg_fill_price, slippage, status}
        """
        mid = self.mid_price()
        if side.upper() == "SELL":
            levels = [l for l in self.bids if l.price >= limit_price]
            levels.sort(key=lambda x: x.price, reverse=True)
        else:
            levels = [l for l in self.asks if l.price <= limit_price]
            levels.sort(key=lambda x: x.price)

        if not levels or sum(l.volume for l in levels) <= 0:
            return {"filled_qty": 0, "status": "PENDING"}

        running_qty = 0.0
        running_notional = 0.0
        for lvl in levels:
            take = min(lvl.volume, qty - running_qty)
            lvl.volume = round(lvl.volume - take, 2)
            running_qty += take
            running_notional += take * lvl.price
            if running_qty >= qty:
                break

        avg_price = running_notional / running_qty
        slippage = abs(avg_price - mid) / mid
        is_full = running_qty >= qty - 1e-9
        return {
            "filled_qty": round(running_qty, 2),
            "fill_price": round(avg_price, 2),
            "slippage": round(slippage, 6),
            "status": "FILLED" if is_full else "PARTIAL_FILLED",
        }

    def random_walk(self, sigma: Optional[float] = None) -> None:
        """Advance one tick: geometric Brownian motion with spread refresh."""
        s = sigma or self.volatility
        ret = random.gauss(0, s / sqrt(252 * 6.5 * 60))
        shift = self.last_price * ret
        new_mid = self.last_price + shift
        spread = self.spread() or 0.5
        self.last_price = new_mid
        # Shift all levels
        bid_shift = new_mid - (self.bids[0].price + self.asks[0].price) / 2 if self.bids and self.asks else 0
        for lvl in self.bids:
            lvl.price = round(lvl.price + bid_shift, 2)
        for lvl in self.asks:
            lvl.price = round(lvl.price + bid_shift, 2)
        # Re-sort
        self.bids.sort(key=lambda x: x.price, reverse=True)
        self.asks.sort(key=lambda x: x.price)

    def drain_bids(self) -> None:
        """Set all bid volumes to 0 — simulate Buyer Strike."""
        for l in self.bids:
            l.volume = 0

    def drain_asks(self) -> None:
        """Set all ask volumes to 0 — simulate Seller Strike."""
        for l in self.asks:
            l.volume = 0

    def restore_depth(self, vol: float = 5000) -> None:
        """Restore depth after drain."""
        for i, l in enumerate(self.bids):
            l.volume = vol * (1 - i * 0.1)
        for i, l in enumerate(self.asks):
            l.volume = vol * (1 - i * 0.1)

    def freeze(self) -> dict:
        """Snapshot and clear book — Trading Halt."""
        snapshot = {
            "bids": [(l.price, l.volume) for l in self.bids],
            "asks": [(l.price, l.volume) for l in self.asks],
            "last_price": self.last_price,
        }
        for l in self.bids:
            l.volume = 0
        for l in self.asks:
            l.volume = 0
        return snapshot

    def unfreeze(self, snapshot: dict) -> None:
        """Restore book from freeze snapshot."""
        for i, (price, vol) in enumerate(snapshot.get("bids", [])):
            if i < len(self.bids):
                self.bids[i].price = price
                self.bids[i].volume = vol
        for i, (price, vol) in enumerate(snapshot.get("asks", [])):
            if i < len(self.asks):
                self.asks[i].price = price
                self.asks[i].volume = vol
        self.last_price = snapshot.get("last_price", self.last_price)


# ── Paper Broker ───────────────────────────────────────────

class PaperBroker(BrokerAPI):
    """Sandbox broker with depth-weighted fill + slippage model.

    Almgren-Chriss inspired:
      - Fill quantity = min(order_qty, cumulative_depth_at_limit)
      - Fill price = VWAP of consumed depth levels
      - Slippage = |fill_price - mid_price| / mid_price
    """

    def __init__(self, book: Optional[OrderBook] = None,
                 impact_coeff: float = 0.3,
                 hourly_volume: float = 500_000):
        super().__init__()
        self.book = book or OrderBook.build("SANDBOX")
        self.impact_coeff = impact_coeff
        self.hourly_volume = hourly_volume
        self._network_down: bool = False
        self._trading_halt: bool = False
        self._freeze_snapshot: Optional[dict] = None
        self._slippage_log: list[dict] = []

    # ── Network & Halt simulation ──────────────────────────

    def set_network_failure(self, down: bool) -> None:
        self._network_down = down

    def set_trading_halt(self, halt: bool) -> None:
        """Trading Halt: freeze book, NO interpolation, reject orders."""
        self._trading_halt = halt
        if halt:
            self._freeze_snapshot = self.book.freeze()
        elif self._freeze_snapshot:
            self.book.unfreeze(self._freeze_snapshot)
            self._freeze_snapshot = None

    def is_trading_halt(self) -> bool:
        return self._trading_halt

    def ping(self, timeout: float = 2) -> bool:
        if self._network_down or self._trading_halt:
            return False
        return True

    # ── Market data ────────────────────────────────────────

    def get_best_bid(self, symbol: str) -> float:
        if self._trading_halt:
            return 0.0
        return self.book.best_bid()

    def get_best_ask(self, symbol: str) -> float:
        if self._trading_halt:
            return float("inf")
        return self.book.best_ask()

    # ── Order placement with fill simulation ──────────────

    def place_limit_order(self, symbol: str, side: str,
                          quantity: float, price: float) -> str:
        oid = f"PAPER_{self._next_id + 1:06d}"
        self._next_id += 1

        if self._trading_halt:
            order = {
                "order_id": oid, "symbol": symbol, "side": side,
                "quantity": quantity, "filled_qty": 0.0,
                "price": price, "fill_price": None,
                "status": "HALTED",
                "slippage": 0.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._orders[oid] = order
            return oid

        fill = self._simulate_fill(side, quantity, price)
        order = {
            "order_id": oid, "symbol": symbol, "side": side,
            "quantity": quantity, "filled_qty": fill.get("filled_qty", 0.0),
            "price": price, "fill_price": fill.get("fill_price"),
            "status": fill["status"],
            "slippage": fill.get("slippage", 0.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._orders[oid] = order

        if fill.get("slippage", 0) > 0:
            self._slippage_log.append({
                "oid": oid, "side": side,
                "qty": fill.get("filled_qty", 0),
                "slippage": fill["slippage"],
                "fill_price": fill.get("fill_price"),
                "mid": self.book.mid_price(),
            })

        return oid

    def _simulate_fill(self, side: str, qty: float,
                       limit_price: float) -> dict:
        """Depth-weighted fill with destructive Order Book Thinning.

        Consumes depth from live OrderBook — subsequent orders see
        reduced liquidity, simulating real market impact.
        """
        return self.book.consume(side, limit_price, qty)

    # ── Impact verification (Almgren-Chriss) ───────────────

    def expected_impact(self, qty: float) -> float:
        """Theoretical square-root impact for verification."""
        sigma = self.book.volatility
        participation = qty / max(self.hourly_volume, 1)
        return self.impact_coeff * sigma * math.sqrt(participation)

    def slippage_report(self) -> dict:
        """Summary statistics of all slippage events this session."""
        if not self._slippage_log:
            return {"n": 0}
        slps = [s["slippage"] for s in self._slippage_log]
        return {
            "n": len(slps),
            "max": round(max(slps), 6),
            "mean": round(sum(slps) / len(slps), 6),
            "total_qty": round(sum(s["qty"] for s in self._slippage_log), 2),
        }


# ── Streaming Feed ─────────────────────────────────────────

HALT_DETECTION_INTERVAL_S = 300  # 5 phút không có tick → halt


class StreamingFeed:
    """Synthetic L1/L2 streaming — sends tick updates to a PaperBroker.

    Tự động phát hiện Trading Halt khi không có tick > HALT_DETECTION_INTERVAL_S.
    """

    def __init__(self, symbol: str = "SANDBOX",
                 base_price: float = 100.0,
                 volatility: float = 0.02,
                 halt_timeout: float = HALT_DETECTION_INTERVAL_S):
        self.book = OrderBook.build(symbol, mid=base_price, volatility=volatility)
        self.tick_count = 0
        self._last_tick_time: float = 0.0
        self._halt_timeout = halt_timeout
        self._halted: bool = False
        self._halt_start: Optional[float] = None

    def tick(self) -> OrderBook:
        """Advance one tick (random walk)."""
        self.book.random_walk()
        self.tick_count += 1
        self._last_tick_time = time.time()
        if self._halted:
            self._halted = False
            self._halt_start = None
        return self.book

    def check_halt(self, now: Optional[float] = None) -> bool:
        """Auto-detect trading halt: no ticks for > halt_timeout seconds."""
        if self.tick_count == 0:
            return False
        elapsed = (now or time.time()) - self._last_tick_time
        if elapsed > self._halt_timeout and not self._halted:
            self._halted = True
            self._halt_start = time.time()
        return self._halted

    def is_halted(self) -> bool:
        return self._halted

    def halt_duration(self) -> float:
        if not self._halt_start:
            return 0.0
        return time.time() - self._halt_start

    def force_halt(self) -> None:
        self._halted = True
        self._halt_start = time.time()

    def force_resume(self) -> None:
        self._halted = False
        self._halt_start = None

    def drain(self, side: str = "SELL") -> None:
        if side.upper() == "SELL":
            self.book.drain_bids()
        else:
            self.book.drain_asks()

    def restore(self, vol: float = 5000) -> None:
        self.book.restore_depth(vol)
