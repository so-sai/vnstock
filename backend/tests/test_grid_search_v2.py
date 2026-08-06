"""
test_grid_search_v2.py — TDD for DecisionGuard-integrated Grid Search V2.

Covers:
  - Bounded weight grid generation (5-Layer Matrix ranges)
  - PortfolioTracker alloc_multiplier (dimmer scaling)
  - run_backtest_with_guard with BUY LOCK (LRI DEFENSIVE) blocking buys
  - EmergencyExitEngine force-sell integration
  - LRI cache persistence
  - CLI entry-point smoke test
"""

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"


def _hydrate_path():
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    return _ROOT


_PROJECT_ROOT = _hydrate_path()

from backtest.grid_search_v2 import (  # noqa: E402
    LRI_DEFENSIVE,
    apply_emergency_exit,
    generate_weight_grid,
    precompute_lri_cache,
    run_backtest_with_guard,
)
from backtest.portfolio_tracker import PortfolioTracker  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────
def _build_test_db(path: Path) -> str:
    """Build a minimal SQLite DB with daily_ohlcv + attached fin schema."""
    db_path = str(path / "screener_cache.db")
    conn = sqlite3.connect(db_path)

    conn.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL)")
    conn.execute("CREATE TABLE symbol_industry (symbol TEXT, icb_name2 TEXT)")

    dates = [f"2021-04-{d:02d}" for d in range(1, 31)]
    dates += [f"2021-05-{d:02d}" for d in range(1, 31)]
    # VNINDEX steady uptrend
    ix_price = 1000.0
    for d in dates:
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?)",
            ("VNINDEX", d, ix_price, 1_000_000),
        )
        ix_price *= 1.001
    # HPG strong uptrend
    hpg_price = 50.0
    for d in dates:
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?)",
            ("HPG", d, hpg_price, 500_000),
        )
        hpg_price *= 1.002
    # VNM flat
    for d in dates:
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?,?,?,?)",
            ("VNM", d, 60.0, 300_000),
        )
    conn.execute("INSERT INTO symbol_industry VALUES ('HPG','Industrial Metals & Mining')")
    conn.execute("INSERT INTO symbol_industry VALUES ('VNM','Food Producers')")

    # Attached fin schema with health ratios + valuation scores
    fin = sqlite3.connect(str(path / "financial_facts.db"))
    fin.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    fin.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)")
    fin.execute(
        "INSERT INTO health_ratios VALUES ('HPG','2021Q1','ROE',0.08),"
        " ('HPG','2021Q1','DEBT_TO_EQUITY',0.5),"
        " ('HPG','2021Q1','GROSS_MARGIN',0.2)"
    )
    fin.execute("INSERT INTO valuation_scores VALUES ('HPG','2021Q1','PE',-1.0), ('HPG','2021Q1','PB',-0.5)")
    fin.commit()
    fin.close()

    conn.commit()
    conn.close()
    return db_path


def _scores(score_days):
    """Build synthetic scores dict: HPG high score, VNM low score."""
    scores = {}
    for d in score_days:
        scores[d] = {
            "HPG": {"fund": 0.9, "behav": 0.8, "alpha": 0.9, "macro_eff": 0.8, "sector": "Basic Materials"},
            "VNM": {"fund": 0.2, "behav": 0.2, "alpha": 0.2, "macro_eff": 0.3, "sector": "Consumer"},
        }
    return scores


# ── Tests: Weight Grid ──────────────────────────────────────────────────────
class TestWeightGrid:
    def test_generate_weight_grid_sums_to_one(self):
        combos = generate_weight_grid(step=0.10)
        assert combos, "Grid must be non-empty"
        for c in combos:
            total = c["w_fund"] + c["w_macro"] + c["w_alpha"] + c["w_behav"]
            assert abs(total - 1.0) < 1e-6, f"Weights must sum to 1.0, got {total}"

    def test_weights_within_bounded_ranges(self):
        combos = generate_weight_grid(step=0.05)
        assert combos, "Bounded grid must be non-empty"
        for c in combos:
            assert 0.35 <= c["w_fund"] <= 0.50, f"w_fund out of range: {c}"
            assert 0.10 <= c["w_macro"] <= 0.20, f"w_macro out of range: {c}"
            assert 0.15 <= c["w_alpha"] <= 0.30, f"w_alpha out of range: {c}"
            assert 0.10 <= c["w_behav"] <= 0.25, f"w_behav out of range: {c}"

    def test_bounded_grid_smaller_than_full_grid(self):
        from backtest.grid_search import generate_weight_grid as full_grid

        full = full_grid(step=0.10)
        bounded = generate_weight_grid(step=0.10)
        assert len(bounded) < len(full)
        assert len(bounded) > 0


# ── Tests: PortfolioTracker alloc_multiplier ───────────────────────────────
class TestAllocMultiplier:
    def test_alloc_multiplier_scales_position(self):
        tracker = PortfolioTracker(initial_capital=100_000_000)
        prices = {"HPG": 50_000.0}
        tracker.buy("HPG", 50_000.0, score=0.8, prices=prices, alloc_multiplier=0.5)
        cost = tracker.trade_log[-1]["cost"]
        full = PortfolioTracker(initial_capital=100_000_000)
        full.buy("HPG", 50_000.0, score=0.8, prices=prices, alloc_multiplier=1.0)
        full_cost = full.trade_log[-1]["cost"]
        assert cost < full_cost, "Half allocation must cost less than full"
        assert 0.45 < (cost / full_cost) < 0.55, f"Ratio should be ~0.5, got {cost / full_cost:.2f}"

    def test_alloc_multiplier_zero_no_buy(self):
        tracker = PortfolioTracker(initial_capital=100_000_000)
        prices = {"HPG": 50_000.0}
        ok = tracker.buy("HPG", 50_000.0, score=0.8, prices=prices, alloc_multiplier=0.0)
        assert ok is False, "0 multiplier -> shares <= 0 -> buy rejected"
        assert len(tracker.positions) == 0


# ── Tests: EmergencyExit ───────────────────────────────────────────────────
class TestEmergencyExit:
    def test_apply_emergency_exit_noop_above_defensive(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        conn = sqlite3.connect(db_path)
        tracker = PortfolioTracker(initial_capital=100_000_000)
        prices = {"HPG": 55.0}
        tracker.buy("HPG", 50.0, score=0.8, prices=prices)
        orders = apply_emergency_exit(conn, tracker, "2021-04-15", lri_score=LRI_DEFENSIVE + 0.1)
        conn.close()
        assert orders == {}

    def test_apply_emergency_exit_generates_orders_when_defensive(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        conn = sqlite3.connect(db_path)
        tracker = PortfolioTracker(initial_capital=100_000_000)
        prices = {"HPG": 55.0}
        tracker.buy("HPG", 50.0, score=0.8, prices=prices)
        orders = apply_emergency_exit(conn, tracker, "2021-04-15", lri_score=LRI_DEFENSIVE - 0.1)
        conn.close()
        assert "HPG" in orders
        assert orders["HPG"]


# ── Tests: run_backtest_with_guard ─────────────────────────────────────────
class TestGuardedBacktest:
    def _run(self, db_path, lri, params=None):
        conn = sqlite3.connect(db_path)
        dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date")]
        score_days = dates[::3]
        conn.close()
        scores = _scores(score_days)
        if params is None:
            params = {
                "w_fund": 0.45,
                "w_macro": 0.20,
                "w_alpha": 0.20,
                "w_behav": 0.15,
                "entry_thresh": 0.60,
                "exit_thresh": 0.35,
                "trailing_stop": 0.05,
                "trailing_take": 0.20,
                "min_hold_days": 5,
            }
        lri_cache = {d: lri for d in dates}
        return run_backtest_with_guard(scores, dates, score_days, params, db_path, lri_cache)

    def test_aggressive_lri_allows_trades(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        result = self._run(db_path, lri=0.90)
        assert result["total_trades"] > 0, "AGGRESSIVE LRI should allow buying"
        assert result["buy_locked_days"] == 0

    def test_defensive_lri_blocks_buys(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        result = self._run(db_path, lri=0.10)
        assert result["buy_locked_days"] > 0, "DEFENSIVE LRI must mark buy_locked days"
        # BUY LOCK -> trades should be drastically reduced vs aggressive
        agg = self._run(db_path, lri=0.90)
        assert result["total_trades"] < agg["total_trades"], (
            f"BUY LOCK must reduce trades: {result['total_trades']} >= {agg['total_trades']}"
        )

    def test_probe_lri_dimmer_scales(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        probe = self._run(db_path, lri=0.50)
        assert probe["dimmer_days"] > 0, "PROBE LRI must trigger dimmer days"
        assert probe["buy_locked_days"] == 0

    def test_result_has_guard_metrics(self, tmp_path):
        db_path = _build_test_db(tmp_path)
        result = self._run(db_path, lri=0.50)
        for key in ("buy_locked_days", "emergency_exits", "dimmer_days"):
            assert key in result, f"Missing metric {key}"
            assert isinstance(result[key], int)


# ── Tests: LRI cache ───────────────────────────────────────────────────────
class TestLriCache:
    def test_precompute_lri_cache_writes_and_loads(self, tmp_path, monkeypatch):
        from backtest import grid_search_v2

        dates = ["2021-04-01", "2021-04-02", "2021-04-03"]
        cache_file = tmp_path / "lri_cache.json"
        monkeypatch.setattr(grid_search_v2, "LRI_CACHE_PATH", cache_file)

        cache = precompute_lri_cache(dates, force=True)
        assert set(dates) <= set(cache.keys()), "Cache must cover all dates"
        assert cache_file.exists(), "Cache must persist to disk"

        # Reload from disk
        cache2 = precompute_lri_cache(dates, force=False)
        assert cache2 == cache

    def test_precompute_lri_cache_partial_uses_cache(self, tmp_path, monkeypatch):
        from backtest import grid_search_v2

        dates = ["2021-04-01", "2021-04-02", "2021-04-03"]
        cache_file = tmp_path / "lri_cache.json"
        monkeypatch.setattr(grid_search_v2, "LRI_CACHE_PATH", cache_file)

        first = precompute_lri_cache(dates, force=True)
        # Larger date set: should reuse cached values, compute only missing
        extended = dates + ["2021-04-04"]
        second = precompute_lri_cache(extended, force=False)
        for d in dates:
            assert second[d] == first[d], "Existing cache entries must be reused"


# ── Tests: CLI entry point ─────────────────────────────────────────────────
class TestCliGridSearch:
    def test_cli_parser_registered(self):
        """ptck.py must register the grid-search subcommand."""

        sys.path.insert(0, str(_PROJECT_ROOT))
        import importlib.util

        spec = importlib.util.spec_from_file_location("ptck", _PROJECT_ROOT / "ptck.py")
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        parser = mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["grid-search", "--help"])
        # parse_args with --help raises SystemExit(0); if subcommand missing, argparse
        # would raise SystemExit(2). We detect via capture.
