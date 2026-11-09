# -*- coding: utf-8 -*-
"""Kernel PyO3 `ptck_core`: "không tính được" phải RAISE, không được trả 0.0.

[WHY]
    `breadth_ratio([]) -> 0.0` và `compute_fused_breadth(window > n_days) -> zeros(...)` là
    đúng mẫu In-band Error Signaling của sự cố musl/OpenSSH: một *giá trị hợp lệ* (0,0) được
    dùng để báo *không có kết quả*. Với breadth, 0,0 KHÔNG trung tính — nó là trạng thái thật
    "0% cổng phiếu trên MA20" (capitulation), nên dữ liệu rỗng/thiếu bị đọc thành tín hiệu
    sập toàn thị trường. Đúng AGENTS §1/§4A: "không biết" ≠ 0.

[ROOT CAUSE]
    Ba nhánh `return Ok(...0.0/zeros...)` trong `backend/crates/ptck_core/src/breadth.rs`
    coi đầu vào rỗng là "breadth bằng 0" thay vì "không có gì để tính".

[INVARIANT]
    (a) ma trận/vector rỗng hoặc `window` không hợp lệ ⇒ `ValueError` phía Python;
    (b) chống vá quá tay: mọi đầu vào HỢP LỆ vẫn cho kết quả y hệt pandas (parity cũ giữ
        nguyên ở `test_ptck_core_breadth.py`);
    (c) `n_days == 0` (không có ngày nào) vẫn trả mảng rỗng — đó là "không có ngày", một sự
        thật, không phải một con số bịa.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

ptck_core = pytest.importorskip(
    "ptck_core", reason="ptck_core chưa build (maturin develop) — test này cần kernel thật"
)


def test_breadth_ratio_empty_raises_instead_of_zero():
    """[WHY] `0.0` khi không có mã nào là con số bịa mang hình dạng tín hiệu thật."""
    with pytest.raises(ValueError, match="empty"):
        ptck_core.breadth_ratio(np.array([], dtype=np.uint8))


def test_rolling_breadth_with_zero_symbols_raises():
    """[WHY] 0 mã × 5 ngày: không thể nói "0% mã trên MA20" khi không có mã nào để đếm."""
    with pytest.raises(ValueError, match="empty"):
        ptck_core.compute_rolling_breadth(np.zeros((0, 5), dtype=np.uint8))


def test_fused_breadth_window_longer_than_history_raises():
    """[WHY] MA20 trên 5 phiên là KHÔNG TÍNH ĐƯỢC. Trả zeros ⇒ "0% trên MA20" — một kết luận
    không có cơ sở, dùng để chặn/rào tín hiệu là sai.
    """
    prices = np.arange(15, dtype=np.float64).reshape(3, 5)
    with pytest.raises(ValueError, match="window"):
        ptck_core.compute_fused_breadth(prices, window=20)


def test_fused_breadth_zero_window_raises():
    """[WHY] `window=0` là tham số vô nghĩa; im lặng trả zeros là che lỗi gọi hàm."""
    prices = np.arange(30, dtype=np.float64).reshape(3, 10)
    with pytest.raises(ValueError, match="window"):
        ptck_core.compute_fused_breadth(prices, window=0)


def test_zero_days_still_returns_empty_array():
    """[WHY] Chống vá quá tay: "không có ngày nào" là sự thật, trả mảng rỗng chứ không raise —
    nếu raise ở đây, mọi lần chạy trên cửa sổ rỗng (ví dụ mã mới niêm yết) sẽ nổ.
    """
    prices = np.zeros((3, 0), dtype=np.float64)
    out = np.asarray(ptck_core.compute_fused_breadth(prices, window=20))
    assert out.size == 0


def test_valid_inputs_still_match_pandas_contract():
    """[WHY] Chống vá quá tay theo chiều ngược lại: đổi hợp đồng lỗi KHÔNG được đổi kết quả
    của đầu vào hợp lệ (parity pandas là lý do kernel này tồn tại).
    """
    above = np.array([[1, 0, 1, 0], [1, 1, 0, 0], [0, 0, 1, 1]], dtype=np.uint8)
    assert np.allclose(
        np.asarray(ptck_core.compute_rolling_breadth(above)),
        np.array([2 / 3, 1 / 3, 2 / 3, 1 / 3]),
        atol=1e-12,
    )
    assert abs(ptck_core.breadth_ratio(np.array([1, 0, 1, 1, 0], dtype=np.uint8)) - 0.6) < 1e-12


def test_simulation_returns_all_metric_keys():
    """[WHY] Nhánh dựng dict ở biên PyO3 từng dùng `.unwrap()` cho TỪNG `set_item`. Đổi sang `?`
    không được làm mất/sai khóa nào — nếu không, `grid_search_v2.py:821` với `zip(..., strict=True)`
    sẽ nổ lệch cột, hoặc tệ hơn là đọc `None` thành số.
    """
    rng = np.random.default_rng(3)
    factors = rng.uniform(-1.0, 1.0, size=(40, 4)).astype(np.float64)
    weights = np.array(
        [[0.25, 0.25, 0.25, 0.25], [0.50, 0.20, 0.20, 0.10]], dtype=np.float64
    )
    out = ptck_core.simulate_grid_parallel(factors, weights)

    assert len(out) == 2, "mỗi bộ trọng số phải trả đúng một dict"
    expected_keys = {
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "total_trades",
        "final_nav",
    }
    for row in out:
        assert set(row) == expected_keys
        assert all(isinstance(row[k], (int, float)) for k in expected_keys)


def test_simulation_rejects_wrong_factor_count():
    """[WHY] Chống vá quá tay: thêm `?` KHÔNG được nuốt lỗi hợp đồng — sai số factor vẫn phải là
    `ValueError` rõ ràng (caller `grid_search_v2` dựa vào thông điệp này khi log).
    """
    factors = np.zeros((10, 3), dtype=np.float64)  # 3 factor thay vì 4
    weights = np.zeros((1, 4), dtype=np.float64)
    with pytest.raises(ValueError, match="expected 4 factors"):
        ptck_core.simulate_grid_parallel(factors, weights)
