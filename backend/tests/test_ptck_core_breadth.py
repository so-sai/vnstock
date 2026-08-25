"""test_ptck_core_breadth.py — TDD: Rust ptck_core vs Pandas parity (np.allclose atol=1e-12)"""

import numpy as np

import ptck_core


def _pandas_breadth(above: np.ndarray) -> np.ndarray:
    """Reference: pandas-equivalent mean(axis=0)"""
    if above.size == 0:
        return np.array([], dtype=np.float64)
    return above.mean(axis=0).astype(np.float64)


class TestRollingBreadthParity:
    def test_random_1700x60_matches_pandas(self):
        rng = np.random.default_rng(42)
        above = rng.integers(0, 2, size=(1700, 60), dtype=np.uint8)
        rust = np.asarray(ptck_core.compute_rolling_breadth(above))
        ref = _pandas_breadth(above)
        assert np.allclose(rust, ref, atol=1e-12), f"max diff {np.max(np.abs(rust - ref))}"

    def test_all_zeros_and_all_ones(self):
        above0 = np.zeros((5, 4), dtype=np.uint8)
        assert np.allclose(ptck_core.compute_rolling_breadth(above0), np.zeros(4), atol=1e-12)
        above1 = np.ones((5, 4), dtype=np.uint8)
        assert np.allclose(ptck_core.compute_rolling_breadth(above1), np.ones(4), atol=1e-12)

    def test_known_small_matrix(self):
        # 3 symbols × 4 days
        above = np.array([[1, 0, 1, 0], [1, 1, 0, 0], [0, 0, 1, 1]], dtype=np.uint8)
        # breadth = [2/3, 1/3, 2/3, 1/3]
        expected = np.array([2 / 3, 1 / 3, 2 / 3, 1 / 3], dtype=np.float64)
        rust = np.asarray(ptck_core.compute_rolling_breadth(above))
        assert np.allclose(rust, expected, atol=1e-12)

    def test_empty_matrix(self):
        above = np.zeros((0, 0), dtype=np.uint8)
        rust = np.asarray(ptck_core.compute_rolling_breadth(above))
        assert rust.size == 0

    def test_breadth_ratio_scalar(self):
        above = np.array([1, 0, 1, 1, 0], dtype=np.uint8)
        assert abs(ptck_core.breadth_ratio(above) - 0.6) < 1e-12
        assert ptck_core.breadth_ratio(np.array([], dtype=np.uint8)) == 0.0
