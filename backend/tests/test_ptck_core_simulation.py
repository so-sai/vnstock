"""test_ptck_core_simulation.py — TDD parity: Rust Rayon vs Python reference (simulate_one mirror)"""

import numpy as np
import ptck_core


def py_simulate_one(factors, weights, entry_thresh=0.60, exit_thresh=0.35,
                     trailing_stop=0.05, trailing_take=0.15, min_hold=5, fee=0.0045):
    """Mirror of simulation.rs::simulate_one — corrected to match Rust exactly."""
    n_days = len(factors) // 4
    nav = 100_000_000.0
    in_pos = False
    entry_nav = 0.0
    hold_days = 0
    wins = sells = 0
    peak = nav
    max_dd = 0.0
    equity = []
    for t in range(n_days):
        base = t * 4
        composite = (weights[0]*factors[base] + weights[1]*factors[base+1] +
                     weights[2]*factors[base+2] + weights[3]*factors[base+3])
        if in_pos:
            hold_days += 1
            entry_ret = (nav - entry_nav) / entry_nav if entry_nav else 0.0
            should_sell = False
            if entry_ret <= -trailing_stop:
                should_sell = True
            if entry_ret >= trailing_take:
                should_sell = True
            if composite < exit_thresh and hold_days >= min_hold:
                should_sell = True
            if should_sell:
                nav *= 1.0 - fee
                in_pos = False
                sells += 1
                if entry_ret > 0:
                    wins += 1
                hold_days = 0
            else:
                drift = (composite - 0.5) * 0.002
                nav *= 1.0 + drift
        elif composite > entry_thresh:
            nav *= 1.0 - fee
            in_pos = True
            entry_nav = nav
            hold_days = 0
        else:
            drift = (composite - 0.5) * 0.0002
            nav *= 1.0 + drift
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak if peak else 0.0
        if dd < max_dd:
            max_dd = dd
        equity.append(nav)
    total_return = nav/100_000_000.0 - 1.0
    years = n_days/365.25
    cagr = (nav/100_000_000.0)**(1.0/years) - 1.0 if years > 0 and nav > 0 else 0.0
    if len(equity) > 1:
        rets = np.diff(np.array(equity))/np.array(equity[:-1])
        mean, std = float(np.mean(rets)), float(np.std(rets))
        sharpe = mean/std*np.sqrt(252) if std > 1e-12 else 0.0
    else:
        sharpe = 0.0
    win_rate = wins/sells if sells else 0.0
    return dict(total_return=total_return, cagr=cagr, sharpe=sharpe,
                max_drawdown=max_dd, win_rate=win_rate, total_trades=sells*2, final_nav=nav)


class TestSimulationParity:
    def test_small_grid_matches_python(self):
        rng = np.random.default_rng(7)
        n_days, n_factors = 60, 4
        n_combos = 8
        factors = rng.uniform(0.2, 0.8, size=(n_days, n_factors)).astype(np.float64)
        weights = rng.uniform(0.1, 0.5, size=(n_combos, n_factors)).astype(np.float64)
        # normalize weights to sum 1 per combo
        weights = weights / weights.sum(axis=1, keepdims=True)

        rust_results = ptck_core.simulate_grid_parallel(factors, weights,
                                                        entry_thresh=0.60, exit_thresh=0.35,
                                                        trailing_stop=0.05, trailing_take=0.15,
                                                        min_hold=5, fee=0.0045)
        assert len(rust_results) == n_combos
        for i in range(n_combos):
            py = py_simulate_one(factors.ravel(), weights[i],
                                 entry_thresh=0.60, exit_thresh=0.35,
                                 trailing_stop=0.05, trailing_take=0.15, min_hold=5)
            rr = rust_results[i]
            for k in ("total_return", "cagr", "sharpe", "max_drawdown", "win_rate", "final_nav"):
                assert abs(py[k] - rr[k]) < 1e-9, f"combo {i} {k}: py {py[k]} vs rust {rr[k]}"
            assert py["total_trades"] == rr["total_trades"]

    def test_empty_grids(self):
        fm = np.zeros((0, 4), dtype=np.float64)
        wg = np.zeros((0, 4), dtype=np.float64)
        assert ptck_core.simulate_grid_parallel(fm, wg) == []

    def test_placeholder(self):
        assert ptck_core.simulation_placeholder() == 42
