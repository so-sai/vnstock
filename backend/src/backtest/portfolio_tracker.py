"""
PortfolioTracker — Extracted capital management for multi-factor backtest.

Encapsulates BUY/SELL execution with proper accounting:
- NAV = cash + Σ(positions × market_price)
- Position sizing capped at initial_capital (no win-streak compounding)
- Transaction costs: buy 0.45%, sell 0.45%
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PortfolioTracker:
    """Core capital management extracted from unified_system_replay for TDD."""

    initial_capital: float = 100_000_000.0
    cash: float = 100_000_000.0
    invested: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)  # symbol -> shares
    entry_prices: dict[str, float] = field(default_factory=dict)  # symbol -> exec_price
    trade_log: list[dict] = field(default_factory=list)
    max_positions: int = 10
    cash_reserve: float = 0.05
    buy_fee: float = 0.0045  # 0.45%
    sell_fee: float = 0.0045  # 0.45%
    trailing_stop: float = 0.05
    trailing_take: float = 0.15
    entry_threshold: float = 0.50
    exit_threshold: float = 0.40

    def nav(self, prices: dict[str, float]) -> float:
        """Total NAV = cash + Σ(shares × current_price)."""
        total = self.cash
        for sym, shares in self.positions.items():
            p = prices.get(sym, 0)
            total += shares * p
        return total

    def capital_per_stock(self, prices: dict[str, float]) -> float:
        """Position sizing: FIXED at initial_capital, never grows with NAV."""
        return self.initial_capital * (1 - self.cash_reserve) / self.max_positions

    def buy(
        self,
        symbol: str,
        price: float,
        score: float,
        prices: dict[str, float],
        alloc_multiplier: float = 1.0,
        date: str = "",
    ) -> bool:
        """Execute BUY. Returns True if executed.

        Args:
            alloc_multiplier: Scale factor for position sizing (0.0-1.0).
                Used by DecisionGuard he_so_giam_ty_trong (dimmer scaling).
                1.0 = full allocation, 0.5 = half, 0.0 = no position.
            date: ISO date of execution — ghi vào trade_log để xuất ledger thật.
        """
        if symbol in self.positions:
            return False
        if len(self.positions) >= self.max_positions:
            return False

        cps = self.capital_per_stock(prices)
        alloc = min(cps, self.cash * 0.95) * max(0.0, min(1.0, alloc_multiplier))
        exec_price = price * 1.002  # slippage
        shares = int(alloc / exec_price)
        if shares <= 0:
            return False

        cost = shares * exec_price * (1 + self.buy_fee)
        if cost > self.cash:
            return False

        self.cash -= cost
        self.invested += cost
        self.positions[symbol] = shares
        self.entry_prices[symbol] = exec_price
        self.trade_log.append(
            {
                "date": date,
                "symbol": symbol,
                "action": "BUY",
                "price": exec_price,
                "shares": shares,
                "score": score,
                "cost": cost,
            }
        )
        return True

    def sell(self, symbol: str, price: float, score: float, prices: dict[str, float], date: str = "") -> bool:
        """Execute SELL. Returns True if executed.

        date: ISO date of execution — ghi vào trade_log để xuất ledger thật.
        """
        if symbol not in self.positions:
            return False

        current_shares = self.positions[symbol]
        exec_price = price * 0.998  # slippage
        entry = self.entry_prices.get(symbol, exec_price)
        pnl = (exec_price - entry) / entry if entry else 0

        # Return cost basis × (1 + pnl) — net of sell fee
        original_cost = current_shares * entry * (1 + self.buy_fee) if entry else 0
        return_amount = original_cost * (1 + pnl) * (1 - self.sell_fee)

        self.cash += return_amount
        self.invested -= original_cost
        self.trade_log.append(
            {
                "date": date,
                "symbol": symbol,
                "action": "SELL",
                "price": exec_price,
                "shares": current_shares,
                "score": score,
                "pnl_pct": pnl * 100,
            }
        )
        self.positions.pop(symbol, None)
        self.entry_prices.pop(symbol, None)
        return True

    def should_sell(self, symbol: str, price: float, score: float) -> bool:
        """Check exit conditions: trailing stop, take profit, or score exit."""
        if symbol not in self.positions:
            return False
        entry = self.entry_prices.get(symbol)
        if entry is None:
            return score < self.exit_threshold
        pnl = (price - entry) / entry
        if pnl <= -self.trailing_stop:
            return True
        if pnl >= self.trailing_take:
            return True
        if score < self.exit_threshold:
            return True
        return False


def annualize_cagr(
    total_ret: float,
    start_date: str,
    end_date: str,
) -> float:
    """Annualized CAGR theo năm LỊCH: (1+r)^(365.25/elapsed_days) - 1.

    Dùng ngày lịch thực (không phải 252 ngày giao dịch) để khớp chuẩn tài chính
    (NAV_đầu/NAV_cuối)^(1/năm) - 1 với năm = thời gian lịch trôi qua.
    """
    from datetime import date

    try:
        d0 = date.fromisoformat(start_date)
        d1 = date.fromisoformat(end_date)
        elapsed_days = max((d1 - d0).days, 1)
    except TypeError, ValueError:
        return 0.0
    years = elapsed_days / 365.25
    if years <= 0:
        return 0.0
    return (1 + total_ret) ** (1 / years) - 1
