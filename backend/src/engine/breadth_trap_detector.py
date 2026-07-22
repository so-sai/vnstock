"""
breadth_trap_detector.py — CUSUM-based Breadth Trap Detector.

Phát hiện Breadth Trap và thời điểm suy yếu.

CUSUM trên Δdivergence (tốc độ thay đổi) thay vì divergence tuyệt đối,
giúp phát hiện sớm khi divergence bắt đầu hội tụ dù cấu trúc chưa hồi phục.
"""

from typing import Optional


class BreadthTrapDetector:
    """CUSUM Breadth Trap Detector.

    Usage:
        d = BreadthTrapDetector(window=20)
        for breadth, so_tru in daily_data:
            state = d.update(breadth, so_tru)
    """

    def __init__(self, window: int = 20, k_factor: float = 0.5, h_factor: float = 3.0):
        self.window = window
        self.k_factor = k_factor
        self.h_factor = h_factor

        self.delta_buffer: list[float] = []
        self.S_plus: float = 0.0
        self.S_minus: float = 0.0
        self.k: float = 0.0
        self.h: float = 0.0
        self.steps: int = 0
        self._prev_divergence: Optional[float] = None
        self._weakening_count: int = 0

    def structural_health(self, so_tru: int) -> float:
        return so_tru / 3.0 * 100

    def _sigma(self) -> float:
        n = len(self.delta_buffer)
        if n < 2:
            return 3.0
        sorted_vals = sorted(self.delta_buffer)
        median = sorted_vals[n // 2]
        abs_devs = sorted(abs(x - median) for x in self.delta_buffer)
        mad = abs_devs[n // 2]
        return max(mad * 1.4826, 1.0)

    def update(self, breadth_pct: float, so_tru: int) -> dict:
        health = self.structural_health(so_tru)
        divergence = breadth_pct - health
        self.steps += 1

        result = {
            "divergence": round(divergence, 2),
            "breadth_pct": round(breadth_pct, 2),
            "so_tru": so_tru,
            "structural_health_pct": round(health, 0),
            "S_plus": 0.0,
            "S_minus": 0.0,
            "k": 0.0,
            "h": 0.0,
            "trap_active": so_tru <= 1 and divergence > 30,
            "trap_deepening": False,
            "trap_weakening": False,
            "ready_for_recovery": False,
            "weakening_streak": 0,
        }

        if self._prev_divergence is not None:
            delta = divergence - self._prev_divergence
            self.delta_buffer.append(delta)
            if len(self.delta_buffer) > self.window:
                self.delta_buffer.pop(0)
        self._prev_divergence = divergence

        # Warmup
        if self.steps <= self.window:
            self.k = self.k_factor * self._sigma()
            self.h = self.h_factor * self._sigma()
            result["k"] = round(self.k, 4)
            result["h"] = round(self.h, 4)
            return result

        # CUSUM on delta
        sigma = self._sigma()
        self.k = self.k_factor * sigma
        self.h = self.h_factor * sigma

        mu = sum(self.delta_buffer) / len(self.delta_buffer)
        epsilon = delta - mu

        self.S_plus = max(0.0, self.S_plus + epsilon - self.k)
        self.S_minus = min(0.0, self.S_minus + epsilon + self.k)

        result["S_plus"] = round(self.S_plus, 4)
        result["S_minus"] = round(self.S_minus, 4)
        result["k"] = round(self.k, 4)
        result["h"] = round(self.h, 4)

        trap_active = so_tru <= 1 and divergence > 30
        deepening = trap_active and self.S_plus > self.h
        weakening = trap_active and self.S_minus < -self.h

        if weakening:
            self._weakening_count += 1
        elif not trap_active:
            self._weakening_count = 0

        result["trap_active"] = trap_active
        result["trap_deepening"] = deepening
        result["trap_weakening"] = weakening
        result["ready_for_recovery"] = weakening and self._weakening_count >= 2
        result["weakening_streak"] = self._weakening_count
        return result

    def reset(self) -> None:
        self.delta_buffer.clear()
        self.S_plus = 0.0
        self.S_minus = 0.0
        self.k = 0.0
        self.h = 0.0
        self.steps = 0
        self._prev_divergence = None
        self._weakening_count = 0
