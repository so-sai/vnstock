"""adaptive_cusum.py — Online Structural Break Detection with Robust Statistics.

Phát hiện gãy cấu trúc thời gian thực (Online Structural Break) cho chuỗi
Regime Score. Dùng CUSUM (Cumulative Sum Control Chart) với:

  - Rolling MAD (Median Absolute Deviation) thay vì Standard Deviation
    → miễn nhiễm với fat tails.
  - Adaptive parameters: k_t, h_t được tính từ sigma_estimate_t mỗi phiên.
  - O(1) CPU, O(window) RAM — phù hợp SLA 60s của ACID.

CUSUM theory:
  ε_t = RS_t - μ_target
  S_t  = max(0, S_{t-1} + ε_t - k_t)    # upper CUSUM
  S_t⁻ = min(0, S_{t-1}⁻ + ε_t + k_t)   # lower CUSUM
  HARD_RESET if S_t > h_t or S_t⁻ < -h_t

References:
  - Page (1954). "Continuous Inspection Schemes"
  - Hawkins & Olwell (1998). "Cumulative Sum Charts and Charting for Quality Improvement"
"""
import math
from collections import deque
from typing import Optional

import numpy as np


# ── Hằng số ---------------------------------------------------------

DEFAULT_WINDOW = 20       # rolling window cho MAD
K_FACTOR = 0.5            # k = K_FACTOR × sigma_estimate
H_FACTOR = 5.0            # h = H_FACTOR × sigma_estimate (fat-tail safe)
EMA_SPAN = 3              # smoothing cho delta (chống noise 1 phiên)
SIGMA_FLOOR = 2.0         # sigma tối thiểu cho RS [0, 100] scale


# ── Rolling MAD (Robust Scale) ─────────────────────────────────────

class RollingMAD:
    """Độ lệch tuyệt đối trung vị cuộn (Rolling Median Absolute Deviation).

    MAD = median(|x_i - median(x)|) × 1.4826
    1.4826 là factor chuẩn hóa để MAD ≈ σ cho phân phối chuẩn.
    """

    def __init__(self, window: int = DEFAULT_WINDOW):
        self.window = window
        self.buffer: deque[float] = deque(maxlen=window)

    def push(self, value: float):
        self.buffer.append(value)

    @property
    def value(self) -> float:
        """MAD × 1.4826 (robust sigma estimate)."""
        if len(self.buffer) < 2:
            return SIGMA_FLOOR
        median = float(np.median(list(self.buffer)))
        abs_devs = [abs(x - median) for x in self.buffer]
        mad = float(np.median(abs_devs))
        sigma = max(mad * 1.4826, SIGMA_FLOOR)
        return sigma

    @property
    def mean(self) -> float:
        """Trung bình cuộn (dùng làm μ_target)."""
        if not self.buffer:
            return 0.0
        return float(np.mean(list(self.buffer)))

    def reset(self):
        self.buffer.clear()


# ── Online EMA ─────────────────────────────────────────────────────

class OnlineEMA:
    """Exponential Moving Average — O(1) memory."""

    def __init__(self, span: int = EMA_SPAN):
        self.alpha = 2.0 / (span + 1)
        self.value: Optional[float] = None

    def push(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = (1.0 - self.alpha) * self.value + self.alpha * x
        return self.value

    def reset(self):
        self.value = None


# ── Adaptive CUSUM ─────────────────────────────────────────────────

class AdaptiveCUSUM:
    """CUSUM thích nghi với rolling MAD và adaptive parameters.

    Usage:
        cusum = AdaptiveCUSUM(window=20)
        for rs in regime_score_series:
            if cusum.update(rs):
                print("HARD RESET at", cusum.break_point)
                cusum.reset()

    Attributes:
        S_plus:   Upper CUSUM statistic
        S_minus:  Lower CUSUM statistic
        k:        Allowable slack (current)
        h:        Decision interval (current)
        break_point: Số phiên kể từ lần reset gần nhất khi break xảy ra
    """

    def __init__(self, window: int = DEFAULT_WINDOW):
        self.window = window
        self.rolling_mad = RollingMAD(window)
        self.smoother = OnlineEMA(span=EMA_SPAN)

        # CUSUM state
        self.S_plus: float = 0.0
        self.S_minus: float = 0.0
        self.k: float = 0.0
        self.h: float = 0.0
        self.steps_since_reset: int = 0
        self.break_point: Optional[int] = None

        # Historical sigma estimates (for diagnostics)
        self.sigma_history: deque[float] = deque(maxlen=window)

    def _compute_adaptive_params(self) -> tuple[float, float]:
        """Tính k_t, h_t từ rolling sigma estimate."""
        sigma = self.rolling_mad.value
        self.sigma_history.append(sigma)
        return K_FACTOR * sigma, H_FACTOR * sigma

    def update(self, regime_score: float) -> bool:
        """Cập nhật CUSUM với Regime Score mới.

        Args:
            regime_score: Regime Score hiện tại [0, 100].

        Returns:
            True nếu HARD_RESET được kích hoạt.
        """
        self.steps_since_reset += 1

        # Warmup: không trigger break trong window đầu tiên
        # (chưa đủ dữ liệu để ước lượng sigma)
        if self.steps_since_reset <= self.window:
            self.rolling_mad.push(regime_score)
            self.smoother.push(regime_score)
            self.k, self.h = self._compute_adaptive_params()
            return False

        # Push vào rolling MAD
        self.rolling_mad.push(regime_score)

        # Target mean từ rolling window
        mu_target = self.rolling_mad.mean

        # Smoothed RS (EMA chống noise 1 phiên)
        smoothed_rs = self.smoother.push(regime_score)

        # Deviation từ mean
        epsilon = smoothed_rs - mu_target

        # Adaptive parameters
        self.k, self.h = self._compute_adaptive_params()

        # CUSUM update
        self.S_plus = max(0.0, self.S_plus + epsilon - self.k)
        self.S_minus = min(0.0, self.S_minus + epsilon + self.k)

        # Check for break
        if self.S_plus > self.h or self.S_minus < -self.h:
            self.break_point = self.steps_since_reset
            return True

        return False

    @property
    def current_sigma(self) -> float:
        """Sigma estimate hiện tại (rolling MAD × 1.4826)."""
        return self.rolling_mad.value

    def reset(self):
        """Reset CUSUM state (gọi sau Hard Reset hoặc khởi tạo)."""
        self.S_plus = 0.0
        self.S_minus = 0.0
        self.k = 0.0
        self.h = 0.0
        self.steps_since_reset = 0
        self.break_point = None
        self.rolling_mad.reset()
        self.smoother.reset()
        self.sigma_history.clear()

    def diagnose(self) -> dict:
        """Trả về thông tin chẩn đoán."""
        return {
            "S_plus": round(self.S_plus, 4),
            "S_minus": round(self.S_minus, 4),
            "k": round(self.k, 4),
            "h": round(self.h, 4),
            "sigma": round(self.current_sigma, 4),
            "steps_since_reset": self.steps_since_reset,
            "break_point": self.break_point,
        }
