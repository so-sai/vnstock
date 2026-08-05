"""
recovery_governor.py — Dual CUSUM Structure Recovery Velocity Governor.

Kiến trúc:
  dS/dt = 0.20 * Δfresh_ratio + 0.50 * Δso_tru + 0.30 * Δbreadth_momentum
  Dual CUSUM: S+ (phục hồi) và S- (suy thoái).
  Giải ngân từng phần: 0% → 20% → 50% → 100%.

Hiệu chuẩn đề xuất (tham khảo backtest 2022):
  k = 0.02 (allowance - nhiễu nền)
  h₁ = 0.10 (probe threshold)
  h₂ = 0.25 (enhanced threshold)
  h₃ = 0.50 (full threshold)
  h  = 0.15 (cutoff threshold)
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_data_dir() -> Path:
    """Resolve data directory for whitelist file."""
    try:
        from src.config import DATA_DIR

        return Path(DATA_DIR)
    except ImportError:
        probe = Path(__file__).resolve().parent.parent.parent / "data"
        if probe.is_dir():
            return probe
        return Path(".")


def load_erl_whitelist(data_dir: Path | None = None) -> list[dict]:
    """Đọc erl_whitelist.json — danh sách 30 cổ phiếu kháng cự tốt nhất."""
    if data_dir is None:
        data_dir = _resolve_data_dir()
    path = data_dir / "erl_whitelist.json"
    if not path.exists():
        logger.info("erl_whitelist.json chưa tồn tại — chưa có ERL scan nào.")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("whitelist", [])  # type: ignore[no-any-return]
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning("Lỗi đọc erl_whitelist.json: %s", e)
        return []


# ── Mặc định (có thể override qua calibrate) ──
# Calibrated via Grid Search on 2022 data (70/30 OOS, 241 rows)
DEFAULT_K = 0.04
DEFAULT_H1 = 0.05
DEFAULT_H2 = 0.15
DEFAULT_H3 = 0.35
DEFAULT_H = 0.15

# Trọng số dS/dt
W_DELTA_FRESH = 0.20
W_DELTA_SOTRU = 0.50
W_DELTA_BREADTH = 0.30

# Mức vị thế
POS_0 = 0.0
POS_PROBE = 0.20
POS_ENHANCED = 0.50
POS_FULL = 1.0


class RecoveryGovernor:
    """Dual CUSUM Governor cho phục hồi cấu trúc.

    Usage:
        gov = RecoveryGovernor.get_instance()
        decision = gov.update(fresh_ratio=0.6, so_tru=2, breadth_momentum=5.0)
        # decision["position_level"] = 0.20 (probe)
        # decision["S_plus"] = 0.15
        # decision["S_minus"] = 0.0
    """

    _instance: "RecoveryGovernor" | None = None

    def __init__(
        self,
        k: float = DEFAULT_K,
        h1: float = DEFAULT_H1,
        h2: float = DEFAULT_H2,
        h3: float = DEFAULT_H3,
        h: float = DEFAULT_H,
    ):
        self.k = k
        self.h1 = h1
        self.h2 = h2
        self.h3 = h3
        self.h = h

        # CUSUM state
        self.S_plus: float = 0.0
        self.S_minus: float = 0.0

        # Previous values for delta computation
        self._prev_fresh: float | None = None
        self._prev_sotru: int | None = None
        self._prev_breadth_mom: float | None = None

        # Warm-up counter (ngày liên tục không bị veto)
        self.warmup_days: int = 0
        self._prev_veto: bool = True  # bắt đầu từ trạng thái veto

        # Lịch sử (cho backtest)
        self.history: list[dict] = []

        # ERL Whitelist cache
        self._whitelist: list[dict] = []

    @classmethod
    def get_instance(cls) -> "RecoveryGovernor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    # ── Public API ──

    def update(
        self,
        fresh_ratio: float,
        so_tru: int,
        breadth_momentum: float,
        today: str | None = None,
    ) -> dict:
        """Tính toán vị thế dựa trên dS/dt và Dual CUSUM.

        Args:
            fresh_ratio: từ StaleTracker (0.0-1.0)
            so_tru: số trụ cấu trúc (0-3)
            breadth_momentum: từ regime_history (%, có thể âm)

        Returns:
            dict với position_level, S+, S-, warmup, etc.
        """
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")

        # ── Tính dS/dt ──
        dS_dt = 0.0
        if self._prev_fresh is not None and self._prev_sotru is not None:
            dfresh = fresh_ratio - self._prev_fresh
            dsotru = (so_tru - self._prev_sotru) / 3.0  # chuẩn hóa về [0,1]
            dbreadth = (breadth_momentum - (self._prev_breadth_mom or 0)) / 100.0  # chuẩn hóa

            dS_dt = W_DELTA_FRESH * dfresh + W_DELTA_SOTRU * dsotru + W_DELTA_BREADTH * dbreadth

        # Lưu cho lần sau
        self._prev_fresh = fresh_ratio
        self._prev_sotru = so_tru
        self._prev_breadth_mom = breadth_momentum

        # ── Dual CUSUM ──
        self.S_plus = max(0.0, self.S_plus + dS_dt - self.k)
        self.S_minus = max(0.0, self.S_minus - dS_dt - self.k)

        # ── ERL Whitelist: tự động nạp khi S+ > h1 ──
        if self.S_plus > self.h1:
            if not self._whitelist:
                self._whitelist = load_erl_whitelist()
                if self._whitelist:
                    logger.info(
                        "S+ = %.4f > h1 = %.2f — ERL Whitelist (%d mã sẵn sàng PROBE 20%%).",
                        self.S_plus,
                        self.h1,
                        len(self._whitelist),
                    )
        else:
            self._whitelist = []

        # ── Position sizing ──
        position_level = POS_0
        position_label = "CASH_ONLY"
        position_reason = []

        if self.S_minus > self.h:
            position_level = POS_0
            position_label = "CASH_ONLY_CUTOFF"
            position_reason.append(f"S-={self.S_minus:.3f} > h={self.h} — hard cutoff")
        elif self.S_plus > self.h3 and so_tru >= 2:
            position_level = POS_FULL
            position_label = "FULL"
            position_reason.append(f"S+={self.S_plus:.3f} > h3={self.h3} + so_tru={so_tru}/3")
        elif self.S_plus > self.h2 and self.warmup_days >= 5:
            position_level = POS_ENHANCED
            position_label = "ENHANCED"
            position_reason.append(f"S+={self.S_plus:.3f} > h2={self.h2} + warmup={self.warmup_days}d")
        elif self.S_plus > self.h1:
            position_level = POS_PROBE
            position_label = "PROBE"
            n_wl = len(self._whitelist)
            position_reason.append(f"S+={self.S_plus:.3f} > h1={self.h1} — PROBE entry (ERL Whitelist: {n_wl} mã)")
        else:
            position_reason.append(f"S+={self.S_plus:.3f} <= h1={self.h1} — chưa đủ tín hiệu phục hồi")

        record = {
            "today": today,
            "fresh_ratio": round(fresh_ratio, 4),
            "so_tru": so_tru,
            "breadth_momentum": round(breadth_momentum, 2),
            "dS_dt": round(dS_dt, 6),
            "S_plus": round(self.S_plus, 6),
            "S_minus": round(self.S_minus, 6),
            "warmup_days": self.warmup_days,
            "position_level": position_level,
            "position_label": position_label,
            "reason": "; ".join(position_reason),
            "erl_whitelist_count": len(self._whitelist),
            "erl_whitelist": self._whitelist[:30] if self._whitelist else [],
        }
        self.history.append(record)

        # Giới hạn lịch sử
        if len(self.history) > 500:
            self.history = self.history[-500:]

        return record

    def record_veto(self, is_veto: bool):
        """Ghi nhận trạng thái veto để đếm warm-up.

        Gọi từ decision_guard sau khi xác định veto.
        """
        if not is_veto:
            if self._prev_veto:
                # Vừa thoát veto — reset CUSUM để tránh nhiễu tích lũy cũ
                self.S_plus = 0.0
                self.S_minus = 0.0
                self.warmup_days = 0
            else:
                self.warmup_days += 1
        else:
            self.warmup_days = 0
        self._prev_veto = is_veto

    def get_whitelist(self) -> list[dict]:
        """Lấy ERL Whitelist hiện tại (Top 30 stocks kháng cự)."""
        return self._whitelist

    def reset(self):
        """Reset toàn bộ state (dùng cho backtest calibration)."""
        self.S_plus = 0.0
        self.S_minus = 0.0
        self._prev_fresh = None
        self._prev_sotru = None
        self._prev_breadth_mom = None
        self.warmup_days = 0
        self._prev_veto = True
        self._whitelist = []
        self.history.clear()

    def to_dict(self) -> dict:
        """Export state để persist."""
        return {
            "k": self.k,
            "h1": self.h1,
            "h2": self.h2,
            "h3": self.h3,
            "h": self.h,
            "S_plus": self.S_plus,
            "S_minus": self.S_minus,
            "warmup_days": self.warmup_days,
            "_prev_veto": self._prev_veto,
            "history": self.history[-100:],
        }
