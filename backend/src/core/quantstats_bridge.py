"""quantstats_bridge.py — Live Calibration Engine cho PTD.

Biến QuantStats thành động cơ giám sát niềm tin (Belief Monitor).
Không dùng để ngắm backtest, mà để đo độ lệch giữa kỳ vọng và thực tế.

3 pipeline song song:
  - LIVE: equity curve thực tế → rolling metrics
  - REJECTED: giả thuyết bị Governor bác bỏ → counterfactual metrics
  - RANDOM: Monte Carlo vectorized → random baseline phân vị 95%
"""
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.database.db_core import get_connection

DEFAULT_WINDOW_DAYS = 30
N_RANDOM_SIMULATIONS = 100
SHARPE_EWMA_SPAN = 10
RISK_FREE_RATE = 0.05  # 5%/năm


class QuantStatsBridge:
    """Cầu nối QuantStats → MetaEvidence.

    Usage:
        bridge = QuantStatsBridge()
        report = bridge.run_all()
        meta = get_meta_evidence()
        meta.update_calibration_from_quantstats(report)
    """

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS):
        self.window_days = window_days
        self._cache: Dict[str, Any] = {}

    # ── 1. LIVE PIPELINE ──────────────────────────────────────────────

    def fetch_live_returns(self) -> np.ndarray:
        """Đọc paper_equity_curve → daily returns array.

        Lọc: bỏ năm 2099 (dữ liệu lỗi), cap daily return ±50%
        để loại capital injection khỏi metrics.
        """
        cutoff = (datetime.now() - timedelta(days=self.window_days * 3)).isoformat()
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT date, total_equity FROM paper_equity_curve "
                "WHERE date >= ? ORDER BY date",
                (cutoff,),
            ).fetchall()
        if len(rows) < 2:
            return np.array([])
        # Lọc bỏ năm 2099
        valid = [(r[0], r[1]) for r in rows if not r[0].startswith("2099")]
        if len(valid) < 2:
            return np.array([])
        equities = np.array([r[1] for r in valid], dtype=np.float64)
        returns = np.diff(equities) / equities[:-1]
        # Cap outliers: daily return > 50% là capital injection, set về 0
        returns = np.clip(returns, -0.50, 0.50)
        if len(returns) > self.window_days:
            returns = returns[-self.window_days:]
        return returns

    def fetch_rejected_trades(self) -> np.ndarray:
        """Đọc paper_trades_log WHERE is_rejected=1 → simulated returns.

        Dùng decision_price làm entry, estimated exit = entry * (1 + random_walk)
        để tạo phân phối counterfactual. Trả về mảng returns.
        """
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT decision_price, side, symbol FROM paper_trades_log "
                "WHERE is_rejected = 1 ORDER BY decision_date DESC LIMIT ?",
                (self.window_days,),
            ).fetchall()
        if not rows:
            return np.array([])

        # Lấy σ thị trường từ daily returns để mô phỏng exit
        live_rets = self.fetch_live_returns()
        sigma = float(np.std(live_rets)) if len(live_rets) > 5 else 0.015

        returns = []
        for r in rows:
            price = r[0] if r[0] and r[0] > 0 else 10000.0
            side = r[1]
            # Mô phỏng exit sau 5 phiên với noise
            sim_exit = price * (1.0 + np.random.normal(0, sigma * math.sqrt(5)))
            ret = (sim_exit - price) / price
            if side == "SELL":
                ret = -ret
            returns.append(ret)
        return np.array(returns, dtype=np.float64)

    def fetch_rejected_signals_archive(self) -> np.ndarray:
        """Đọc Evidence Ledger → counterfactual returns (IG-weighted).

        Chỉ lấy ACTIVE records còn trong evaluation_horizon.
        Weighted bằng information_gain để Governor học từ IG,
        không từ số lượng reject.
        """
        try:
            from src.database.rejected_signals import (
                get_counterfactual_returns, get_cumulative_information_gain,
            )
            returns = get_counterfactual_returns(window_days=self.window_days, weighted=True)
            self._cache["cumulative_ig"] = get_cumulative_information_gain()
            if len(returns) > 0:
                return returns
        except Exception:
            pass
        return np.array([])

    # ── 2. METRICS ENGINE ─────────────────────────────────────────────

    @staticmethod
    def compute_sharpe(returns: np.ndarray, annualize: bool = True) -> float:
        """Sharpe Ratio từ daily returns."""
        if len(returns) < 5:
            return 0.0
        sigma = float(np.std(returns, ddof=1))
        if sigma < 1e-10:
            return 0.0
        daily_rf = RISK_FREE_RATE / 252.0
        excess = float(np.mean(returns)) - daily_rf
        sharpe = excess / sigma
        if annualize:
            sharpe *= math.sqrt(252)
        return sharpe

    @staticmethod
    def compute_sortino(returns: np.ndarray, annualize: bool = True) -> float:
        """Sortino Ratio — chỉ tính downside deviation."""
        if len(returns) < 5:
            return 0.0
        downside = returns[returns < 0]
        if len(downside) < 2:
            return float(np.mean(returns)) / 1e-10 if np.mean(returns) > 0 else 0.0
        downside_std = float(np.std(downside, ddof=1))
        if downside_std < 1e-10:
            return 0.0
        daily_rf = RISK_FREE_RATE / 252.0
        sortino = (float(np.mean(returns)) - daily_rf) / downside_std
        if annualize:
            sortino *= math.sqrt(252)
        return sortino

    @staticmethod
    def compute_max_drawdown(returns: np.ndarray) -> float:
        """Max Drawdown (tỷ lệ, dương)."""
        if len(returns) < 2:
            return 0.0
        cum = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        return float(abs(np.min(dd)))

    @staticmethod
    def compute_recovery_factor(returns: np.ndarray) -> float:
        """Recovery Factor = tổng lợi nhuận / |Max DD|."""
        if len(returns) < 2:
            return 0.0
        total_return = float(np.prod(1 + returns) - 1)
        mdd = QuantStatsBridge.compute_max_drawdown(returns)
        return total_return / mdd if mdd > 1e-10 else 0.0

    @staticmethod
    def compute_outlier_ratio(returns: np.ndarray, n_std: float = 2.0) -> Tuple[float, float]:
        """(outlier_win_ratio, outlier_loss_ratio).

        Outlier = return lệch > n_std so với mean.
        """
        if len(returns) < 5:
            return (0.0, 0.0)
        mu = float(np.mean(returns))
        sigma = float(np.std(returns, ddof=1))
        if sigma < 1e-10:
            return (0.0, 0.0)
        threshold = n_std * sigma
        n_wins = int(np.sum(returns > 0))
        n_losses = int(np.sum(returns < 0))
        n_outlier_win = int(np.sum(returns > mu + threshold))
        n_outlier_loss = int(np.sum(returns < mu - threshold))
        owr = n_outlier_win / n_wins if n_wins > 0 else 0.0
        olr = n_outlier_loss / n_losses if n_losses > 0 else 0.0
        return (owr, olr)

    @staticmethod
    def compute_profit_factor(returns: np.ndarray) -> float:
        """Profit Factor = tổng lợi nhuận / tổng thua lỗ (absolute)."""
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        total_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
        total_loss = float(abs(np.sum(losses))) if len(losses) > 0 else 1e-10
        return total_win / total_loss if total_loss > 1e-10 else 0.0

    @staticmethod
    def compute_win_rate(returns: np.ndarray) -> float:
        """Win Rate = % phiên dương."""
        if len(returns) < 1:
            return 0.0
        return float(np.sum(returns > 0)) / len(returns)

    @staticmethod
    def compute_kelly_criterion(returns: np.ndarray) -> float:
        """Kelly Criterion = W - (1-W) / (R).

        W = win_rate, R = avg_win / |avg_loss|.
        """
        if len(returns) < 5:
            return 0.0
        w = QuantStatsBridge.compute_win_rate(returns)
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(abs(np.mean(losses))) if len(losses) > 0 else 1e-10
        r = avg_win / avg_loss if avg_loss > 1e-10 else 0.0
        if r < 1e-10:
            return 0.0
        kelly = w - (1 - w) / r
        return max(0.0, min(kelly, 0.25))  # clamp [0, 0.25] Half-Kelly

    def compute_metrics(self, returns: np.ndarray) -> Dict[str, float]:
        """Tính toàn bộ metrics từ returns array."""
        if len(returns) < 2:
            return self._empty_metrics()
        return {
            "sharpe": round(self.compute_sharpe(returns), 4),
            "sortino": round(self.compute_sortino(returns), 4),
            "max_drawdown": round(self.compute_max_drawdown(returns), 4),
            "recovery_factor": round(self.compute_recovery_factor(returns), 4),
            "win_rate": round(self.compute_win_rate(returns), 4),
            "profit_factor": round(self.compute_profit_factor(returns), 4),
            "kelly_criterion": round(self.compute_kelly_criterion(returns), 4),
            "n_observations": len(returns),
        }

    @staticmethod
    def _empty_metrics() -> Dict[str, float]:
        return {
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
            "recovery_factor": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
            "kelly_criterion": 0.0, "n_observations": 0,
        }

    # ── 3. RANDOM BASELINE (Vectorized Monte Carlo) ──────────────────

    def compute_random_baseline(self, mu: float = 0.0, sigma: float = 0.015) -> Dict[str, float]:
        """Vectorized Monte Carlo — O(1) NumPy.

        Tạo ma trận (N_sim, N_days) returns ngẫu nhiên,
        tính phân phối tích lũy vectorized, trả về phân vị 95%.
        """
        sims = N_RANDOM_SIMULATIONS
        days = self.window_days
        random_returns = np.random.normal(loc=mu, scale=sigma, size=(sims, days))

        # Vectorized metrics trên từng simulation
        cum = np.cumprod(1 + random_returns, axis=1)
        final_returns = cum[:, -1] - 1  # (sims,)

        # Sharpe từng simulation
        sim_means = np.mean(random_returns, axis=1)
        sim_stds = np.std(random_returns, axis=1, ddof=1)
        sim_sharpe = np.where(
            sim_stds > 1e-10,
            (sim_means - RISK_FREE_RATE / 252.0) / sim_stds * math.sqrt(252),
            0.0,
        )

        # Max DD từng simulation
        peaks = np.maximum.accumulate(cum, axis=1)
        drawdowns = (cum - peaks) / peaks
        sim_mdd = np.min(drawdowns, axis=1)

        return {
            "random_sharpe_p95": round(float(np.percentile(sim_sharpe, 95)), 4),
            "random_sharpe_p50": round(float(np.percentile(sim_sharpe, 50)), 4),
            "random_return_p95": round(float(np.percentile(final_returns, 95)), 4),
            "random_return_p50": round(float(np.percentile(final_returns, 50)), 4),
            "random_mdd_p95": round(float(abs(np.percentile(sim_mdd, 5))), 4),
            "random_mdd_p50": round(float(abs(np.percentile(sim_mdd, 50))), 4),
            "n_simulations": sims,
        }

    # ── 4. EWMA SMOOTHING ───────────────────────────────────────────

    def compute_smoothed_sharpe(self, returns: np.ndarray, span: int = SHARPE_EWMA_SPAN) -> float:
        """Sharpe với EWMA smoothing (span=10).

        Tính daily sharpe rolling window 5 phiên → EWMA(span).
        """
        if len(returns) < 5:
            return 0.0
        # Rolling sharpe window=5
        daily_sharpes = []
        for i in range(len(returns) - 4):
            window = returns[i:i + 5]
            daily_sharpes.append(self.compute_sharpe(window, annualize=False))
        if not daily_sharpes:
            return 0.0
        arr = np.array(daily_sharpes, dtype=np.float64)
        # EWMA
        alpha = 2.0 / (span + 1)
        ewma = np.zeros_like(arr)
        ewma[0] = arr[0]
        for i in range(1, len(arr)):
            ewma[i] = alpha * arr[i] + (1 - alpha) * ewma[i - 1]
        smoothed = float(ewma[-1]) * math.sqrt(252)
        return round(smoothed, 4)

    # ── 4b. DOC INDEX ────────────────────────────────────────────────

    def compute_doc_index(self) -> Dict[str, Any]:
        """Decision Opportunity Cost — đo Governor mù quyết định.

        DOC_Index = Mean(R_alternative - R_rejected_simulated)
          - DOC_Index > 0: Governor chọn đúng (thay thế tốt hơn reject)
          - DOC_Index < 0: Governor mù quyết định (chọn tệ hơn)

        Khi DOC_Index < 0: calibration_penalty cộng thêm 0.2.
        Khi không đủ dữ liệu: trả về 0 (trung tính).
        """
        try:
            from src.database.rejected_signals import fetch_doc_returns
            pairs = fetch_doc_returns(window_days=self.window_days)
        except Exception:
            pairs = []

        if len(pairs) < 3:
            return {"doc_index": 0.0, "doc_penalty": 0.0, "n_pairs": len(pairs)}

        diffs = [p["alternative_return"] - p["rejected_return"] for p in pairs]
        doc_index = float(np.mean(diffs))

        # DOC penalty: chỉ phạt khi Governor chọn tệ hơn
        doc_penalty = 0.2 if doc_index < 0 else 0.0

        return {
            "doc_index": round(doc_index, 4),
            "doc_penalty": doc_penalty,
            "doc_wins": sum(1 for d in diffs if d > 0),
            "doc_losses": sum(1 for d in diffs if d < 0),
            "n_pairs": len(pairs),
        }

    # ── 5. CALIBRATION SIGNAL ────────────────────────────────────────

    def calibration_signal(
        self,
        live_metrics: Dict[str, float],
        rejected_metrics: Dict[str, float],
        random_metrics: Dict[str, float],
        live_returns: np.ndarray,
    ) -> Dict[str, Any]:
        """So sánh 3 pipeline → calibration signal cho MetaEvidence.

        Trả về dict với:
          - sharpe_live_smoothed: Sharpe EWMA(10) annualized
          - sharpe_vs_random: live_sharpe / random_p95 Sharpe
          - outlier_win_ratio: tỷ lệ lợi nhuận bất thường
          - calibration_penalty: mức phạt [0,1] cho effective_trust
          - action: "NONE" | "SCALE" | "ABORT"
          - doc_index: Decision Opportunity Cost
        """
        if len(live_returns) < 5:
            return {
                "sharpe_live_smoothed": 0.0,
                "sharpe_vs_random": 0.0,
                "outlier_win_ratio": 0.0,
                "outlier_loss_ratio": 0.0,
                "calibration_penalty": 1.0,
                "action": "ABORT",
                "reason": "INSUFFICIENT_DATA",
            }

        # Sharpe smoothed
        sharpe_smoothed = self.compute_smoothed_sharpe(live_returns)

        # Sharpe vs Random P95
        random_p95 = random_metrics.get("random_sharpe_p95", 0.0)
        sharpe_vs_random = (
            sharpe_smoothed / random_p95 if random_p95 > 0.1 else 0.0
        )

        # Outlier ratio
        owr, olr = self.compute_outlier_ratio(live_returns)

        # DOC Index
        doc = self.compute_doc_index()

        # Calibration penalty
        penalty = 0.0
        if sharpe_smoothed < 0.5:
            penalty += 0.4
        if sharpe_vs_random < 1.0:
            penalty += 0.3  # không tốt hơn random
        if owr > 0.3:
            penalty += 0.2  # lợi nhuận nhờ outlier
        if live_metrics.get("max_drawdown", 0) > 0.15:
            penalty += 0.3

        # DOC penalty: Governor mù quyết định
        penalty += doc.get("doc_penalty", 0.0)

        penalty = min(penalty, 1.0)

        # Action
        action = "NONE"
        reason = ""
        if penalty >= 0.7:
            action = "ABORT"
            reason = "PILOT_ABORT — nhiều chỉ số calibration xuống cấp"
        elif penalty >= 0.4:
            action = "SCALE"
            reason = "SCALE — sharpe_smoothed hoặc max_drawdown vượt ngưỡng"

        return {
            "sharpe_live_smoothed": sharpe_smoothed,
            "sharpe_vs_random": round(sharpe_vs_random, 4),
            "outlier_win_ratio": round(owr, 4),
            "outlier_loss_ratio": round(olr, 4),
            "calibration_penalty": round(penalty, 4),
            "action": action,
            "reason": reason,
            "doc_index": doc.get("doc_index", 0.0),
            "doc_wins": doc.get("doc_wins", 0),
            "doc_losses": doc.get("doc_losses", 0),
            "cumulative_information_gain": self._cache.get("cumulative_ig", 0.0),
            "live_metrics": live_metrics,
            "rejected_metrics": rejected_metrics,
            "random_metrics": {k: v for k, v in random_metrics.items()
                               if k != "n_simulations"},
        }

    # ── 6. RUN ALL ───────────────────────────────────────────────────

    def run_all(self) -> Dict[str, Any]:
        """Chạy toàn bộ pipeline: live + rejected + random + calibration."""
        live_returns = self.fetch_live_returns()
        rejected_returns = self.fetch_rejected_trades()
        archive_returns = self.fetch_rejected_signals_archive()
        if len(archive_returns) > 0:
            rejected_returns = np.concatenate([rejected_returns, archive_returns])

        live_metrics = self.compute_metrics(live_returns) if len(live_returns) >= 2 else self._empty_metrics()
        rejected_metrics = self.compute_metrics(rejected_returns) if len(rejected_returns) >= 2 else self._empty_metrics()

        # Random baseline — dùng sigma capped để tránh overflow
        mu = float(np.mean(live_returns)) if len(live_returns) > 5 else 0.0
        sigma = float(np.std(live_returns, ddof=1)) if len(live_returns) > 5 else 0.015
        sigma = min(sigma, 0.05)  # cap daily vol ở 5% để Monte Carlo ổn định
        random_metrics = self.compute_random_baseline(mu=mu, sigma=sigma)

        calibration = self.calibration_signal(
            live_metrics, rejected_metrics, random_metrics, live_returns
        )

        return {
            "timestamp": datetime.now().isoformat(),
            "window_days": self.window_days,
            "live": live_metrics,
            "rejected": rejected_metrics,
            "random_baseline": random_metrics,
            "calibration": calibration,
        }

    def save_to_db(self, report: Dict[str, Any]):
        """Lưu kết quả QuantStats vào CSDL meta_evidence.

        Tạo bảng quantstats_calibration nếu chưa có.
        """
        cal = report.get("calibration", {})
        with get_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS quantstats_calibration ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  timestamp TEXT NOT NULL,"
                "  sharpe_live_smoothed REAL,"
                "  sharpe_vs_random REAL,"
                "  outlier_win_ratio REAL,"
                "  outlier_loss_ratio REAL,"
                "  calibration_penalty REAL,"
                "  action TEXT,"
                "  reason TEXT,"
                "  doc_index REAL,"
                "  doc_wins INTEGER,"
                "  doc_losses INTEGER,"
                "  live_metrics TEXT,"
                "  rejected_metrics TEXT,"
                "  random_metrics TEXT,"
                "  created_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
            conn.execute(
                "INSERT INTO quantstats_calibration "
                "(timestamp, sharpe_live_smoothed, sharpe_vs_random, "
                " outlier_win_ratio, outlier_loss_ratio, calibration_penalty, "
                " action, reason, doc_index, doc_wins, doc_losses, "
                " live_metrics, rejected_metrics, random_metrics) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report["timestamp"],
                    cal.get("sharpe_live_smoothed"),
                    cal.get("sharpe_vs_random"),
                    cal.get("outlier_win_ratio"),
                    cal.get("outlier_loss_ratio"),
                    cal.get("calibration_penalty"),
                    cal.get("action"),
                    cal.get("reason"),
                    cal.get("doc_index", 0.0),
                    cal.get("doc_wins", 0),
                    cal.get("doc_losses", 0),
                    str(report.get("live", {})),
                    str(report.get("rejected", {})),
                    str(report.get("random_baseline", {})),
                ),
            )

    def load_last_from_db(self) -> Optional[Dict[str, Any]]:
        """Đọc bản ghi quantstats gần nhất."""
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM quantstats_calibration ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
        return None
