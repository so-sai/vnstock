//! lib.rs — ptck_core PyO3 extension entry
//! Exposes `compute_rolling_breadth`, `breadth_ratio` (Step-1) + stub for Step-2.
//!
//! [WHY] `#![forbid(unsafe_code)]` ở crate này vì toàn bộ kernel là số học thuần trên `ndarray`
//! và Rayon — PyO3/numpy lo phần FFI an toàn. Không có lý do chính đáng nào để có `unsafe` ở
//! đây; nếu ai đó thêm vào, biên dịch PHẢI đỏ (đây là "compiler làm hộ reviewer" — shift-left).
#![forbid(unsafe_code)]

use pyo3::prelude::*;

mod breadth;
mod simulation;

#[pymodule]
fn ptck_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(breadth::compute_rolling_breadth, m)?)?;
    m.add_function(wrap_pyfunction!(breadth::breadth_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(breadth::compute_fused_breadth, m)?)?;
    m.add_function(wrap_pyfunction!(simulation::simulation_placeholder, m)?)?;
    m.add_function(wrap_pyfunction!(simulation::simulate_grid_parallel, m)?)?;
    Ok(())
}
