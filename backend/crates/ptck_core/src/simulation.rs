//! simulation.rs — Rayon Parallel Grid-Search Kernel (Step 2)
//!
//! Simplified but faithful to `grid_search_v2.run_backtest_with_guard`:
//! - Input: factor_matrix (n_days × 4) [fund, macro_eff, alpha, behav] + weights_grid (n_combos × 4)
//! - Per-combo loop over days: composite = w·factors[t], entry/exit + trailing stop/take + min_hold
//! - Metrics: total_return, cagr (365.25), sharpe (√252), max_drawdown, win_rate, total_trades, final_nav
//! - Parallel: `weights_grid.into_par_iter()` with `py.allow_threads` (zero GIL, zero pickle)

use numpy::{PyArray1, PyReadonlyArray2};
use pyo3::prelude::*;
use rayon::prelude::*;

// ── SimulationResult exposed to Python as dict-like via PyO3 ───────────────

#[derive(Clone, Debug)]
pub struct SimResult {
    pub total_return: f64,   // fraction (0.05 = 5%)
    pub cagr: f64,
    pub sharpe: f64,
    pub max_drawdown: f64, // fraction negative
    pub win_rate: f64,     // 0..1
    pub total_trades: usize,
    pub final_nav: f64,
}

// ── Core single-combo simulation (pure Rust, no GIL) ───────────────────────

fn simulate_one(
    factors: &[f64], // flat: n_days * 4, row-major
    n_days: usize,
    weights: &[f64; 4],
    entry_thresh: f64,
    exit_thresh: f64,
    trailing_stop: f64,
    trailing_take: f64,
    min_hold: usize,
    initial_cap: f64,
    fee: f64, // 0.0045 = 0.45% per round-trip split
) -> SimResult {
    let mut nav = initial_cap;
    let mut in_pos = false;
    let mut entry_nav = 0.0;
    let mut hold_days: usize = 0;
    let mut wins: usize = 0;
    let mut sells: usize = 0;
    let mut peak = nav;
    let mut max_dd: f64 = 0.0;
    let mut equity: Vec<f64> = Vec::with_capacity(n_days);

    for t in 0..n_days {
        let base = t * 4;
        let composite = weights[0] * factors[base]
            + weights[1] * factors[base + 1]
            + weights[2] * factors[base + 2]
            + weights[3] * factors[base + 3];

        if in_pos {
            hold_days += 1;
            // Synthetic daily P&L proxy: composite maps to expected daily drift.
            // For scaffold we model nav change as composite scaled drift.
            // Entry/exit logic mirrors grid_search_v2: stop/take on composite-implied pnl + threshold cross.
            let entry_ret = (nav - entry_nav) / entry_nav;
            let mut should_sell = false;
            if entry_ret <= -trailing_stop {
                should_sell = true;
            }
            if entry_ret >= trailing_take {
                should_sell = true;
            }
            if composite < exit_thresh && hold_days >= min_hold {
                should_sell = true;
            }
            if should_sell {
                // apply fee on exit
                nav *= 1.0 - fee;
                in_pos = false;
                sells += 1;
                if entry_ret > 0.0 {
                    wins += 1;
                }
                hold_days = 0;
            } else {
                // drift while in position
                let drift = (composite - 0.5) * 0.002; // small daily drift around neutral
                nav *= 1.0 + drift;
            }
        } else if composite > entry_thresh {
            // buy
            nav *= 1.0 - fee;
            in_pos = true;
            entry_nav = nav;
            hold_days = 0;
        } else {
            // flat: tiny drift
            let drift = (composite - 0.5) * 0.0002;
            nav *= 1.0 + drift;
        }

        if nav > peak {
            peak = nav;
        }
        let dd = (nav - peak) / peak;
        if dd < max_dd {
            max_dd = dd;
        }
        equity.push(nav);
    }

    // Metrics
    let total_return = (nav / initial_cap) - 1.0;
    let years = (n_days as f64) / 365.25;
    let cagr = if years > 0.0 && nav > 0.0 {
        (nav / initial_cap).powf(1.0 / years) - 1.0
    } else {
        0.0
    };
    let sharpe = if equity.len() > 1 {
        let rets: Vec<f64> = equity.windows(2).map(|w| (w[1] - w[0]) / w[0]).collect();
        let mean = rets.iter().sum::<f64>() / (rets.len() as f64);
        let var = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (rets.len() as f64);
        let std = var.sqrt();
        if std > 1e-12 {
            mean / std * (252.0f64).sqrt()
        } else {
            0.0
        }
    } else {
        0.0
    };
    let win_rate = if sells > 0 {
        (wins as f64) / (sells as f64)
    } else {
        0.0
    };

    SimResult {
        total_return,
        cagr,
        sharpe,
        max_drawdown: max_dd,
        win_rate,
        total_trades: sells * 2, // buy+sell pairs
        final_nav: nav,
    }
}

// ── PyO3 entry: parallel grid ──────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (factor_matrix, weights_grid, entry_thresh=0.60, exit_thresh=0.35, trailing_stop=0.05, trailing_take=0.15, min_hold=5, fee=0.0045))]
pub fn simulate_grid_parallel<'py>(
    py: Python<'py>,
    factor_matrix: PyReadonlyArray2<'py, f64>,
    weights_grid: PyReadonlyArray2<'py, f64>,
    entry_thresh: f64,
    exit_thresh: f64,
    trailing_stop: f64,
    trailing_take: f64,
    min_hold: usize,
    fee: f64,
) -> PyResult<Vec<PyObject>> {
    let fm = factor_matrix.as_array();
    let wg = weights_grid.as_array();
    let n_days = fm.nrows();
    let n_factors = fm.ncols();
    let n_combos = wg.nrows();
    let wg_cols = wg.ncols();

    if n_factors != 4 || wg_cols != 4 {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "expected 4 factors, got factor_matrix {}×{} and weights_grid {}×{}",
            n_days, n_factors, n_combos, wg_cols
        )));
    }
    if n_days == 0 || n_combos == 0 {
        return Ok(vec![]);
    }

    // Copy into flat contiguous Vec for zero-copy inside Rayon (avoid ndarray borrow across threads)
    let factors_flat: Vec<f64> = fm.iter().copied().collect();
    let weights_flat: Vec<f64> = wg.iter().copied().collect();

    // Release GIL for the entire parallel section
    let results: Vec<SimResult> = py.allow_threads(|| {
        (0..n_combos)
            .into_par_iter()
            .map(|ci| {
                let base = ci * 4;
                let w = [
                    weights_flat[base],
                    weights_flat[base + 1],
                    weights_flat[base + 2],
                    weights_flat[base + 3],
                ];
                simulate_one(
                    &factors_flat,
                    n_days,
                    &w,
                    entry_thresh,
                    exit_thresh,
                    trailing_stop,
                    trailing_take,
                    min_hold,
                    100_000_000.0,
                    fee,
                )
            })
            .collect()
    });

    // Convert to Python list of dicts (PyO3 stable)
    let out: Vec<PyObject> = results
        .into_iter()
        .map(|r| {
            let d = pyo3::types::PyDict::new_bound(py);
            d.set_item("total_return", r.total_return).unwrap();
            d.set_item("cagr", r.cagr).unwrap();
            d.set_item("sharpe", r.sharpe).unwrap();
            d.set_item("max_drawdown", r.max_drawdown).unwrap();
            d.set_item("win_rate", r.win_rate).unwrap();
            d.set_item("total_trades", r.total_trades).unwrap();
            d.set_item("final_nav", r.final_nav).unwrap();
            d.into_pyobject(py).unwrap().into_any().unbind()
        })
        .collect();
    Ok(out)
}

/// Scalar stub for smoke test (kept from Step 1)
#[pyfunction]
pub fn simulation_placeholder() -> usize {
    42
}
