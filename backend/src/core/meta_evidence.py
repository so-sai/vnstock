"""meta_evidence.py — Dual Belief Calibration: Meta-labeling cho PTD.

Tách biệt hai vòng suy luận:
  - Market Loop: Governor suy luận trạng thái thị trường (P(State))
  - Meta Loop: Calibration đo độ tin cậy của mô hình (Effective_Trust)

Architecture: Hierarchical Bayesian Control (Meta-labeling, de Prado 2018).
"""
import math
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.core.regime_confusion import RegimeAwareConfusion

# ── Hằng số ---------------------------------------------------------

# Half-life = 60 phiên → decay = exp(ln(0.5) / 60) ≈ 0.9885
STOP_LOSS_DECAY = 0.9885

# Cửa sổ rolling cho meta metrics
DEFAULT_WINDOW = 60

# Sigmoid bias: khi penalty = 0 → sigmoid(3.0) ≈ 0.953
SIGMOID_BIAS = 3.0


# ── Helpers ----------------------------------------------------------

def _sigmoid(raw: float) -> float:
    """Sigmoid với bias: sigmoid(3.0) ≈ 0.95, sigmoid(0) = 0.5."""
    return 1.0 / (1.0 + math.exp(-raw))


def _ema(old: float, new: float, alpha: float = 0.05) -> float:
    """Exponential Moving Average."""
    return (1.0 - alpha) * old + alpha * new


# ── Execution Journal ───────────────────────────────────────────────

class ExecutionJournal:
    """Nhật ký thực thi — đầu vào cho Meta Evidence.

    Mỗi entry ghi lại một sự kiện giao dịch (mua/bán/cắt lỗ).
    """

    def __init__(self):
        self.entries: deque[Dict[str, Any]] = deque(maxlen=1000)

    def record(self, entry: Dict[str, Any]):
        """Ghi một sự kiện thực thi."""
        entry["timestamp"] = entry.get("timestamp", datetime.now().isoformat())
        self.entries.append(entry)

    def recent(self, n: int = DEFAULT_WINDOW) -> list:
        """Lấy n entry gần nhất."""
        return list(self.entries)[-n:]

    def clear(self):
        """Xóa journal (sau Hard Reset)."""
        self.entries.clear()


# ── Execution Quality ───────────────────────────────────────────────

class ExecutionQuality:
    """Chất lượng thực thi — đo lỗi từ broker/thị trường.

    Ảnh hưởng đến: E_exec (Execution Fit) trong Calibration vector.
    KHÔNG ảnh hưởng đến Market Belief của Governor.
    """

    def __init__(self):
        self.slippage_bps_ma: float = 0.0
        self.partial_fill_ma: float = 0.0
        self.latency_ms_ma: float = 0.0
        self.commission_bps_ma: float = 0.0

    def update(self, entry: Dict[str, Any]):
        """Cập nhật từ một entry Execution Journal."""
        alpha = 0.1  # phản ứng nhanh hơn với execution issues
        if "slippage_bps" in entry:
            self.slippage_bps_ma = _ema(self.slippage_bps_ma, entry["slippage_bps"], alpha)
        if "partial_fill_rate" in entry:
            self.partial_fill_ma = _ema(self.partial_fill_ma, 1.0 - entry["partial_fill_rate"], alpha)
        if "latency_ms" in entry:
            norm_latency = min(entry["latency_ms"] / 1000.0, 1.0)
            self.latency_ms_ma = _ema(self.latency_ms_ma, norm_latency, alpha)
        if "commission_bps" in entry:
            norm_comm = min(entry["commission_bps"] / 50.0, 1.0)
            self.commission_bps_ma = _ema(self.commission_bps_ma, norm_comm, alpha)

    @property
    def score(self) -> float:
        """Execution Fit: [0, 1]. 1.0 = hoàn hảo."""
        penalty = (
            0.4 * (self.slippage_bps_ma / 20.0)          # slippage chuẩn hóa
            + 0.3 * self.partial_fill_ma                  # partial fill
            + 0.2 * self.latency_ms_ma                    # latency
            + 0.1 * self.commission_bps_ma                # commission
        )
        return _sigmoid(SIGMOID_BIAS - penalty)

    def reset(self):
        """Reset về mặc định (sau Hard Reset)."""
        self.__init__()


# ── Model Quality ───────────────────────────────────────────────────

class ModelQuality:
    """Chất lượng mô hình — đo độ tin cậy của Governor.

    Ảnh hưởng đến: C_model (Market Fit) + R_novelty (Novelty Risk).
    KHÔNG ảnh hưởng đến Market Belief (P(State)).
    """

    def __init__(self, half_life: int = 60):
        self.half_life = half_life
        self.decay = 0.5 ** (1.0 / half_life)  # exp(ln(0.5)/half_life)

        # Stop-loss frequency (decaying counter)
        self.stop_loss_count: float = 0.0

        # Prediction error (EMA)
        self.prediction_error_ma: float = 0.0

        # Signal flip rate (EMA)
        self.signal_flip_rate: float = 0.0

        # Posterior calibration: expected vs observed transition frequency
        self.posterior_calibration: float = 1.0

        # Mahalanobis distance proxy (novelty)
        self.novelty_distance: float = 0.0

    def decay_step(self):
        """Mỗi phiên: decay tất cả các bộ đếm."""
        self.stop_loss_count *= STOP_LOSS_DECAY

    def record_stop_loss(self):
        """Ghi nhận một sự kiện stop-loss."""
        self.stop_loss_count += 1.0

    def record_prediction_error(self, expected: float, realized: float):
        """Ghi nhận sai số dự báo."""
        error = abs(expected - realized)
        self.prediction_error_ma = _ema(self.prediction_error_ma, error)

    def record_signal_flip(self):
        """Ghi nhận đảo chiều tín hiệu."""
        self.signal_flip_rate = _ema(self.signal_flip_rate, 1.0)

    def update_novelty(self, mahalanobis_distance: float):
        """Cập nhật novelty risk từ Mahalanobis distance."""
        # Chuẩn hóa: sigmoid(distance / scale) → [0, 1]
        self.novelty_distance = 1.0 / (1.0 + math.exp(-(mahalanobis_distance - 3.0) / 1.5))

    @property
    def market_fit_score(self) -> float:
        """C_model: [0, 1]. Độ tin cậy của mô hình thị trường."""
        penalty = (
            0.5 * (self.stop_loss_count / 3.0)          # SL tần suất cao
            + 10.0 * self.prediction_error_ma            # Sai số dự báo
            + 2.0 * self.signal_flip_rate                 # Đảo chiều tín hiệu
        )
        return _sigmoid(SIGMOID_BIAS - penalty)

    @property
    def novelty_risk_score(self) -> float:
        """R_novelty: [0, 1]. 1.0 = hoàn toàn mới (cẩn thận)."""
        return self.novelty_distance

    @property
    def score(self) -> float:
        """Overall model quality (aggregate)."""
        return self.market_fit_score * (1.0 - self.novelty_risk_score * 0.5)

    def reset(self):
        """Reset về mặc định (sau Hard Reset)."""
        self.__init__(half_life=self.half_life)


# ── Meta Evidence (Calibration Vector) ──────────────────────────────

class MetaEvidence:
    """Đầu não Meta Evidence — tổng hợp calibration vector 5 chiều.

    Calibration vector:
      - C_model:     Market Fit (Governor có đang hoạt động tốt không?)
      - E_exec:      Execution Fit (Broker/Execution có ổn không?)
      - Q_data:      Data Quality (Dữ liệu đầu vào có tin cậy không?)
      - R_novelty:   Novelty Risk (Thị trường có đang ở miền mới không?)
      - S_fit:       Strategy Fit (Chiến lược có hiệu quả trong regime này không?)

    Usage:
        meta = MetaEvidence()
        meta.update_from_journal({"exit_reason": "STOP_LOSS", "slippage_bps": 15})
        cv = meta.calibration_vector   # {"market_fit": 0.88, "execution_fit": 0.95, ...}
        trust = meta.effective_trust   # 0.88 × 0.95 × 1.0 × (1 - 0.12) × 0.90 = ...
    """

    def __init__(self):
        self.execution_quality = ExecutionQuality()
        self.model_quality = ModelQuality(half_life=60)
        self.data_quality_score: float = 1.0
        self.current_adx: float = 0.0  # set externally each EOD

        # Regime-Aware Confusion Matrix (lazy-loaded)
        self._regime_confusion: Optional["RegimeAwareConfusion"] = None

    @property
    def regime_confusion(self):
        if self._regime_confusion is None:
            from src.core.regime_confusion import (
                RegimeAwareConfusion, load_confusion_from_db,
            )
            loaded = load_confusion_from_db()
            self._regime_confusion = loaded if loaded else RegimeAwareConfusion()
        return self._regime_confusion

    def update_from_journal(self, entry: Dict[str, Any]):
        """Cập nhật từ một entry Execution Journal."""
        # Execution Quality
        self.execution_quality.update(entry)

        # Model Quality
        if entry.get("exit_reason") == "STOP_LOSS":
            self.model_quality.record_stop_loss()
        if "expected_return" in entry and "realized_return" in entry:
            self.model_quality.record_prediction_error(
                entry["expected_return"], entry["realized_return"]
            )
        if entry.get("signal_flipped"):
            self.model_quality.record_signal_flip()
        if "mahalanobis_distance" in entry:
            self.model_quality.update_novelty(entry["mahalanobis_distance"])

    def update_data_quality(self, stale: bool = False, missing_ratio: float = 0.0):
        """Cập nhật chất lượng dữ liệu."""
        penalty = (0.5 if stale else 0.0) + missing_ratio
        self.data_quality_score = _sigmoid(SIGMOID_BIAS - penalty * 3.0)

    def update_calibration_from_quantstats(self, calibration: dict):
        """Cập nhật calibration penalty từ QuantStatsBridge.

        calibration_penalty [0,1] → điều chỉnh data_quality_score
        và market_fit score trong calibration vector.
        """
        penalty = calibration.get("calibration_penalty", 0.0)
        action = calibration.get("action", "NONE")

        # Phạt data_quality theo mức độ lệch calibration
        penalty_factor = 1.0 - penalty * 0.5  # penalty 1.0 → factor 0.5
        self.data_quality_score = max(0.1, self.data_quality_score * penalty_factor)

        # Nếu ABORT → force data_quality về sát 0 để Governor block
        if action == "ABORT":
            self.data_quality_score = min(self.data_quality_score, 0.15)

        # Ghi nhận outlier win ratio
        owr = calibration.get("outlier_win_ratio", 0.0)
        if owr > 0.3:
            self.model_quality.record_signal_flip()

    def decay_step(self):
        """Mỗi phiên EOD: decay counters, trừ khi có entry mới."""
        self.model_quality.decay_step()

    @property
    def strategy_fit_score(self) -> float:
        """Strategy Fit: EV/Kelly gate từ RegimeAwareConfusion."""
        return self.regime_confusion.calibration_score(self.current_adx)

    @property
    def calibration_vector(self) -> Dict[str, float]:
        """Calibration vector 5 chiều."""
        return {
            "market_fit": round(self.model_quality.market_fit_score, 4),
            "execution_fit": round(self.execution_quality.score, 4),
            "data_quality": round(self.data_quality_score, 4),
            "novelty_risk": round(self.model_quality.novelty_risk_score, 4),
            "strategy_fit": round(self.strategy_fit_score, 4),
        }

    @property
    def effective_trust(self) -> float:
        """Effective trust = tích các thành phần calibration.

        Multiplicative aggregation: nếu bất kỳ thành phần nào về 0,
        effective_trust = 0 → Governor không cấp vốn.
        """
        cv = self.calibration_vector
        return round(
            cv["market_fit"]
            * cv["execution_fit"]
            * cv["data_quality"]
            * (1.0 - cv["novelty_risk"])
            * cv["strategy_fit"],
            4,
        )

    def reset(self):
        """Reset toàn bộ (sau Hard Reset)."""
        self.execution_quality.reset()
        self.model_quality.reset()
        self.data_quality_score = 1.0
        self._regime_confusion = None


# Singleton cho toàn bộ hệ thống (reset theo Hard Reset)
_ACTIVE_META: Optional[MetaEvidence] = None


def get_meta_evidence() -> MetaEvidence:
    """Lấy instance MetaEvidence toàn cục."""
    global _ACTIVE_META
    if _ACTIVE_META is None:
        _ACTIVE_META = MetaEvidence()
    return _ACTIVE_META


def reset_meta_evidence():
    """Reset MetaEvidence (gọi khi Hard Reset kích hoạt)."""
    global _ACTIVE_META
    if _ACTIVE_META is not None:
        _ACTIVE_META.reset()
