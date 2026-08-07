"""per_symbol_absorption.py — Dynamic PCA Absorption Detection (Per-Symbol)

Kiến trúc:
  1. Adaptive Z-Score Standardization Layer trước PCA
  2. Streaming PCA trên ma trận thanh khoản 5 chiều (20 x 5)
  3. Máy trạng thái tuần tự 3 pha: PANIC -> ABSORPTION_ACTIVE -> EQUILIBRIUM
  4. Adaptive HDR Unlock (1.0 -> 0.80) với Transition Confidence

Luồng hoạt động:
  Phase 1 (PANIC):      Khối ngoại xả lũ, SDI >= 0.75, HDR = 1.0
  Phase 2 (ABSORPTION): SDI giảm đơn điệu 3 phiên, domestic_absorption > 1.5x
  Phase 3 (EQUILIBRIUM): Net value dương/tiệm cận 0, giá hội tụ Value Area

Usage:
  from src.engine.per_symbol_absorption import PerSymbolAbsorption
  result = PerSymbolAbsorption("FPT").analyze()
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def _hydrate_path():
    if getattr(sys, "frozen", False):
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
LOOKBACK = 20
MIN_ROWS = 10
SDI_PANIC_THRESHOLD = 0.75
MONOTONIC_PERIODS = 3
ABSORPTION_RATIO_THRESHOLD = 1.5
VALUE_AREA_PCT = 0.03
HDR_FLOOR = 0.20
HDR_CEIL = 1.0
HDR_TARGET = 0.80
ETA_UNLOCK = 1.5
FOREIGN_STOP_THRESHOLD = -10.0  # Billion VND: ngừng xả ròng mạnh
STATE_DIR = Path(str(DATA_DIR)) / "probe_cache"


class VolumeQualityAnalyzer:
    """Bộ phân tích chất lượng khối lượng (Volume Quality Analyzer - VQA).

    Phân biệt 4 trạng thái dòng tiền:
      - CASCADE_LIQUIDATION:    Bán tháo thác đổ, không lực đỡ (FPT 15/07)
      - MARGIN_AVERAGE_DOWN:    Giằng co bắt đáy nội phiên, cuối phiên vẫn sát sàn
      - INSTITUTIONAL_ACCUMULATION: Tổ chức gom hàng, giá hồi phục mạnh
      - MARKET_MAKING_CHURN:    Nhiễu thanh khoản, volume thấp
    """

    @staticmethod
    def compute_er(open_p: float, high: float, low: float, close: float) -> float:
        """Efficiency Ratio — Hiệu quả dịch chuyển giá nội phiên."""
        denom = max(high - low, 1e-10)
        return min(abs(close - open_p) / denom, 1.0)

    @staticmethod
    def compute_vsi(close: float, high: float, low: float) -> float:
        """Volume Signature Index — Vị thế đóng cửa trong thân nến."""
        denom = max(high - low, 1e-10)
        return np.clip((close - low) / denom, 0.0, 1.0)

    @staticmethod
    def compute_volume_zscore(volume: float, vol_series: np.ndarray) -> float:
        """Volume Z-Score chuẩn hóa [-3, 3] so với lịch sử cửa sổ N."""
        mu = float(np.mean(vol_series))
        sigma = float(np.std(vol_series))
        if sigma < 1e-10:
            return 0.0
        return float(np.clip((volume - mu) / sigma, -3.0, 3.0))

    @staticmethod
    def compute_aq(er: float, vsi: float, vol_z_norm: float) -> float:
        """Accumulation Quality — Điểm chất lượng tích lũy [0, 1]."""
        term1 = 0.40 * (1.0 - er) * vsi
        term2 = 0.40 * vsi
        term3 = 0.20 * vol_z_norm
        return float(np.clip(term1 + term2 + term3, 0.0, 1.0))

    @staticmethod
    def classify(er: float, vsi: float, vol_z_norm: float, aq: float) -> str:
        """Máy trạng thái VQA — 4 trạng thái dòng tiền."""
        if vol_z_norm < 0.5:
            return "LOW_LIQUIDITY_NOISE"
        if er > 0.70 and vsi < 0.20:
            return "CASCADE_LIQUIDATION"
        if er < 0.35 and vsi < 0.25:
            return "MARGIN_AVERAGE_DOWN"
        if vsi > 0.75 and aq > 0.65:
            return "INSTITUTIONAL_ACCUMULATION"
        return "MARKET_MAKING_CHURN"

    @staticmethod
    def analyze_row(row: pd.Series, vol_series: np.ndarray) -> dict:
        """Phân tích VQA cho một phiên giao dịch."""
        er = VolumeQualityAnalyzer.compute_er(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        vsi = VolumeQualityAnalyzer.compute_vsi(float(row["close"]), float(row["high"]), float(row["low"]))
        vol_z = VolumeQualityAnalyzer.compute_volume_zscore(float(row["volume"]), vol_series)
        vol_z_norm = (vol_z + 3.0) / 6.0
        aq = VolumeQualityAnalyzer.compute_aq(er, vsi, vol_z_norm)
        classification = VolumeQualityAnalyzer.classify(er, vsi, vol_z_norm, aq)
        return {
            "er": round(er, 4),
            "vsi": round(vsi, 4),
            "volume_zscore": round(vol_z, 4),
            "volume_zscore_norm": round(vol_z_norm, 4),
            "aq": round(aq, 4),
            "classification": classification,
        }

    @staticmethod
    def is_distribution_warning(classification: str) -> bool:
        """Override: các trạng thái nguy hiểm khóa HDR."""
        return classification in ("CASCADE_LIQUIDATION", "MARGIN_AVERAGE_DOWN")


class PerSymbolAbsorption:
    """Dynamic PCA Absorption Detector cho từng mã cổ phiếu.

    Stateful: trạng thái pha + lịch sử SDI được persist vào probe_cache.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.state_file = STATE_DIR / f"abs_{self.symbol}.json"

        # Data
        self.ohlcv: pd.DataFrame = pd.DataFrame()
        self.foreign: pd.DataFrame = pd.DataFrame()

        # Matrix
        self.matrix_raw: np.ndarray | None = None
        self.matrix_norm: np.ndarray | None = None

        # PCA
        self.eigenvalues: np.ndarray | None = None
        self.sdi: float | None = None
        self.sdi_hist: list[float] = []

        # Raw metrics (latest row)
        self.pc: float | None = None  # price_change
        self.slip: float | None = None  # slippage
        self.f_net: float | None = None  # foreign net value (billion VND)
        self.dom_ratio: float | None = None  # domestic absorption ratio
        self.vpoc_dist: float | None = None  # VPOC distance

        # VQA
        self.vqa: dict | None = None

        # Macro Governor
        self.macro_state: dict | None = None

        # State machine
        self.phase: str = "UNKNOWN"
        self.hdr: float = HDR_CEIL
        self.history: list[dict] = []

        self._load()

    # --- Persistence ---------------------------------------------------------

    def _load(self):
        if self.state_file.exists():
            try:
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.sdi_hist = state.get("sdi_hist", [])
                self.phase = state.get("phase", "UNKNOWN")
                self.hdr = state.get("hdr", HDR_CEIL)
                self.history = state.get("history", [])
            except Exception as e:
                logger.warning(f"[ABS_{self.symbol}] Load state failed: {e}")

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            state = {
                "symbol": self.symbol,
                "phase": self.phase,
                "hdr": self.hdr,
                "sdi_hist": self.sdi_hist[-30:],
                "history": self.history[-20:],
                "updated_at": datetime.now().isoformat(),
            }
            self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"[ABS_{self.symbol}] Save state failed: {e}")

    # --- Data Fetching -------------------------------------------------------

    def fetch_data(self) -> bool:
        """Tải OHLCV + foreign flow của symbol."""
        with get_connection() as conn:
            self.ohlcv = pd.read_sql(
                """SELECT date, open, high, low, close, volume
                   FROM daily_ohlcv
                   WHERE symbol = ? AND date >= ?
                   ORDER BY date""",
                conn,
                params=[self.symbol, (datetime.now() - timedelta(days=LOOKBACK * 4)).strftime("%Y-%m-%d")],
            )
            self.foreign = pd.read_sql(
                """SELECT date, foreign_vol, net_vol, net_value
                   FROM market_foreign_history
                   WHERE symbol = ? AND date >= ?
                   ORDER BY date""",
                conn,
                params=[self.symbol, (datetime.now() - timedelta(days=LOOKBACK * 4)).strftime("%Y-%m-%d")],
            )

        if self.ohlcv.empty:
            logger.warning(f"[ABS_{self.symbol}] No OHLCV data")
            return False

        self.ohlcv["date"] = pd.to_datetime(self.ohlcv["date"])
        self.foreign["date"] = pd.to_datetime(self.foreign["date"])
        return True

    # --- Liquidity Matrix (5 features) ---------------------------------------

    def build_matrix(self) -> np.ndarray:
        """Xây ma trận thanh khoản 5 chiều X[N x 5].

        Features:
          [0] price_change:  % thay đổi close (dương/âm)
          [1] slippage:      (high - low) / close, clip [0, 0.15]
          [2] vpoc_dist:     (close - VWAP_5d) / VWAP_5d — độ lệch khỏi vùng giá trị
          [3] foreign_net:   net_value * -1 (dương = càng xả mạnh)
          [4] dom_absorb:    volume / max(foreign_vol, 1) — tỷ lệ hấp thụ nội
        """
        df = self.ohlcv.copy()

        pct = df["close"].pct_change().fillna(0)
        pct = np.where(np.isinf(pct), 0, pct)

        slip = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
        slip = slip.clip(0, 0.15).fillna(0).values

        # VWAP 5 ngày làm proxy cho POC
        vwap_5d = ((df["close"] * df["volume"]).rolling(5).sum() / df["volume"].rolling(5).sum()).fillna(df["close"])
        vpoc = ((df["close"] - vwap_5d) / vwap_5d.replace(0, np.nan)).fillna(0).values

        # Merge foreign
        fg = self.foreign.merge(df[["date"]], on="date", how="right").sort_values("date")
        fg["net_value"] = fg["net_value"].fillna(0).values
        fg["foreign_vol"] = fg["foreign_vol"].fillna(0).values
        fg["dom_vol"] = df["volume"].fillna(0).values - fg["foreign_vol"].values
        fg["dom_vol"] = np.maximum(fg["dom_vol"].values, 1)

        foreign_net = fg["net_value"].values * -1  # Positive = intense selling
        dom_absorb = df["volume"].fillna(0).values / np.maximum(fg["foreign_vol"].values, 1)
        dom_absorb = np.clip(dom_absorb, 0, 50)

        # Store latest raw metrics
        self.pc = float(pct[-1]) if len(pct) > 0 else 0.0
        self.slip = float(slip[-1]) if len(slip) > 0 else 0.0
        self.vpoc_dist = float(vpoc[-1]) if len(vpoc) > 0 else 0.0
        self.dom_ratio = float(dom_absorb[-1]) if len(dom_absorb) > 0 else 0.0
        self.f_net = float(fg["net_value"].iloc[-1]) if len(fg) > 0 else 0.0

        matrix = np.column_stack([pct, slip, vpoc, foreign_net, dom_absorb])
        matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)

        if matrix.shape[0] < MIN_ROWS:
            self.matrix_raw = None
            return np.array([])

        self.matrix_raw = matrix[-LOOKBACK:]
        return self.matrix_raw

    # --- Adaptive Z-Score Standardization ------------------------------------

    def _zscore(self, X: np.ndarray) -> np.ndarray:
        """Adaptive Z-Score: (X - mu) / sigma, rolling trong cửa sổ N."""
        if X.shape[0] < 2:
            return X
        mu = np.nanmean(X, axis=0)
        sigma = np.nanstd(X, axis=0)
        sigma = np.where(sigma < 1e-10, 1.0, sigma)
        return (X - mu) / sigma

    # --- Streaming PCA & SDI -------------------------------------------------

    def compute_pca(self):
        """Streaming PCA trên ma trận đã Z-score norm."""
        if self.matrix_raw is None or self.matrix_raw.shape[0] < 3:
            return

        X_norm = self._zscore(self.matrix_raw)
        self.matrix_norm = X_norm

        # Mask NaN rows
        mask = ~np.isnan(X_norm).any(axis=1)
        if mask.sum() < 3:
            return

        cov = np.cov(X_norm[mask], rowvar=False)
        eigenvalues, _ = np.linalg.eigh(cov)
        eigenvalues = np.sort(eigenvalues)[::-1]
        self.eigenvalues = eigenvalues

        lam_sum = max(np.sum(eigenvalues), 1e-10)
        self.sdi = float(eigenvalues[0] / lam_sum)

        self.sdi_hist.append(self.sdi)
        self.sdi_hist = self.sdi_hist[-30:]

    # --- Volume Quality Analyzer (VQA) ----------------------------------------

    def _compute_vqa(self):
        """Phân tích chất lượng khối lượng phiên gần nhất bằng VQA."""
        if self.ohlcv.empty or len(self.ohlcv) < 2:
            self.vqa = {"classification": "NO_DATA", "aq": 0.0}
            return
        latest = self.ohlcv.iloc[-1]
        vol_series = self.ohlcv["volume"].values
        # Dùng rolling 20 phiên để tính Z-score, nhưng fallback về toàn bộ dữ liệu
        if len(vol_series) >= 20:
            vol_window = vol_series[-20:]
        else:
            vol_window = vol_series
        self.vqa = VolumeQualityAnalyzer.analyze_row(latest, vol_window)
        if self.vqa["classification"] == "CASCADE_LIQUIDATION":
            logger.info(f"[VQA_{self.symbol}] Cascade Liquidation — ER={self.vqa['er']:.4f}, VSI={self.vqa['vsi']:.4f}")
        elif self.vqa["classification"] == "MARGIN_AVERAGE_DOWN":
            logger.info(f"[VQA_{self.symbol}] Margin Average Down — ER={self.vqa['er']:.4f}, VSI={self.vqa['vsi']:.4f}")

    # --- 3-Phase State Machine -----------------------------------------------

    def _detect_phase(self) -> str:
        """Xác định pha hiện tại dựa trên SDI + dòng tiền.

        Returns: PANIC | ABSORPTION_ACTIVE | EQUILIBRIUM | MONITORING
        """
        if self.sdi is None:
            return "UNKNOWN"

        # Phase 1: Panic
        if self.sdi >= SDI_PANIC_THRESHOLD:
            return "PANIC"

        # Phase 2: Absorption Active
        recent = self.sdi_hist[-MONOTONIC_PERIODS:] if len(self.sdi_hist) >= MONOTONIC_PERIODS else []
        if len(recent) == MONOTONIC_PERIODS:
            monotonic = all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1))
            absorbing = self.dom_ratio is not None and self.dom_ratio > ABSORPTION_RATIO_THRESHOLD
            if monotonic and absorbing:
                return "ABSORPTION_ACTIVE"

        # Phase 3: Equilibrium
        net_ok = self.f_net is not None and self.f_net > FOREIGN_STOP_THRESHOLD
        conv_ok = self.vpoc_dist is not None and abs(self.vpoc_dist) < VALUE_AREA_PCT
        if net_ok and conv_ok:
            return "EQUILIBRIUM"

        return "MONITORING"

    # --- Transition Confidence (Phase 2 -> 3) --------------------------------

    def _compute_transition_confidence(self) -> dict:
        """Đo độ tin cậy chuyển pha ABSORPTION_ACTIVE -> EQUILIBRIUM.

        Phân biệt:
          - REAL_EXHAUSTION: SDI decelerating decay + volume expanding at value area
          - FAKE_PAUSE:      SDI abrupt stop (v=0) + volume contracting

        Returns:
          confidence: 0.0 (fake) -> 1.0 (real)
          signal:     REAL_EXHAUSTION | FAKE_PAUSE | INSUFFICIENT
          evidence:   dict of metrics
        """
        if len(self.sdi_hist) < 6 or self.sdi is None:
            return {"confidence": 0.0, "signal": "INSUFFICIENT_HISTORY", "evidence": {}}

        recent_5 = self.sdi_hist[-5:]
        vel = [recent_5[i + 1] - recent_5[i] for i in range(len(recent_5) - 1)]
        accel = [vel[i + 1] - vel[i] for i in range(len(vel) - 1)]

        avg_vel = float(np.mean(vel))
        avg_accel = float(np.mean(accel)) if accel else 0.0

        # SDI decay quality: real exhaustion = velocity negative, acceleration positive
        vel_score = np.clip(1.0 - abs(avg_vel) / 0.05, 0, 1)  # velocity near 0
        accel_score = np.clip(avg_accel / 0.01, 0, 1) if avg_accel > 0 else 0.0  # decelerating
        sdi_decay_quality = (vel_score + accel_score) / 2

        # Volume profile: real exhaustion has volume converging to value area
        # Fake pause: volume drops off
        if hasattr(self, "ohlcv") and len(self.ohlcv) >= 3:
            recent_vol = self.ohlcv["volume"].iloc[-3:].values
            vol_trend = (recent_vol[-1] - recent_vol[0]) / max(recent_vol[0], 1)
            vol_expanding = vol_trend > -0.1
            vol_quality = 1.0 if vol_expanding else 0.3
        else:
            vol_quality = 0.5

        # Foreign net value trajectory
        if self.f_net is not None:
            fnet_quality = np.clip((self.f_net - FOREIGN_STOP_THRESHOLD) / 100, 0, 1)
        else:
            fnet_quality = 0.0

        confidence = 0.4 * sdi_decay_quality + 0.35 * vol_quality + 0.25 * fnet_quality
        confidence = np.clip(confidence, 0.0, 1.0)

        signal = "REAL_EXHAUSTION" if confidence >= 0.6 else "FAKE_PAUSE" if confidence < 0.35 else "UNCERTAIN"

        return {
            "confidence": round(float(confidence), 4),
            "signal": signal,
            "evidence": {
                "sdi_avg_velocity": round(avg_vel, 6),
                "sdi_avg_acceleration": round(avg_accel, 6),
                "sdi_decay_quality": round(float(sdi_decay_quality), 4),
                "volume_trend": round(float(vol_trend if "vol_trend" in dir() else 0), 4),
                "vol_quality": round(float(vol_quality), 4),
                "fnet_quality": round(float(fnet_quality), 4),
            },
        }

    # --- HDR Unlock ----------------------------------------------------------

    def _compute_hdr(self) -> float:
        """Adaptive HDR unlock (1.0 -> 0.80) theo máy trạng thái.

        - Phase 1 (PANIC):          HDR = 1.0 (locked)
        - Phase 2 (ABSORPTION):     HDR = 1.0 (preparing)
        - Phase 3 (EQUILIBRIUM):    HDR = unlock(1.0 -> 0.80)
        """
        if self.phase not in ("EQUILIBRIUM",):
            return HDR_CEIL

        if self.sdi is None or len(self.sdi_hist) < 2:
            return self.hdr

        ema10 = float(pd.Series(self.sdi_hist).ewm(span=10, adjust=False).mean().iloc[-1])
        delta_sdi = max(ema10 - self.sdi, 0)

        vr = min(self.dom_ratio / ABSORPTION_RATIO_THRESHOLD, 2.0) if self.dom_ratio else 0.5
        progress = min(ETA_UNLOCK * delta_sdi * vr, 1.0)
        hdr_new = HDR_CEIL - progress * (HDR_CEIL - HDR_TARGET)
        return round(float(np.clip(hdr_new, HDR_TARGET, HDR_CEIL)), 2)

    # --- Main Pipeline -------------------------------------------------------

    def analyze(self, target_date: str | None = None, macro_state: dict | None = None) -> dict:
        """Pipeline chính: fetch -> build -> PCA -> VQA -> phase -> hdr.

        Two-Tier Architecture:
          1. Macro Governor (Tier 1) — quyết định khóa/mở cửa vĩ mô
          2. Micro Executor (Tier 2) — chỉ chạy khi Governor an toàn
        """
        # --- Tier 1: Macro Governor Gatekeeper ---
        if macro_state is None:
            from src.engine.macro_governor import MacroGovernor

            macro_state = MacroGovernor().assess()

        governor_conf = macro_state.get("confidence", 0.0)
        governor_hdr = macro_state.get("hdr_override")
        governor_state = macro_state.get("state", "UNKNOWN")

        # Nếu Governor khóa cứng HDR, trả về ngay
        macro_locked = governor_hdr is not None
        if macro_locked:
            self.hdr = governor_hdr
            self.macro_state = macro_state
            result = self._build_result(target_date)
            result["governor_lock"] = True
            result["governor_state"] = governor_state
            result["governor_confidence"] = governor_conf
            self._save()
            return result

        # --- Tier 2: Micro Executor ---
        if not self.fetch_data():
            return self._default_result("NO_DATA")

        self.build_matrix()
        if self.matrix_raw is None or len(self.matrix_raw) < MIN_ROWS:
            return self._default_result("INSUFFICIENT_DATA")

        self.compute_pca()
        self._compute_vqa()

        distribution_override = False
        if self.vqa and VolumeQualityAnalyzer.is_distribution_warning(self.vqa["classification"]):
            distribution_override = True
            if self.phase == "ABSORPTION_ACTIVE":
                from src.utils.localization import log_structured

                log_structured(
                    logger,
                    "VQA_OVERRIDE",
                    "DISTRIBUTION_WARNING",
                    {
                        "symbol": self.symbol,
                        "vqa_class": self.vqa["classification"],
                        "sdi": round(self.sdi, 4) if self.sdi else None,
                    },
                )
                self.phase = "MONITORING"
                self.hdr = HDR_CEIL
                self.history.append(
                    {
                        "from": "ABSORPTION_ACTIVE",
                        "to": "MONITORING_VQA_OVERRIDE",
                        "date": (target_date or datetime.now().strftime("%Y-%m-%d")),
                        "sdi": self.sdi,
                        "vqa_class": self.vqa["classification"],
                        "reason": "DISTRIBUTION_WARNING",
                    }
                )

        new_phase = self._detect_phase()
        transition = new_phase != self.phase

        if not distribution_override:
            if transition:
                entry = {
                    "from": self.phase,
                    "to": new_phase,
                    "date": (target_date or datetime.now().strftime("%Y-%m-%d")),
                    "sdi": self.sdi,
                    "dom_ratio": self.dom_ratio,
                    "f_net": self.f_net,
                    "vqa_class": self.vqa["classification"] if self.vqa else None,
                }
                self.history.append(entry)
                from src.utils.localization import log_structured

                log_structured(
                    logger,
                    f"ABS_{self.symbol}",
                    new_phase,
                    {
                        "from": self.phase,
                        "sdi": round(self.sdi, 4) if self.sdi else None,
                        "vqa_class": entry["vqa_class"],
                    },
                )
            self.phase = new_phase

        if not distribution_override or self.phase == "MONITORING":
            self.hdr = self._compute_hdr()

        self.macro_state = macro_state
        self._save()
        result = self._build_result(target_date)
        result["governor_lock"] = False
        result["governor_state"] = governor_state
        result["governor_confidence"] = governor_conf
        return result

    # --- Result Builders -----------------------------------------------------

    def _default_result(self, status: str) -> dict:
        return {
            "symbol": self.symbol,
            "status": status,
            "phase": self.phase,
            "hdr": self.hdr,
            "sdi": self.sdi,
            "details": {},
            "governor_lock": self.macro_state.get("hdr_override") is not None if self.macro_state else False,
            "governor_state": self.macro_state.get("state", "UNKNOWN") if self.macro_state else "UNKNOWN",
            "governor_confidence": self.macro_state.get("confidence", 0.0) if self.macro_state else 0.0,
        }

    def _build_result(self, target_date: str | None = None) -> dict:
        eigenvalues_list = [round(float(v), 4) for v in self.eigenvalues] if self.eigenvalues is not None else None
        tc = (
            self._compute_transition_confidence()
            if self.phase == "ABSORPTION_ACTIVE"
            else {"confidence": 0.0, "signal": "N/A"}
        )
        dist_warn = bool(self.vqa and VolumeQualityAnalyzer.is_distribution_warning(self.vqa["classification"]))
        vqa_data = (
            {
                "er": self.vqa["er"],
                "vsi": self.vqa["vsi"],
                "volume_zscore": self.vqa["volume_zscore"],
                "volume_zscore_norm": self.vqa["volume_zscore_norm"],
                "aq": self.vqa["aq"],
                "classification": self.vqa["classification"],
            }
            if self.vqa
            else None
        )

        return {
            "symbol": self.symbol,
            "status": "OK" if self.sdi is not None else "LOW_DATA",
            "target_date": target_date or datetime.now().strftime("%Y-%m-%d"),
            "phase": self.phase,
            "hdr": self.hdr,
            "sdi": round(self.sdi, 4) if self.sdi is not None else None,
            "distribution_warning": dist_warn,
            "details": {
                "price_change_pct": round(self.pc * 100, 2) if self.pc is not None else None,
                "slippage_pct": round(self.slip * 100, 2) if self.slip is not None else None,
                "vpoc_distance_pct": round(self.vpoc_dist * 100, 2) if self.vpoc_dist is not None else None,
                "domestic_absorption_ratio": round(self.dom_ratio, 2) if self.dom_ratio is not None else None,
                "foreign_net_value_bn": round(self.f_net, 1) if self.f_net is not None else None,
                "eigenvalues": eigenvalues_list,
                "sdi_history": [round(v, 4) for v in self.sdi_hist[-10:]],
            },
            "vqa": vqa_data,
            "transition_confidence": tc,
            "hdr_progress": {
                "target": HDR_TARGET,
                "current": self.hdr,
                "locked_at": HDR_CEIL,
                "phase_required": "EQUILIBRIUM",
            },
        }

    # --- Report --------------------------------------------------------------

    @staticmethod
    def print_report(result: dict, lang: str = "vi"):
        """In báo cáo CLI."""
        from src.utils.localization import localize_classification, localize_phase, localize_state, translate

        def t(key):
            return translate(key, lang)

        symbol = result["symbol"]
        phase = result["phase"]
        hdr = result["hdr"]
        sdi = result["sdi"]
        d = result.get("details", {})
        tc = result.get("transition_confidence", {})
        vqa = result.get("vqa")

        # Localize phase + governor state
        phase_vi = localize_phase(phase, lang)
        gov_state = localize_state(result.get("governor_state", "UNKNOWN"), lang)
        gov_lock = result.get("governor_lock", False)
        lock_str = f"{t('locked')} (HDR={result.get('hdr', 1.0):.2f})" if gov_lock else t("open")

        # VQA classification
        vqa_class_vi = localize_classification(vqa["classification"], lang) if vqa else "N/A"

        # Phase icons
        icons = {
            "PANIC": "R",
            "ABSORPTION_ACTIVE": "Y",
            "EQUILIBRIUM": "G",
            "MONITORING": "B",
            "MONITORING_VQA_OVERRIDE": "Y",
            "UNKNOWN": "W",
        }
        icon = icons.get(phase, "W")

        print(f"\n{'=' * 65}")
        print(f"  {t('symbol')}: {symbol}")
        print(f"{'=' * 65}")

        # Tier 1
        print(f"  Tier 1 {t('tier1_gov'):10s}: {gov_state:25s} (conf={result.get('governor_confidence', 0):.1f}%, {lock_str})")

        # Tier 2
        print(f"  {icon} {t('b_phase'):20s}: {phase_vi}")
        print(f"  {t('hdr_label'):20s}: {hdr:.2f}  ({t('cash_only')})")
        if sdi is not None:
            print(f"  SDI               : {sdi:.4f}")
        print(f"  {t('distribution'):20s}: {t('yes') if result.get('distribution_warning') else t('no')}")

        # Raw metrics
        print(f"\n  -- {t('status')} --")
        for label, key in [
            (t("price_change"), "price_change_pct"),
            (t("slippage"), "slippage_pct"),
            (t("vpoc_distance"), "vpoc_distance_pct"),
            (t("domestic_absorb"), "domestic_absorption_ratio"),
            (t("foreign_net"), "foreign_net_value_bn"),
        ]:
            v = d.get(key)
            if v is not None:
                print(f"  {label:25s}: {v:>10.2f}")
        if d.get("eigenvalues"):
            print(f"  Eigenvalues       : {d['eigenvalues']}")

        # VQA section
        if vqa:
            print(f"\n  -- VQA ({t('classification')}) --")
            print(f"  {t('er_label'):25s}: {vqa['er']:.4f}")
            print(f"  {t('vsi_label'):25s}: {vqa['vsi']:.4f}")
            print(f"  {t('volume_zscore'):25s}: {vqa['volume_zscore']:.4f}  (norm: {vqa['volume_zscore_norm']:.4f})")
            print(f"  {t('aq_label'):25s}: {vqa['aq']:.4f}")
            print(f"  {t('classification'):25s}: {vqa_class_vi}")

        # Transition confidence
        if tc.get("signal") not in (None, "N/A"):
            print("\n  -- Transition Confidence --")
            print(f"  Signal            : {tc.get('signal', 'N/A')}")
            print(f"  Confidence        : {tc.get('confidence', 0):.2%}")

        # HDR progress
        print(f"\n  -- HDR {t('unlock')} --")
        hp = result.get("hdr_progress", {})
        print(f"  {t('current'):25s}: {hp.get('current', 1.0):.2f}")
        print(f"  {t('target'):25s}: {hp.get('target', 0.80):.2f}")
        print(f"  {t('hdr_phase_req'):25s}: {localize_phase(hp.get('phase_required', 'N/A'), lang)}")
        print(f"{'=' * 65}")
        print()

    @staticmethod
    def print_history(symbol: str, n: int = 5):
        """In lịch sử chuyển pha từ state file."""
        state_file = STATE_DIR / f"abs_{symbol.upper()}.json"
        if not state_file.exists():
            print(f"  Khong co lich su cho {symbol}")
            return
        state = json.loads(state_file.read_text(encoding="utf-8"))
        history = state.get("history", [])[-n:]
        print(f"\n{'=' * 65}")
        print(f"  ABSORPTION HISTORY — {symbol.upper()}")
        print(f"{'=' * 65}")
        for h in reversed(history):
            print(
                f"  {h['date']}: {h['from']:22s} -> {h['to']:22s}  SDI={h.get('sdi', 0):.4f}  DR={h.get('dom_ratio', 0):.2f}"
            )
        print(f"{'=' * 65}")
        print()


def run_per_symbol_absorption(symbol: str, show_details: bool = True) -> dict:
    """Wrapper CLI."""
    detector = PerSymbolAbsorption(symbol)
    result = detector.analyze()
    if show_details:
        PerSymbolAbsorption.print_report(result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Per-Symbol Absorption Detector")
    parser.add_argument("--symbol", required=True, help="Ma co phieu (VD: FPT)")
    parser.add_argument("--history", action="store_true", help="In lich su chuyen pha")
    parser.add_argument("--quiet", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.history:
        PerSymbolAbsorption.print_history(args.symbol)
    else:
        result = run_per_symbol_absorption(args.symbol, show_details=not args.quiet)
        if args.quiet:
            print(json.dumps(result, indent=2, ensure_ascii=False))
