"""structure_evolution.py — Tầng Tiến hóa Cấu trúc (SEL)

Kiến trúc:
  1. SpaceNormalizationLayer — Robust Scaler + Whitening (PCA chéo)
  2. WassersteinEngine — POT Sinkhorn2 trên 7D state vector
  3. DynamicThresholding — 99th percentile W1 + exponential HDR mapping
  4. StationarityValidator — ADF test cho chuẩn hóa Reference Regime mới
  5. SurvivalGovernor — Active Exploration mode khi W1 >= theta_novelty

Usage:
  from src.engine.structure_evolution import StructureEvolutionLayer
  sel = StructureEvolutionLayer()
  result = sel.assess(current_state_vector)
"""
import json
import logging
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import ot

# Statsmodels ADF có thể phát RuntimeWarning trên chuỗi ngắn — vô hại.
# POT không còn cần suppression: epsilon-smoothing + stabilized Sinkhorn khử
# tận gốc divide-by-zero. Giữ lại filter statsmodels cho ADF.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="statsmodels")


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config

from src.database.db_core import get_connection

DATA_DIR = src.config.DATA_DIR
logger = logging.getLogger("PTCK_SYSTEM")

# --- Constants ---------------------------------------------------------------
REGIME_LOOKBACK = 120
WINDOW_STATIONARITY = 21  # 1 trading month for stationarity test
MIN_SAMPLES_FOR_REGIME = 60
W1_LIBRARY_FILE = Path(str(DATA_DIR)) / "probe_cache" / "sel_w1_history.json"
REGIME_LIBRARY_FILE = Path(str(DATA_DIR)) / "probe_cache" / "sel_regime_library.json"
STATE_DIR = Path(str(DATA_DIR)) / "probe_cache"

# Default reference regimes (seed data — will be updated dynamically)
INITIAL_REFERENCE_REGIMES = {
    "stable_ranging": {
        "label": "DAO ĐỘNG ỔN ĐỊNH", "source": "seed",
        "feature_mean": [0.20, 0.30, 0.10, 0.50, 0.30, 0.01, 0.30],
        "feature_std": [0.05, 0.10, 0.05, 0.10, 0.10, 0.02, 0.15],
    },
    "moderate_bull": {
        "label": "TĂNG TRƯỞNG VỪA", "source": "seed",
        "feature_mean": [0.15, 0.20, 0.05, 0.70, 0.50, -0.02, 0.10],
        "feature_std": [0.05, 0.08, 0.03, 0.10, 0.15, 0.03, 0.10],
    },
    "liquidity_stress": {
        "label": "CĂNG THẲNG THANH KHOẢN", "source": "seed",
        "feature_mean": [0.60, 0.80, 0.60, 0.30, 0.40, 0.05, 0.80],
        "feature_std": [0.10, 0.15, 0.15, 0.10, 0.15, 0.04, 0.15],
    },
    "extreme_panic": {
        "label": "HOẢNG LOẠN CỰC ĐỘ", "source": "seed",
        "feature_mean": [0.90, 1.20, 0.90, 0.15, 0.60, 0.10, 1.00],
        "feature_std": [0.10, 0.20, 0.20, 0.08, 0.20, 0.05, 0.00],
    },
}


class SpaceNormalizationLayer:
    """Tầng Định chuẩn Không gian — Robust Scaler + Whitening PCA.

    Đưa 7 features về cùng thang phương sai, khử tương quan chéo.
    """

    def __init__(self):
        self.robust_scaler = None
        self.whiten_matrix: Optional[np.ndarray] = None
        self.feature_means: Optional[np.ndarray] = None
        self._is_fitted = False

    def fit(self, X: np.ndarray):
        """Fit Robust Scaler + Whitening PCA trên dữ liệu lịch sử."""
        if X.shape[0] < 10:
            return

        # Robust Scaler: median + IQR
        med = np.median(X, axis=0)
        q75, q25 = np.percentile(X, [75, 25], axis=0)
        iqr = np.maximum(q75 - q25, 1e-10)
        X_robust = (X - med) / iqr
        self.feature_medians = med
        self.feature_iqr = iqr

        # Whitening: PCA on robust-scaled data
        cov = np.cov(X_robust, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        eigenvalues = np.maximum(eigenvalues, 1e-10)
        # Whitening matrix: W = D^(-1/2) @ E^T
        self.whiten_matrix = (eigenvectors / np.sqrt(eigenvalues)).T
        self._is_fitted = True

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Chuẩn hóa + tẩy trắng."""
        if not self._is_fitted:
            return X
        if self.feature_medians is None or self.feature_iqr is None:
            return X

        X_robust = (X - self.feature_medians) / self.feature_iqr
        X_white = X_robust @ self.whiten_matrix.T
        return X_white


class WassersteinEngine:
    """Đo khoảng cách Wasserstein giữa P và Q — PRODUCTION-GRADE.

    Kiến trúc Primary-Fallback (ưu tiên độ chính xác toán học tuyệt đối):
      1. PRIMARY: ot.emd2 (Exact EMD / Network Simplex).
         - Đảm bảo d(x,x) = 0.0 tuyệt đối → khử triệt để Entropy Bias.
         - Hội tụ 100%, không sinh NaN/divide-by-zero.
      2. FALLBACK: ot.sinkhorn2 (reg siêu nhỏ, stabilized) — chỉ khi emd2
         không hội tụ (cực hiếm ở không gian 7 chiều).
      3. LAST RESORT: L2 proxy nếu cả hai solver đều lỗi.

    Ghi log mọi lần fallback để audit độ ổn định số học.
    """

    # Fallback Sinkhorn: reg siêu nhỏ (sắc nét) → lớn (ổn định).
    SINKHORN_FALLBACK_LADDER = (0.01, 0.05, 0.2)
    EPS = 1e-12

    def __init__(self, reg: float = 0.01):
        self.reg = reg
        self._w1_history: List[float] = []
        # Chẩn đoán độ ổn định số học (audit trail)
        self.numerical_diagnostics = {
            "emd2_exact": 0,          # Primary success
            "sinkhorn_fallbacks": 0,  # emd2 failed → sinkhorn
            "l2_fallbacks": 0,        # both failed
        }

    @staticmethod
    def _to_distribution(X: np.ndarray) -> np.ndarray:
        """Chuyển vector [d] thành empirical distribution weights (sum=1, dương ngặt).

        Epsilon smoothing đảm bảo mọi bin > 0 để fallback Sinkhorn (nếu cần)
        không gặp divide-by-zero. emd2 không yêu cầu điều này nhưng vô hại.
        """
        x = np.asarray(X, dtype=np.float64).flatten()
        x = x - np.min(x)  # shift to non-negative
        x = x + WassersteinEngine.EPS
        s = np.sum(x)
        if s < WassersteinEngine.EPS:
            return np.ones_like(x) / len(x)
        return x / s

    @staticmethod
    def _build_cost_matrix(p: np.ndarray, q: np.ndarray) -> np.ndarray:
        """Ma trận chi phí L2 chuẩn hóa, an toàn với divide-by-zero."""
        M = ot.dist(p.reshape(-1, 1), q.reshape(-1, 1), metric="sqeuclidean")
        mmax = float(np.max(M))
        if mmax > WassersteinEngine.EPS:
            M = M / mmax
        return np.ascontiguousarray(M, dtype=np.float64)

    def _stable_ot(self, p: np.ndarray, q: np.ndarray, M: np.ndarray) -> Tuple[float, str]:
        """Optimal Transport: emd2 Primary → Sinkhorn Fallback → L2.

        Returns: (w1_value, method_used). Trả -1.0 nếu cần L2 proxy (sentinel).
        """
        # Trường hợp trùng khớp: cost ~0 → W1=0 chính xác, bỏ qua solver.
        if float(np.max(M)) <= self.EPS:
            self.numerical_diagnostics["emd2_exact"] += 1
            return 0.0, "identity"

        # --- PRIMARY: Exact EMD (Network Simplex) ---
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            try:
                val = ot.emd2(p, q, M, numItermax=200000)
                w1 = float(np.asarray(val).ravel()[0])
                if np.isfinite(w1) and w1 >= -self.EPS:
                    self.numerical_diagnostics["emd2_exact"] += 1
                    return max(w1, 0.0), "emd2_exact"
                logger.debug(f"[SEL_OT] emd2 returned non-finite/negative: {w1}")
            except Exception as e:
                logger.debug(f"[SEL_OT] emd2 primary raised: {e}")

        # --- FALLBACK: Sinkhorn stabilized với reg tăng dần ---
        for reg in self.SINKHORN_FALLBACK_LADDER:
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                try:
                    val = ot.sinkhorn2(p, q, M, reg=reg, numItermax=2000,
                                       stopThr=1e-9, method="sinkhorn_stabilized")
                    w1 = float(np.asarray(val).ravel()[0])
                except Exception as e:
                    logger.debug(f"[SEL_OT] Sinkhorn fallback reg={reg} raised: {e}")
                    continue
            if np.isfinite(w1) and w1 >= -self.EPS:
                self.numerical_diagnostics["sinkhorn_fallbacks"] += 1
                logger.info(f"[SEL_OT] emd2 failed — Sinkhorn fallback reg={reg} used.")
                return max(w1, 0.0), f"sinkhorn_fallback_reg={reg}"

        # --- LAST RESORT: L2 proxy ---
        self.numerical_diagnostics["l2_fallbacks"] += 1
        logger.warning("[SEL_OT] Both emd2 and Sinkhorn failed — L2 proxy used.")
        return -1.0, "l2_proxy"

    def compute_w1(self, P: np.ndarray, Q: np.ndarray) -> float:
        """Wasserstein-1 distance — Exact EMD primary, an toàn số học."""
        p = self._to_distribution(P)
        q = self._to_distribution(Q)

        if len(p) != len(q):
            return 0.0

        M = self._build_cost_matrix(p, q)
        w1, method = self._stable_ot(p, q, M)
        if method == "l2_proxy" or w1 < 0:
            w1 = float(np.sqrt(np.mean((np.asarray(P, np.float64) -
                                        np.asarray(Q, np.float64)) ** 2)))
        self._w1_history.append(w1)
        return w1

    @staticmethod
    def compute_pairwise_w1_matrix(vectors: List[np.ndarray],
                                   reg: float = 0.01) -> np.ndarray:
        """Ma trận W1 giữa tất cả các cặp vector — dùng cùng engine (emd2 primary)."""
        n = len(vectors)
        if n < 2:
            return np.array([[0.0]])
        engine = WassersteinEngine(reg=reg)
        M = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                w = engine.compute_w1(vectors[i], vectors[j])
                M[i, j] = w
                M[j, i] = w
        return M


class DynamicThresholding:
    """Hệ thống Ngưỡng Động — 99th percentile + exponential HDR mapping."""

    # Bất biến ngưỡng (invariants) — bảo vệ chống Survival Mode kích hoạt sai.
    THETA_STABLE_FLOOR = 0.10   # ngưỡng ổn định tối thiểu tuyệt đối
    THETA_NOVELTY_FLOOR = 0.35  # novelty không bao giờ thấp hơn mức này
    THETA_MIN_GAP = 0.15        # novelty phải cách stable ít nhất khoảng này

    def __init__(self):
        self.w1_history: List[float] = []
        self.theta_stable: float = 0.15
        self.theta_novelty: float = 0.65
        self._percentile_99: float = 0.65
        self.beta: float = 2.0
        self.gamma: float = 3.0
        self._load_history()

    def _load_history(self):
        if W1_LIBRARY_FILE.exists():
            try:
                data = json.loads(W1_LIBRARY_FILE.read_text(encoding="utf-8"))
                self.w1_history = data.get("w1_history", [])
                self._update_thresholds()
            except Exception:
                pass

    def _save_history(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            W1_LIBRARY_FILE.write_text(
                json.dumps({"w1_history": self.w1_history[-500:]}),
                encoding="utf-8"
            )
        except Exception:
            pass

    def _update_thresholds(self):
        if len(self.w1_history) >= 10:
            arr = np.array(self.w1_history)
            self._percentile_99 = float(np.percentile(arr, 99))
            # theta_stable: P30 với floor cứng 0.10
            self.theta_stable = max(float(np.percentile(arr, 30)), self.THETA_STABLE_FLOOR)
            # theta_novelty: P99 nhưng BẮT BUỘC > theta_stable (biên tối thiểu),
            # đồng thời có floor tuyệt đối để tránh Survival Mode kích hoạt sai
            # khi phân phối W1 quá hẹp (emd2 cho giá trị rất nhỏ, đồng nhất).
            novelty = max(self._percentile_99,
                          self.theta_stable + self.THETA_MIN_GAP,
                          self.THETA_NOVELTY_FLOOR)
            self.theta_novelty = novelty

    def record_w1(self, w1: float):
        self.w1_history.append(w1)
        self._update_thresholds()
        self._save_history()

    def compute_hdr_limit(self, w1: float) -> Optional[float]:
        """Exponential HDR mapping: HDR = exp(-gamma * max(0, W1 - theta_stable))."""
        if w1 < self.theta_stable:
            return None  # No constraint
        if w1 >= self.theta_novelty:
            return 1.0  # Full lock — Survival Mode
        excess = w1 - self.theta_stable
        hdr = float(np.exp(-self.gamma * excess))
        return float(np.clip(hdr, 0.0, 1.0))

    def is_novelty(self, w1: float) -> bool:
        """Kiểm tra W1 có vượt ngưỡng novelty (P99)."""
        return w1 >= self.theta_novelty

    def get_survival_params(self, w1: float) -> Dict:
        """Tham số chế độ Survival khi W1 >= theta_novelty."""
        inflation_factor = 1.0 + w1 * 2.0
        return {
            "survival_mode": True,
            "covariance_inflation": round(float(inflation_factor), 2),
            "prior_type": "diffuse",
            "prior_description": "Uniform over parameter space — no historical prior",
            "decision_logic": "minimize(E[loss])",
            "observation_mode": "tick_level",
        }


class StationarityValidator:
    """Xác thực tính dừng (Stationarity) của chuỗi dữ liệu mới.

    Sử dụng ADF test + rolling W1 variance để quyết định
    khi nào Q_new đủ ổn định để thành Reference Regime.
    """

    def __init__(self, min_samples: int = MIN_SAMPLES_FOR_REGIME):
        self.min_samples = min_samples

    def validate(self, data_series: np.ndarray) -> Dict:
        """Kiểm tra tính dừng của chuỗi dữ liệu.

        Returns:
          is_stationary: bool — đủ ổn định để là Reference Regime
          confidence: 0-1
          evidence: chi tiết kiểm định
        """
        from statsmodels.tsa.stattools import adfuller

        if len(data_series) < self.min_samples:
            return {
                "is_stationary": False,
                "confidence": 0.0,
                "evidence": {
                    "reason": f"INSUFFICIENT_SAMPLES: {len(data_series)} < {self.min_samples}",
                    "n_samples": len(data_series),
                    "min_required": self.min_samples,
                },
            }

        # Downsample to 60 if longer
        if len(data_series) > 120:
            indices = np.linspace(0, len(data_series) - 1, 120, dtype=int)
            samples = data_series[indices]
        else:
            samples = data_series

        # 1. ADF test on the full series
        try:
            adf_stat, adf_pvalue, _, _, critical_values, _ = adfuller(samples, maxlag=10)
            adf_stationary = adf_pvalue < 0.05
        except Exception:
            adf_stationary = False
            adf_stat = 0.0
            adf_pvalue = 1.0
            critical_values = {}

        # 2. Rolling W1 variance test
        window = min(WINDOW_STATIONARITY, len(samples) // 4)
        if window >= 5:
            w1_internal = []
            for i in range(0, len(samples) - window, window // 2):
                s1 = samples[i:i + window]
                s2 = samples[i + window:i + 2 * window]
                if len(s1) == len(s2) and len(s1) >= 5:
                    w = WassersteinEngine().compute_w1(s1, s2)
                    w1_internal.append(w)
            if w1_internal:
                w1_var = float(np.var(w1_internal))
                w1_converged = w1_var < 0.05
            else:
                w1_var = 1.0
                w1_converged = False
        else:
            w1_var = 1.0
            w1_converged = False

        # 3. Decision
        is_stationary = adf_stationary and w1_converged
        confidence = 0.0
        if adf_stationary and w1_converged:
            confidence = 0.85
        elif adf_stationary or w1_converged:
            confidence = 0.50
        else:
            confidence = 0.15

        return {
            "is_stationary": is_stationary,
            "confidence": round(confidence, 2),
            "evidence": {
                "n_samples": len(samples),
                "adf_statistic": round(float(adf_stat), 4),
                "adf_pvalue": round(float(adf_pvalue), 6),
                "adf_stationary": adf_stationary,
                "adf_critical_1pct": float(critical_values.get("1%", 0)),
                "w1_rolling_variance": round(float(w1_var), 6),
                "w1_converged": w1_converged,
            },
        }

    @staticmethod
    def build_reference_regime(data_series: np.ndarray,
                               label: str,
                               w1_distances: List[float]) -> Dict:
        """Tạo mẫu Reference Regime mới từ dữ liệu đã kiểm định."""
        return {
            "label": label,
            "source": "sel_discovery",
            "created_at": datetime.now().isoformat(),
            "n_samples": len(data_series),
            "feature_mean": np.mean(data_series, axis=0).tolist() if data_series.ndim > 1 else [float(np.mean(data_series))],
            "feature_std": np.std(data_series, axis=0).tolist() if data_series.ndim > 1 else [float(np.std(data_series))],
            "representative_sample": data_series[-1].tolist() if data_series.ndim > 1 else [float(data_series[-1])],
            "w1_self_consistency": {
                "mean": float(np.mean(w1_distances)) if w1_distances else 0,
                "std": float(np.std(w1_distances)) if w1_distances else 0,
            },
        }


class SurvivalGovernor:
    """Chế độ Sống sót (Active Exploration) khi W1 >= theta_novelty.

    - HDR=1.0 Cash-only
    - Covariance inflation
    - Prior reset
    - Observation-only logging
    - Seeding Q_new
    """

    def __init__(self):
        self.survival_data: List[np.ndarray] = []
        self.survival_start: Optional[str] = None

    def enter(self, w1: float, params: Dict) -> Dict:
        """Kích hoạt Survival Mode."""
        self.survival_start = datetime.now().isoformat()
        self.survival_data = []
        return {
            "survival_mode": True,
            "survival_start": self.survival_start,
            "hdr": 1.0,
            "prior_type": params.get("prior_type", "diffuse"),
            "covariance_inflation": params.get("covariance_inflation", 2.0),
            "decision_logic": "minimize(E[loss]) — sống sót là ưu tiên duy nhất",
            "active_monitors": ["DXY cascade", "VIX spike", "Gold divergence", "Interbank shock"],
        }

    def record_observation(self, state_vector: np.ndarray):
        """Ghi nhận từng tick trong giai đoạn survival."""
        self.survival_data.append(state_vector.copy())

    def get_Q_new(self) -> Optional[np.ndarray]:
        """Trả về Q_new nếu đã thu thập đủ N phiên."""
        if len(self.survival_data) >= MIN_SAMPLES_FOR_REGIME:
            return np.array(self.survival_data[-MIN_SAMPLES_FOR_REGIME:])
        return None


class StructureEvolutionLayer:
    """Tầng Tiến hóa Cấu trúc (SEL) — Kiến trúc tổng thể.

    Pipeline:
      current_state → [Robust Scaler + Whitening] → [WassersteinEngine]
        → [DynamicThresholding] → [SurvivalGovernor if novelty]
    """

    FEATURE_NAMES = [
        "dxy_normalized",
        "usdvnd_deviation",
        "interbank_on_stress",
        "vnindex_breadth",
        "vnindex_adx",
        "gold_xau_momentum",
        "foreign_flow_10d",
    ]

    def __init__(self, as_of: Optional[str] = None, offline: bool = True):
        """Khởi tạo SEL.

        Args:
          as_of: Mốc ngày T (YYYY-MM-DD). None → dùng ngày EOD mới nhất trong DB.
                 State vector = dữ liệu <= T. Normalizer fit = dữ liệu < T
                 (Anti-Lookahead: ma trận hiệp biến của T KHÔNG chứa T).
          offline: True → CHỈ đọc từ SQLite cục bộ, tuyệt đối không gọi API fetch.
                   Đây là rào chắn kiểm thử chống IP ban.
        """
        self.offline = offline
        self.normalizer = SpaceNormalizationLayer()
        self.wasserstein = WassersteinEngine()
        self.threshold = DynamicThresholding()
        self.validator = StationarityValidator()
        self.survival = SurvivalGovernor()

        # Mốc thời gian T — quyết định biên anti-lookahead.
        self.as_of: str = self._resolve_as_of(as_of)

        self.regime_library: Dict = {}
        self._load_regime_library()

        # Current state
        self.current_w1: float = 0.0
        self.best_match: str = "unknown"
        self.state: str = "NORMAL"
        self.hdr_limit: Optional[float] = None
        self.is_survival: bool = False
        self.survival_params: Dict = {}
        self.stationarity_check: Dict = {}

    def _resolve_as_of(self, as_of: Optional[str]) -> str:
        """Xác định mốc T. Nếu None → ngày macro_history mới nhất trong DB cục bộ.

        KHÔNG bao giờ gọi API — chỉ đọc SQLite (giao thức offline).
        """
        if as_of:
            return as_of
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM macro_history WHERE value IS NOT NULL"
                ).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception as e:
            logger.debug(f"[SEL] _resolve_as_of fallback: {e}")
        return datetime.now().strftime("%Y-%m-%d")

    # --- Persistence ---

    def _load_regime_library(self):
        if REGIME_LIBRARY_FILE.exists():
            try:
                self.regime_library = json.loads(
                    REGIME_LIBRARY_FILE.read_text(encoding="utf-8")
                )
            except Exception:
                self.regime_library = dict(INITIAL_REFERENCE_REGIMES)
        else:
            self.regime_library = dict(INITIAL_REFERENCE_REGIMES)

    def _save_regime_library(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            # Truncate feature arrays for storage
            saveable = {}
            for name, data in self.regime_library.items():
                d = dict(data)
                for key in ["feature_mean", "feature_std", "representative_sample"]:
                    if key in d and isinstance(d[key], list) and len(d[key]) > 7:
                        d[key] = d[key][:7]
                saveable[name] = d
            REGIME_LIBRARY_FILE.write_text(
                json.dumps(saveable, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    # --- State Vector Construction ---

    def _build_state_vector(self) -> Tuple[Optional[np.ndarray], Dict]:
        """Xây dựng vector trạng thái 7-chiều từ macro_history.

        Returns: (vector, details) hoặc (None, {}) nếu thiếu dữ liệu.
        """
        dxy = self._fetch_latest("DXY")
        usdvnd = self._fetch_latest("USD_VND")
        interbank_on = self._fetch_latest("INTERBANK_ON")
        gold = self._fetch_latest("GOLD_XAU")

        breadth_pct = self._fetch_engine_metric("breadth_pct", "regime_history")
        adx = self._fetch_engine_metric("active_model", "regime_history")

        foreign_10d = self._fetch_foreign_10d()
        gold_mom = self._fetch_gold_momentum()

        dxy_norm = max(0, min(1, (dxy - 95) / 15))
        usdvnd_dev = (usdvnd - 25000) / 1000
        interbank_stress = max(0, (interbank_on - 4) / 8)
        breadth = (breadth_pct or 50) / 100 if breadth_pct else 0.5
        adx_val = (adx or 20) / 60 if adx else 0.3
        ff_10d = max(0, min(1, (foreign_10d + 2000) / 4000))

        details = {
            "dxy_raw": dxy, "dxy_norm": round(dxy_norm, 4),
            "usdvnd_raw": usdvnd, "usdvnd_dev": round(usdvnd_dev, 4),
            "interbank_on": interbank_on,
            "breadth_pct": breadth_pct, "adx_val": adx_val,
            "gold_momentum_10d": round(gold_mom, 4),
            "foreign_10d_bn": round(foreign_10d, 1),
        }

        vector = np.array([dxy_norm, usdvnd_dev, interbank_stress,
                           breadth, adx_val, gold_mom, ff_10d],
                          dtype=np.float64)
        return vector, details

    def _fetch_latest(self, variable: str) -> float:
        """Giá trị mới nhất TÍNH ĐẾN mốc T (date <= as_of). Anti-lookahead."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM macro_history WHERE variable = ? "
                "AND value IS NOT NULL AND date <= ? ORDER BY date DESC LIMIT 1",
                (variable, self.as_of)
            ).fetchone()
        return float(row[0]) if row else 0.0

    def _fetch_foreign_10d(self) -> float:
        """Dòng vốn ngoại 10 phiên TÍNH ĐẾN T (không vượt quá as_of)."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT SUM(net_value) FROM market_foreign_history "
                "WHERE date <= ? AND date >= date(?, '-10 days')",
                (self.as_of, self.as_of)
            ).fetchone()
        return float(row[0]) if row and row[0] else 0.0

    def _fetch_gold_momentum(self) -> float:
        """Momentum vàng 10 phiên gần nhất TÍNH ĐẾN T."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT value FROM macro_history WHERE variable = 'GOLD_XAU' "
                "AND value IS NOT NULL AND date <= ? ORDER BY date DESC LIMIT 10",
                (self.as_of,)
            ).fetchall()
        if len(rows) >= 2:
            vals = [float(r[0]) for r in rows]
            return (vals[0] - vals[-1]) / max(vals[-1], 1e-6)
        return 0.0

    def _fetch_engine_metric(self, column: str, table: str) -> Optional[float]:
        with get_connection() as conn:
            try:
                row = conn.execute(
                    f"SELECT {column} FROM {table} WHERE date <= ? "
                    f"ORDER BY date DESC LIMIT 1",
                    (self.as_of,)
                ).fetchone()
                val = row[0] if row else None
                if column == "active_model":
                    return 20.0 if val == "NONE" else 30.0
                return float(val) if val is not None else None
            except Exception:
                return None

    def _build_historical_sample(self) -> Optional[np.ndarray]:
        """Xây mẫu lịch sử để fit normalizer — MÀNG LỌC ANTI-LOOKAHEAD.

        NGUYÊN TẮC BẤT KHẢ XÂM PHẠM:
          Normalizer (Robust Scaler + Whitening PCA) fit trên dữ liệu 'date < as_of'
          — NGHIÊM NGẶT loại bỏ ngày T. Ma trận hiệp biến dùng để chuẩn hóa vector
          của T KHÔNG được chứa bất kỳ thông tin nào từ T hoặc sau T.

        Điều này ngăn chặn rò rỉ dữ liệu (data leakage) khi backtest/paper-trade:
        thống kê median/IQR/covariance của T được tính CHỈ từ quá khứ (T-1, T-2, ...).
        """
        rows_per_variable = 60
        variables = ["DXY", "USD_VND", "INTERBANK_ON", "GOLD_XAU"]
        data = {v: [] for v in variables}
        with get_connection() as conn:
            for var in variables:
                rows = conn.execute(
                    "SELECT value FROM macro_history WHERE variable = ? "
                    "AND value IS NOT NULL AND date < ? "  # STRICT '<': loại bỏ T
                    "ORDER BY date DESC LIMIT ?",
                    (var, self.as_of, rows_per_variable)
                ).fetchall()
                data[var] = [float(r[0]) for r in rows]

        if not all(len(v) >= 5 for v in data.values()):
            return None

        n = min(len(v) for v in data.values())
        samples = []
        for i in range(n):
            dxy_n = max(0, min(1, (data["DXY"][i] - 95) / 15))
            usdvnd_d = (data["USD_VND"][i] - 25000) / 1000
            ib_stress = max(0, (data["INTERBANK_ON"][i] - 4) / 8)
            samples.append([dxy_n, usdvnd_d, ib_stress, 0.5, 0.3, 0.0, 0.3])
        return np.array(samples, dtype=np.float64)

    # --- Main Assessment ---

    def assess(self) -> Dict:
        """Pipeline SEL chính."""
        # 1. Build state vector
        vector, details = self._build_state_vector()
        if vector is None:
            return {"status": "NO_DATA", "state": "UNKNOWN", "w1": 0, "hdr_limit": None}

        # 2. Normalize — fit on first run if not fitted
        X = vector.reshape(1, -1)
        if not self.normalizer._is_fitted:
            # Build a small historical sample for fitting
            hist = self._build_historical_sample()
            if hist is not None:
                self.normalizer.fit(hist)
        v_norm = self.normalizer.transform(X).flatten() if self.normalizer._is_fitted else vector

        # 3. Compute W1 to each reference regime
        regime_distances = {}
        for regime_name, regime_data in self.regime_library.items():
            if "feature_mean" in regime_data:
                q = np.array(regime_data["feature_mean"], dtype=np.float64)
                if len(q) == len(v_norm):
                    w = self.wasserstein.compute_w1(v_norm, q)
                    regime_distances[regime_name] = w

        if regime_distances:
            self.current_w1 = min(regime_distances.values())
            self.best_match = min(regime_distances, key=regime_distances.get)
        else:
            # Fallback: W1 to self (identity = 0)
            self.current_w1 = 0.0
            self.best_match = "unknown"

        # 4. Dynamic thresholding
        self.threshold.record_w1(self.current_w1)
        self.hdr_limit = self.threshold.compute_hdr_limit(self.current_w1)

        # 5. State classification
        if self.threshold.is_novelty(self.current_w1):
            self.state = "SURVIVAL_MODE"
            self.is_survival = True
            self.survival_params = self.threshold.get_survival_params(self.current_w1)
            survival_cmd = self.survival.enter(self.current_w1, self.survival_params)
            survival_cmd["record_observation"] = vector.tolist()
        elif self.hdr_limit is not None:
            self.state = "STRUCTURAL_SHIFT"
            self.is_survival = False
        else:
            self.state = "NORMAL"
            self.is_survival = False

        # 6. Stationarity check for Q_new
        q_new = self.survival.get_Q_new()
        if q_new is not None:
            self.stationarity_check = self.validator.validate(q_new.flatten())
            if self.stationarity_check.get("is_stationary"):
                self._certify_new_regime(q_new)

        self._save_regime_library()

        result = {
            "status": "OK",
            "timestamp": datetime.now().isoformat(),
            "state": self.state,
            "w1": round(self.current_w1, 4),
            "best_match_regime": self.best_match,
            "hdr_limit": self.hdr_limit,
            "theta_stable": round(self.threshold.theta_stable, 4),
            "theta_novelty": round(self.threshold.theta_novelty, 4),
            "state_vector_raw": {n: round(float(v), 4) for n, v in zip(self.FEATURE_NAMES, vector)},
            "regime_distances": {k: round(v, 4) for k, v in
                                  sorted(regime_distances.items(), key=lambda x: x[1])},
            "survival": {
                "active": self.is_survival,
                "params": self.survival_params if self.is_survival else {},
                "observations_collected": len(self.survival.survival_data),
            },
            "stationarity": self.stationarity_check if self.stationarity_check else None,
            "regime_library_size": len(self.regime_library),
            "audit": {
                "as_of": self.as_of,
                "offline": self.offline,
                "normalizer_fitted": self.normalizer._is_fitted,
                "anti_lookahead": "normalizer_fit_on_date_lt_as_of",
                "ot_diagnostics": dict(self.wasserstein.numerical_diagnostics),
            },
        }
        logger.info(
            f"[SEL] State={self.state} W1={self.current_w1:.4f} "
            f"HDR={self.hdr_limit} Best={self.best_match}"
        )
        return result

    def _certify_new_regime(self, q_new: np.ndarray):
        """Chứng nhận Q_new thành Reference Regime mới."""
        label = f"sel_discovery_{datetime.now().strftime('%Y%m')}"
        w1_distances = self.wasserstein._w1_history[-20:] if self.wasserstein._w1_history else [0]
        regime = StationarityValidator.build_reference_regime(
            q_new, label, w1_distances
        )
        regime["certified_at"] = datetime.now().isoformat()
        regime["w1_mean_to_library"] = round(self.current_w1, 4)
        self.regime_library[label] = regime
        logger.info(f"[SEL_CERTIFY] New regime certified: {label} — {len(q_new)} samples")
        # Reset survival buffer
        self.survival.survival_data = []

    @staticmethod
    def assess_global(as_of: Optional[str] = None, offline: bool = True) -> Dict:
        """Static wrapper.

        Args:
          as_of: Mốc ngày T. None → EOD mới nhất trong DB cục bộ.
          offline: True (mặc định) → CHỈ đọc SQLite, không gọi API (chống IP ban).
        """
        return StructureEvolutionLayer(as_of=as_of, offline=offline).assess()

    @staticmethod
    def print_report(result: Dict, lang: str = "vi"):
        """In báo cáo SEL CLI."""
        from src.utils.localization import translate, localize_state, log_structured
        t = lambda k: translate(k, lang)

        state_labels = {
            "NORMAL": "BÌNH THƯỜNG",
            "STRUCTURAL_SHIFT": "LỆCH CẤU TRÚC",
            "SURVIVAL_MODE": "CHẾ ĐỘ SINH TỒN",
        }
        state_vi = state_labels.get(result["state"], result["state"])

        print(f"\n{'=' * 65}")
        print(f"  STRUCTURE EVOLUTION LAYER — {t('tier1_gov')}")
        print(f"{'=' * 65}")
        print(f"  {t('state'):20s}: {state_vi}")
        print(f"  W1 (Wasserstein): {result['w1']}")
        print(f"  Best match:       {result['best_match_regime']}")
        print(f"  HDR limit:        {result['hdr_limit']}")
        print(f"  θ_stable:         {result['theta_stable']}")
        print(f"  θ_novelty (P99):  {result['theta_novelty']}")
        print(f"\n  -- {t('status')} --")
        for name, val in result.get("state_vector_raw", {}).items():
            print(f"  {name:25s}: {val:.4f}")
        print(f"\n  -- Regime Distances --")
        for reg, w in result.get("regime_distances", {}).items():
            print(f"  {reg:30s}: W1={w:.4f}")
        sv = result.get("survival", {})
        if sv.get("active"):
            print(f"\n  -- SURVIVAL MODE --")
            for k, v in sv.get("params", {}).items():
                print(f"  {k:30s}: {v}")
            print(f"  Observations:     {sv.get('observations_collected', 0)} / {MIN_SAMPLES_FOR_REGIME}")
        st = result.get("stationarity")
        if st and st.get("evidence", {}).get("n_samples", 0) > 0:
            print(f"\n  -- Stationarity Check --")
            print(f"  {t('status'):20s}: {'OK' if st.get('is_stationary') else 'CHUA DAT'}")
            print(f"  Confidence:       {st.get('confidence', 0):.0%}")
            ev = st.get("evidence", {})
            print(f"  ADF p-value:      {ev.get('adf_pvalue', 'N/A')}")
            print(f"  W1 variance:      {ev.get('w1_rolling_variance', 'N/A')}")
        print(f"{'=' * 65}")
        print()
