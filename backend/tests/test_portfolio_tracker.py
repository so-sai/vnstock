"""
TDD Sanity Checks for PortfolioTracker — Capital Explosion Prevention.

7 "Death Tests" that must ALL pass before PortfolioTracker is allowed
to touch real market data. Each test isolates a specific failure mode
of the capital accounting engine.
"""

from backtest.portfolio_tracker import PortfolioTracker

# ── Test 1: Conservation of Wealth ──────────────────────────────────────
# Buy at price P, price doesn't change, sell immediately.
# NAV must equal initial minus fees. No phantom money created.


def test_conservation_of_wealth():
    """Buy then sell at same price — NAV must equal initial minus fees."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}

    tracker.buy("HPG", 50_000.0, score=0.8, prices=prices)
    tracker.sell("HPG", 50_000.0, score=0.8, prices=prices)
    nav = tracker.nav(prices)

    # Fees: buy 0.45% + sell 0.45% = ~0.9% total
    assert nav < 100_000_000, f"NAV must be less than initial (fees), got {nav / 1e6:.2f}M"
    assert nav > 100_000_000 * 0.97, f"NAV shouldn't drop more than 3%, got {nav / 1e6:.2f}M"
    assert len(tracker.positions) == 0
    assert tracker.invested == 0


# ── Test 2: Loss Test — NAV decreases on -5% drop ──────────────────────
# Buy, price drops -5%, sell. NAV must decrease. Cash must increase
# by less than initial investment.


def test_loss_decreases_nav():
    """Stock drops -5%, trailing stop fires. NAV must decrease proportionally."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}

    tracker.buy("HPG", 50_000.0, score=0.8, prices=prices)
    cost = tracker.trade_log[-1]["cost"]
    entry_price = tracker.entry_prices["HPG"]

    # Price drops -5% from entry — must trigger trailing stop
    drop_price = entry_price * 0.95
    assert tracker.should_sell("HPG", drop_price, score=0.8)

    cash_before = tracker.cash

    tracker.sell("HPG", drop_price, score=0.8, prices={"HPG": drop_price})

    # Key invariant: cash increases by LESS than what we invested (loss)
    cash_gained = tracker.cash - cash_before
    assert cash_gained < cost, f"Cash gained ({cash_gained:.0f}) should be less than cost ({cost:.0f})"

    # NAV must have decreased
    nav_after = tracker.nav({"HPG": drop_price})
    assert nav_after < 100_000_000, f"NAV should decrease on loss, got {nav_after / 1e6:.2f}M"
    assert nav_after > 100_000_000 * 0.90, f"NAV shouldn't drop >10%, got {nav_after / 1e6:.2f}M"


# ── Test 3: Gain Test — NAV increases on +15% rise ─────────────────────
# Buy, price rises +15%, sell. NAV must increase. Cash must increase
# by MORE than initial investment.


def test_gain_increases_nav():
    """Stock rises +15%, take profit fires. NAV must increase proportionally."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}

    tracker.buy("HPG", 50_000.0, score=0.8, prices=prices)
    cost = tracker.trade_log[-1]["cost"]
    entry_price = tracker.entry_prices["HPG"]

    # Price rises +15% from entry — use 15.1% to avoid float edge case
    rise_price = entry_price * 1.151
    assert tracker.should_sell("HPG", rise_price, score=0.8)

    cash_before = tracker.cash
    tracker.sell("HPG", rise_price, score=0.8, prices={"HPG": rise_price})

    # Key invariant: cash increases by MORE than what we invested (profit)
    cash_gained = tracker.cash - cash_before
    assert cash_gained > cost, f"Cash gained ({cash_gained:.0f}) should exceed cost ({cost:.0f})"

    # NAV must have increased
    nav_after = tracker.nav({"HPG": rise_price})
    assert nav_after > 100_000_000, f"NAV should increase on gain, got {nav_after / 1e6:.2f}M"


# ── Test 4: Position Sizing Cap ─────────────────────────────────────────
# After winning trades, NAV grows. Position sizing must NOT grow with it.


def test_position_sizing_cap():
    """Capital per stock must be FIXED at initial_capital — never changes."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}

    cps_initial = tracker.capital_per_stock(prices)
    expected_cps = 100_000_000 * 0.95 / 10
    assert abs(cps_initial - expected_cps) < 1.0

    # Even with NAV doubling, sizing stays fixed
    tracker.cash = 200_000_000
    cps_doubled = tracker.capital_per_stock(prices)
    assert cps_doubled == cps_initial, f"CPS should be FIXED: {cps_doubled / 1e6:.1f}M != {cps_initial / 1e6:.1f}M"


# ── Test 5: Multiple Positions Don't Leak Capital ───────────────────────


def test_multiple_positions_no_leak():
    """3 sequential buy/sell cycles — no phantom money."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"A": 100.0, "B": 200.0, "C": 300.0}
    initial = tracker.cash

    for sym, px in prices.items():
        tracker.buy(sym, px, score=0.8, prices=prices)
        tracker.sell(sym, px, score=0.8, prices=prices)

    final = tracker.cash
    assert final < initial, "Cash should decrease from fees"
    assert final > initial * 0.95, "Shouldn't lose more than 5%"
    assert len(tracker.positions) == 0
    assert tracker.invested == 0


# ── Test 6: Sell Non-Existent Position ──────────────────────────────────


def test_sell_nonexistent_is_noop():
    """Selling a symbol not held must be a no-op."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}
    cash_before = tracker.cash
    result = tracker.sell("HPG", 50_000.0, score=0.8, prices=prices)
    assert result is False
    assert tracker.cash == cash_before


# ── Test 7: Buy Already-Held Position ───────────────────────────────────


def test_buy_already_held_is_noop():
    """Buying a symbol already held must be a no-op."""
    tracker = PortfolioTracker(initial_capital=100_000_000)
    prices = {"HPG": 50_000.0}
    tracker.buy("HPG", 50_000.0, score=0.8, prices=prices)
    result = tracker.buy("HPG", 50_000.0, score=0.8, prices=prices)
    assert result is False
    assert len(tracker.positions) == 1
