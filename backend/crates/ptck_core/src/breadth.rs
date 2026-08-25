//! breadth.rs — Kernel 1: rolling breadth (pure &[f64]/&[u8], zero-copy, Rayon-ready)
//!
//! Input:  boolean matrix `above` shape (n_symbols, n_days), where 1 = Price > MA20
//! Output: breadth per day shape (n_days,) = mean(above[:, day])  (0.0 .. 1.0)
//! Matches pandas: `above.mean(axis=0)` exactly; verified via `np.allclose`.
//!
//! Also exposes scalar helper `breadth_ratio` for single-slice parity checks.

use numpy::{PyArray1, PyReadonlyArray2, PyReadonlyArray1};
use pyo3::prelude::*;
use rayon::prelude::*;

/// Breadth per day from 2-D boolean matrix (u8 0/1).
/// `above` shape (n_symbols, n_days) — row-major from NumPy (C-contiguous assumed).
#[pyfunction]
pub fn compute_rolling_breadth<'py>(
    py: Python<'py>,
    above: PyReadonlyArray2<'py, u8>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let arr = above.as_array();
    let (n_sym, n_days) = (arr.nrows(), arr.ncols());
    if n_sym == 0 || n_days == 0 {
        return Ok(PyArray1::<f64>::zeros(py, 0, false));
    }
    // Fast path: iterate columns, parallel over days with Rayon.
    // NumPy ndarray is (n_sym, n_days) C-order: arr[[i,j]] is at i*n_days + j.
    // We read via `arr[[i,j]]` — bounds-checked but still vectorized; for max speed
    // the 2-D view could be flattened and chunked, but correctness first.
    let mut out = vec![0.0f64; n_days];
    // Par over days — each day sums n_sym u8 values.
    out.par_iter_mut().enumerate().for_each(|(j, slot)| {
        let mut cnt: usize = 0;
        for i in 0..n_sym {
            // SAFETY: in-bounds by loop limits.
            if arr[[i, j]] != 0 {
                cnt += 1;
            }
        }
        *slot = (cnt as f64) / (n_sym as f64);
    });
    Ok(PyArray1::from_vec(py, out))
}

/// Scalar breadth for a single day (1-D slice): mean(above)
#[pyfunction]
pub fn breadth_ratio(py: Python<'_>, above: PyReadonlyArray1<u8>) -> PyResult<f64> {
    let arr = above.as_array();
    if arr.is_empty() {
        return Ok(0.0);
    }
    let cnt: usize = arr.iter().map(|&v| (v != 0) as usize).sum();
    let _ = py; // keep GIL token explicit
    Ok((cnt as f64) / (arr.len() as f64))
}
