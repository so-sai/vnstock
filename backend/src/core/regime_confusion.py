"""regime_confusion.py — Regime-Aware Confusion Matrix cho Strategy Evaluation.

Ba hiệu chỉnh kiến trúc so với thiết kế heuristic:

  1. Unresolved Queue: Tín hiệu ngày T được tag ADX_Bucket tại entry,
     chỉ resolve tại T+5 (hoặc stop-loss/take-profit hit). Chống look-ahead bias.

  2. EV/Kelly Gating: Thay vì heuristic trừ tỷ lệ,
     dùng Expected Value: EV < 0 → calibration = 0.0 (chặn đứng).
     EV > 0 → Half-Kelly fraction.

  3. Bayesian Prior cho N < 30: Beta(α₀=1, β₀=3) — skeptical prior
     mean=0.25. Posterior tự động blend prior + data, không special-case.

Reference: Lopez de Prado (2018), "Advances in Financial Machine Learning", Ch. 14.
"""

import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("PTCK_SYSTEM")

# ── Hằng số ─────────────────────────────────────────────────────────

ADX_BUCKETS: Dict[str, tuple[float, float]] = {
    "ADX_LT20": (0.0, 20.0),
    "ADX_20_30": (20.0, 30.0),
    "ADX_GT30": (30.0, 100.0),
}

DEFAULT_RISK_REWARD = 2.0  # Risk:Reward = 1:2
RESOLVE_AFTER_DAYS = 5  # T+5 evaluation horizon
PRIOR_ALPHA = 1  # Beta prior α₀ (skeptical)
PRIOR_BETA = 3  # Beta prior β₀  → mean = 0.25
MIN_SIGNALS_STATISTICAL = 30  # CLT threshold (not a hard gate, prior handles it)
KELLY_FRACTION = 0.5  # Half-Kelly


def _days_between(d1: str, d2: str) -> int:
    """Số ngày giữa hai chuỗi YYYY-MM-DD."""
    return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days


def _classify_adx(adx: float) -> str:
    """Phân loại ADX vào bucket."""
    for bucket, (lo, hi) in ADX_BUCKETS.items():
        if lo <= adx < hi:
            return bucket
    return "ADX_LT20"


# ── RegimeAwareConfusion ─────────────────────────────────────────────


class RegimeAwareConfusion:
    """Confusion Matrix phân tầng theo Regime (ADX bucket).

    Flow:
      [Screener V1] → enqueue_signal() → unresolved queue
      [EOD T+5]     → resolve_pending() → confusion matrix update
      [Governor]    → calibration_score(adx_current) → EV/Kelly gate
    """

    def __init__(self, strategy: str = "screener_v1", prior_alpha: int = PRIOR_ALPHA, prior_beta: int = PRIOR_BETA):
        self.strategy = strategy
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta

        # Confusion matrix: {bucket: {"wins": n, "losses": n}}
        self.matrix: Dict[str, Dict[str, int]] = {b: {"wins": 0, "losses": 0} for b in ADX_BUCKETS}

        # Unresolved queue: {signal_id: signal_dict}
        self.unresolved: Dict[int, Dict[str, Any]] = {}
        self._next_id: int = 0

    # ── Queue Management ────────────────────────────────────────────

    def enqueue_signal(
        self,
        entry_date: str,
        adx: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        expected_return: Optional[float] = None,
    ) -> int:
        """Đẩy tín hiệu vào unresolved queue, tag ADX bucket tại entry.

        Args:
            entry_date: Ngày tín hiệu sinh ra (YYYY-MM-DD)
            adx: ADX tại ngày entry
            entry_price, stop_loss, take_profit: Giá entry/SL/TP
            expected_return: Kỳ vọng lợi nhuận (optional)

        Returns:
            signal_id để theo dõi
        """
        bucket = _classify_adx(adx)
        sid = self._next_id
        self._next_id += 1
        self.unresolved[sid] = {
            "entry_date": entry_date,
            "adx_bucket": bucket,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "expected_return": expected_return,
        }
        return sid

    def resolve_pending(self, current_date: str, price_lookup: Callable[[str, str], float]) -> int:
        """Resolve tín hiệu đã đủ tuổi (T+5 hoặc SL/TP hit).

        Args:
            current_date: Ngày hiện tại (YYYY-MM-DD)
            price_lookup: Hàm tra giá (symbol, date) → float

        Returns:
            Số lượng tín hiệu đã resolve
        """
        resolved = 0
        to_remove: List[int] = []

        for sid, signal in list(self.unresolved.items()):
            entry_date = signal["entry_date"]
            age = _days_between(entry_date, current_date)

            if age < RESOLVE_AFTER_DAYS:
                continue  # chưa đủ tuổi

            # Tra giá tại T+resolve để xác định outcome
            # Dùng entry_price làm proxy nếu không lookup được
            try:
                current_price = price_lookup(self.strategy, current_date)
            except Exception:
                current_price = signal["entry_price"]

            outcome = self._classify_outcome(signal, current_price)
            bucket = signal["adx_bucket"]

            if outcome == "win":
                self.matrix[bucket]["wins"] += 1
            elif outcome == "loss":
                self.matrix[bucket]["losses"] += 1
            # outcome == "expired" → không计入 confusion matrix

            to_remove.append(sid)
            resolved += 1

        for sid in to_remove:
            del self.unresolved[sid]

        return resolved

    @staticmethod
    def _classify_outcome(signal: Dict[str, Any], current_price: float) -> str:
        """Phân loại kết quả: win / loss / expired."""
        entry = signal["entry_price"]
        sl = signal.get("stop_loss")
        tp = signal.get("take_profit")

        if sl is not None and current_price <= sl:
            return "loss"
        if tp is not None and current_price >= tp:
            return "win"

        # Không hit SL/TP trong window → expired
        # Tính PnL đơn giản tại T+5
        pnl_pct = (current_price - entry) / max(entry, 1e-10)
        if pnl_pct > 0.02:  # >2% → win
            return "win"
        if pnl_pct < -0.02:  # <-2% → loss
            return "loss"
        return "expired"

    # ── Bayesian Inference ──────────────────────────────────────────

    def posterior_win_rate(self, adx: float) -> float:
        """Posterior mean win rate = Beta posterior expectation.

        Bayesian update: Beta(α₀ + wins, β₀ + losses)
        Prior: Beta(1, 3) → skeptical, mean = 0.25
        Với N < 30: prior vẫn chi phối, không cần special-case.
        Với N → ∞: posterior → empirical win rate.
        """
        bucket = _classify_adx(adx)
        stats = self.matrix[bucket]
        alpha_post = self.prior_alpha + stats["wins"]
        beta_post = self.prior_beta + stats["losses"]
        return alpha_post / (alpha_post + beta_post)

    def n_signals(self, adx: float) -> int:
        """Tổng số tín hiệu đã resolve cho bucket ADX này."""
        bucket = _classify_adx(adx)
        return self.matrix[bucket]["wins"] + self.matrix[bucket]["losses"]

    # ── EV/Kelly Gating ─────────────────────────────────────────────

    def calibration_score(self, adx: float, risk_reward: float = DEFAULT_RISK_REWARD) -> float:
        """Cổng EV/Kelly: quyết định calibration từ win rate.

        EV = win_rate × reward - (1 - win_rate) × risk
        EV ≤ 0 → calibration = 0.0 (chặn đứng)
        EV > 0 → Half-Kelly fraction

        Args:
            adx: ADX hiện tại (dùng để chọn bucket)
            risk_reward: Risk:Reward ratio (mặc định 1:2)

        Returns:
            Calibration score [0, 1] cho strategy này
        """
        win_rate = self.posterior_win_rate(adx)

        # Expected Value
        ev = win_rate * risk_reward - (1.0 - win_rate) * 1.0

        if ev <= 0.0:
            return 0.0

        # Kelly: f* = (p × b - q) / b
        kelly = (win_rate * risk_reward - (1.0 - win_rate)) / risk_reward
        half_kelly = kelly * KELLY_FRACTION

        return max(0.0, min(1.0, half_kelly))

    # ── Persistence ─────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialization cho DB persistence."""
        return {
            "strategy": self.strategy,
            "matrix": self.matrix,
            "unresolved": {str(k): v for k, v in self.unresolved.items()},
            "next_id": self._next_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegimeAwareConfusion":
        """Deserialize từ DB."""
        obj = cls(strategy=data.get("strategy", "screener_v1"))
        obj.matrix = data.get("matrix", {b: {"wins": 0, "losses": 0} for b in ADX_BUCKETS})
        obj.unresolved = {int(k): v for k, v in data.get("unresolved", {}).items()}
        obj._next_id = data.get("next_id", 0)
        return obj

    # ── Diagnostics ─────────────────────────────────────────────────

    def diagnose(self, adx: Optional[float] = None) -> Dict[str, Any]:
        """Trả về thông tin chẩn đoán."""
        result = {
            "strategy": self.strategy,
            "unresolved_count": len(self.unresolved),
            "buckets": {},
        }
        for bucket in ADX_BUCKETS:
            stats = self.matrix[bucket]
            n = stats["wins"] + stats["losses"]
            win_rate = self.posterior_win_rate(
                ADX_BUCKETS[bucket][0] + 1.0  # midpoint for display
            )
            result["buckets"][bucket] = {
                "wins": stats["wins"],
                "losses": stats["losses"],
                "n_signals": n,
                "bayesian_win_rate": round(win_rate, 4),
            }
            if adx is not None:
                result["calibration_score"] = round(self.calibration_score(adx), 4)

        return result

    def reset(self):
        """Reset toàn bộ (sau Hard Reset hoặc strategy change)."""
        self.matrix = {b: {"wins": 0, "losses": 0} for b in ADX_BUCKETS}
        self.unresolved.clear()
        self._next_id = 0


# ── DB Persistence Helpers ──────────────────────────────────────────


def save_confusion_to_db(confusion: RegimeAwareConfusion):
    """Lưu confusion matrix + unresolved queue vào screener_cache.db."""
    from src.database.db_core import get_connection

    with get_connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_confusion (
                strategy TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO strategy_confusion (strategy, data_json, updated_at) VALUES (?, ?, ?)",
            (
                confusion.strategy,
                json.dumps(confusion.to_dict(), ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


def load_confusion_from_db(strategy: str = "screener_v1") -> Optional[RegimeAwareConfusion]:
    """Đọc confusion matrix từ DB (auto-create table nếu chưa có)."""
    from src.database.db_core import get_connection

    try:
        with get_connection() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS strategy_confusion (
                    strategy TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            row = conn.execute(
                "SELECT data_json FROM strategy_confusion WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        if row:
            data = json.loads(row["data_json"])
            return RegimeAwareConfusion.from_dict(data)
    except Exception as e:
        logger.warning(f"[CONFUSION] Load failed: {e}")

    return None
