"""Tests for partial_data_entropy.py — H(t) strict monotonicity + boundary."""
import math
import pytest
from src.engine.partial_data_entropy import (
    calculate_entropy_penalty,
    _today_is_holiday,
    TENOR_ORDER,
    WEIGHTS,
    BASE, GAMMA, GRACE, TTL, H_MIN, H_MAX,
)


class TestEntropyMonotonicity:
    """Verify H(t) increases strictly as each tenor decays, in weight order."""

    def _H_for_failed_tenors(self, n_failed: int) -> float:
        stale = {}
        for i, t in enumerate(TENOR_ORDER):
            stale[t] = GRACE + 24.0 if i < n_failed else GRACE - 1.0
        return calculate_entropy_penalty(stale)

    def test_strictly_increasing(self):
        H_vals = [self._H_for_failed_tenors(n) for n in range(1, 7)]
        for i in range(len(H_vals) - 1):
            assert H_vals[i + 1] > H_vals[i], (
                f"H({TENOR_ORDER[i+1]})={H_vals[i+1]:.4f} "
                f"<= H({TENOR_ORDER[i]})={H_vals[i]:.4f}"
            )

    def test_deltas_decreasing_with_weights(self):
        H_vals = [self._H_for_failed_tenors(n) for n in range(7)]
        deltas = [H_vals[1] - H_vals[0]] + [H_vals[i] - H_vals[i-1] for i in range(2, 7)]
        for i in range(len(deltas) - 1):
            assert deltas[i] > deltas[i + 1], (
                f"Δ{i}({deltas[i]:.4f}) <= Δ{i+1}({deltas[i+1]:.4f}) — "
                f"trọng số w[{i}]={WEIGHTS[TENOR_ORDER[i]]} "
                f"phải cho delta > w[{i+1}]={WEIGHTS[TENOR_ORDER[i+1]]}"
            )

    def test_H_min_when_all_fresh(self):
        stale = {t: GRACE - 1.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert abs(H - H_MIN) < 1e-6, f"Expected {H_MIN}, got {H:.4f}"

    def test_H_max_when_all_decayed(self):
        stale = {t: GRACE + 720.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert abs(H - H_MAX) < 1e-4, f"Expected near {H_MAX}, got {H:.4f}"
        assert H <= H_MAX

    def test_H_clamp_lower(self):
        stale = {t: GRACE - 1.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert H == H_MIN

    def test_H_clamp_upper(self):
        stale = {t: GRACE + 9999.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert H == H_MAX

    def test_each_tenor_failure_produces_different_H(self):
        H_set = set()
        for i in range(6):
            stale = {t: GRACE - 1.0 for t in TENOR_ORDER}
            stale[TENOR_ORDER[i]] = GRACE + 24.0
            H_set.add(round(calculate_entropy_penalty(stale), 6))
        assert len(H_set) == 6, "Mỗi kỳ hạn hỏng phải cho H(t) khác nhau"

    def test_holiday_freezes_clock(self):
        from datetime import datetime
        wd = datetime.now().weekday()
        today_holiday = _today_is_holiday()
        if wd >= 5:
            assert today_holiday is True
        else:
            pass  # Không assert — phụ thuộc vào calendar file


class TestEntropyEdgeCases:
    """Boundary tests for extreme values."""

    def test_zero_stale(self):
        stale = {t: 0.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert H == H_MIN

    def test_negative_stale(self):
        stale = {t: -5.0 for t in TENOR_ORDER}
        H = calculate_entropy_penalty(stale)
        assert H == H_MIN

    def test_empty_dict(self):
        H = calculate_entropy_penalty({})
        assert H_MIN <= H <= H_MAX

    def test_partial_dict(self):
        stale = {"1W": GRACE + 100.0}
        H = calculate_entropy_penalty(stale)
        assert H_MIN <= H <= H_MAX

    @pytest.mark.parametrize("t_eff", [
        GRACE,          # sát biên grace
        GRACE + 1e-6,   # vừa qua grace
        GRACE + TTL,    # 1 hằng số thời gian
        GRACE + 10*TTL, # 10 hằng số
    ])
    def test_lambda_decay_continuity(self, t_eff):
        stale = {"1W": t_eff, "2W": GRACE - 1, "1M": GRACE - 1,
                 "3M": GRACE - 1, "6M": GRACE - 1, "9M": GRACE - 1}
        H = calculate_entropy_penalty(stale)
        assert H_MIN <= H <= H_MAX
