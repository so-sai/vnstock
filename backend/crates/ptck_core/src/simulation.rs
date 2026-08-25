//! simulation.rs — Placeholder for Phase-2 Grid Search Rayon kernel.
//! Will host `grid_search_batch` / `unified_replay_step` with `rayon::par_iter`.
//! Kept minimal for Step-1 scaffold so the crate builds cleanly.

use pyo3::prelude::*;

/// Dummy stub — proves the module links. Real kernel lands in Step-2.
#[pyfunction]
pub fn simulation_placeholder() -> usize {
    42
}
