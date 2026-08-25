//! lib.rs — ptck_core PyO3 extension entry
//! Exposes `compute_rolling_breadth`, `breadth_ratio` (Step-1) + stub for Step-2.

use pyo3::prelude::*;

mod breadth;
mod simulation;

#[pymodule]
fn ptck_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(breadth::compute_rolling_breadth, m)?)?;
    m.add_function(wrap_pyfunction!(breadth::breadth_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(simulation::simulation_placeholder, m)?)?;
    m.add_function(wrap_pyfunction!(simulation::simulate_grid_parallel, m)?)?;
    Ok(())
}
