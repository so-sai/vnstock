"""absorption_detector.py — Phát hiện Cân bằng Hấp thụ thị trường.

Kiến trúc:
  1. Streaming PCA trên Liquidity Matrix [decline, VPOC_volume, slippage]
  2. SDI (Spectral Dominance Index) — λ_max / sum(λ_i)
  3. Volume Profile Convergence Filter — POC / Value Area
  4. Adaptive HDR unlock — giải phóng Cash-only state

Luồng hoạt động:
  panic_sdi >= 0.75 → kích hoạt theo dõi
  SDI giảm đơn điệu 3 phiên + cắt xuống EMA10(SDI) - σ_SDI → tín hiệu equilibrium
  Volume_Ratio > 0.65 + price trong Value Area → xác nhận hấp thụ
  HDR_new = clamp(1.0 - η · ΔSDI · VR, 0.20, 1.0) → mở khóa từng phần

Chạy: python ptck.py absorption-detector [--date YYYY-MM-DD]
"""

import json
import sys
from collections import deque
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
LOOKBACK_PCA = 20
LOOKBACK_VOLUME = 5
SDI_PANIC_THRESHOLD = 0.75
SDI_MONOTONIC_PERIODS = 3
VOLUME_RATIO_THRESHOLD = 0.65
HDR_FLOOR = 0.20
HDR_CEIL = 1.0
ETA_UNLOCK = 1.5


class AbsorptionDetector:
    """Streaming PCA-based Absorption Detection Engine.

    Stateful: giữ lịch sử SDI để phát hiện hội tụ phổ.
    """

    def __init__(self, eta_unlock: float = ETA_UNLOCK):
        self.eta_unlock = eta_unlock
        self._sdi_history: deque = deque(maxlen=30)
        self._eigen_history: deque = deque(maxlen=30)
        self._rotation_history: deque = deque(maxlen=10)
        self._last_pca_result: dict | None = None
        self._last_liquidity_matrix: np.ndarray | None = None

    # ── 1. Liquidity Matrix Construction ────────────────────────────────

    def _load_market_data(self, target_date: str | None = None) -> pd.DataFrame:
        """Load VNINDEX index data — chỉ kéo 1 dòng đại diện, triệt tiêu I/O."""
        end = pd.Timestamp(target_date) if target_date else pd.Timestamp.now()
        start = end - timedelta(days=LOOKBACK_PCA * 4)

        with get_connection() as conn:
            df = pd.read_sql(
                """SELECT date, symbol, open, high, low, close, volume
                   FROM daily_ohlcv
                   WHERE symbol = 'VNINDEX' AND date >= ? AND date <= ?
                   ORDER BY date""",
                conn,
                params=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"], format="mixed")
        return df

    def _compute_liquidity_matrix(self, df: pd.DataFrame) -> np.ndarray:
        """Xây dựng Liquidity Matrix [N_days x 3 features].

        Features:
          [0] price_decline: % thay đổi của VNINDEX close (âm = giảm)
          [1] VPOC_volume: tổng khối lượng tại vùng giá thấp nhất (volume-weighted)
          [2] slippage: (high - low) / close — độ trượt giá

        PCA áp dụng trên Ma trận Thanh khoản, KHÔNG phải trên giá.
        """
        if df.empty:
            return np.array([])

        daily = df.sort_values("date").reset_index(drop=True)

        if len(daily) < LOOKBACK_PCA:
            return np.array([])

        closes = daily["close"].values
        volumes = daily["volume"].values
        highs = daily["high"].values
        lows = daily["low"].values

        pct_chg = np.diff(closes, prepend=closes[0]) / np.maximum(closes[0], 1e-10)
        pct_chg = np.where(np.isinf(pct_chg), 0, pct_chg)
        price_decline = np.minimum(pct_chg, 0)

        vpoc_volume = np.zeros(len(daily))
        for i in range(1, len(daily)):
            low_idx = int((lows[i] / max(closes[i], 1e-10)) * 10)
            low_idx = min(low_idx, 9)
            vpoc_volume[i] = volumes[i] * (1.0 - low_idx / 10.0)

        slippage = np.where(closes > 0, (highs - lows) / closes, 0)
        slippage = np.clip(slippage, 0, 0.15)

        matrix = np.column_stack([price_decline, vpoc_volume, slippage])
        matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)

        if matrix.shape[0] < LOOKBACK_PCA:
            return np.array([])

        return matrix[-LOOKBACK_PCA:]

    # ── 2. Streaming PCA & SDI ─────────────────────────────────────────

    def _compute_streaming_pca(self, matrix: np.ndarray) -> dict:
        """Streaming PCA trên Liquidity Matrix — covariance-based.

        KHÔNG dùng eigenvalue decomposition trên giá,
        mà trên Ma trận Thanh khoản.
        """
        if matrix.shape[0] < 3:
            return {"status": "INSUFFICIENT_DATA"}

        std = np.std(matrix, axis=0)
        std = np.where(std < 1e-10, 1.0, std)
        standardized = (matrix - np.mean(matrix, axis=0)) / std

        cov_matrix = np.cov(standardized, rowvar=False)

        eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)
        eigenvalues = np.sort(eigenvalues)[::-1]

        lambda_sum = max(np.sum(eigenvalues), 1e-10)
        sdi = eigenvalues[0] / lambda_sum

        total_variance = np.sum(eigenvalues)
        pc1_var = eigenvalues[0] / max(total_variance, 1e-10)
        pc2_var = eigenvalues[1] / max(total_variance, 1e-10) if len(eigenvalues) > 1 else 0
        pc3_var = eigenvalues[2] / max(total_variance, 1e-10) if len(eigenvalues) > 2 else 0

        pc1 = eigenvectors[:, -1]
        pc1 = pc1 / max(np.linalg.norm(pc1), 1e-10)
        loadings = {f"PC1_{i}": float(round(pc1[i], 4)) for i in range(len(pc1))}

        return {
            "status": "ok",
            "eigenvalues": eigenvalues.tolist(),
            "lambda_max": float(eigenvalues[0]),
            "sdi": float(round(sdi, 4)),
            "pc1_variance": float(round(pc1_var, 4)),
            "pc2_variance": float(round(pc2_var, 4)),
            "pc3_variance": float(round(pc3_var, 4)),
            "loadings": loadings,
            "n_observations": matrix.shape[0],
        }

    def _detect_equilibrium(self, sdi: float) -> dict:
        """Phát hiện hội tụ phổ dựa trên lịch sử SDI.

        Điều kiện:
          1. SDI giảm ngặt đơn điệu ≥ SDI_MONOTONIC_PERIODS phiên
          2. SDI < EMA10(SDI) - σ(SDI)
        """
        self._sdi_history.append(sdi)
        if len(self._sdi_history) < SDI_MONOTONIC_PERIODS + 1:
            return {"equilibrium": False, "reason": "INSUFFICIENT_HISTORY"}

        recent = list(self._sdi_history)[-(SDI_MONOTONIC_PERIODS + 1) :]
        sdi_values = np.array(recent)

        sdi_diff = np.diff(sdi_values)
        monotonic = bool(np.all(sdi_diff < 0))

        if not monotonic:
            return {"equilibrium": False, "reason": "NOT_MONOTONIC"}

        if self._sdi_history:
            arr = np.array(self._sdi_history)
            ema10 = pd.Series(arr).ewm(span=10, adjust=False).mean().iloc[-1]
            std_sdi = float(np.std(arr))
            threshold = float(ema10) - std_sdi
            below_threshold = bool(sdi < threshold)

            return {
                "equilibrium": below_threshold,
                "sdi_current": float(sdi),
                "ema10_sdi": round(float(ema10), 4),
                "std_sdi": round(std_sdi, 4),
                "threshold": round(threshold, 4),
                "monotonic_decrease_months": SDI_MONOTONIC_PERIODS,
                "reason": "EQUILIBRIUM_CONFIRMED" if below_threshold else "ABOVE_THRESHOLD",
            }

        return {"equilibrium": False, "reason": "NO_HISTORY"}

    # ── 3. Volume Profile Convergence ──────────────────────────────────

    def _compute_volume_profile(self, df: pd.DataFrame) -> dict:
        """Volume Profile Convergence Filter.

        Value Area: vùng giá bao quanh POC chứa 70% khối lượng.
        Volume_Ratio = Volume_Value_Area / Volume_Total_5D
        """
        if df.empty:
            return {"status": "INSUFFICIENT_DATA"}

        daily = df.sort_values("date").reset_index(drop=True)

        if len(daily) < 5:
            return {"status": "INSUFFICIENT_DATA"}

        recent_5 = daily.tail(5)
        vol_total_5d = recent_5["volume"].sum()

        if vol_total_5d == 0:
            return {"status": "INSUFFICIENT_DATA"}

        daily["low"].values
        daily["high"].values
        daily["volume"].values

        poc_price = daily.loc[daily["volume"].idxmax(), "close"]
        daily.loc[daily["volume"].idxmax()]

        val = poc_price * 0.97
        vah = poc_price * 1.03

        vol_value_area = 0
        for i in range(len(daily)):
            if val <= daily["close"].iloc[i] <= vah:
                vol_value_area += daily["volume"].iloc[i]

        volume_ratio = vol_value_area / max(vol_total_5d, 1)
        price_vnindex = daily["close"].iloc[-1]
        price_in_value_area = val <= price_vnindex <= vah

        return {
            "status": "ok",
            "poc_price": round(float(poc_price), 2),
            "val": round(float(val), 2),
            "vah": round(float(vah), 2),
            "price_current": round(float(price_vnindex), 2),
            "price_in_value_area": bool(price_in_value_area),
            "vol_value_area": int(vol_value_area),
            "vol_total_5d": int(vol_total_5d),
            "volume_ratio": round(float(volume_ratio), 4),
            "volume_converged": bool(volume_ratio > VOLUME_RATIO_THRESHOLD and price_in_value_area),
        }

    # ── 4. Adaptive HDR Unlock ─────────────────────────────────────────

    def _compute_hdr_unlock(self, pca_result: dict, equilibrium: dict, volume_profile: dict) -> float:
        """Phương trình mở khóa thích nghi HDR.

        HDR_new = clamp(1.0 - η · (EMA10(SDI) - SDI) · Volume_Ratio, 0.20, 1.0)

        Chỉ unlock khi cả SDI + Volume Profile đều xác nhận.
        """
        if equilibrium.get("status") == "INSUFFICIENT_DATA":
            return HDR_CEIL

        if not equilibrium.get("equilibrium") or not volume_profile.get("volume_converged"):
            return HDR_CEIL

        sdi_current = equilibrium.get("sdi_current", 0)
        ema10_sdi = equilibrium.get("ema10_sdi", 0)
        delta_sdi = ema10_sdi - sdi_current
        volume_ratio = volume_profile.get("volume_ratio", 0)

        hdr_new = 1.0 - self.eta_unlock * delta_sdi * volume_ratio
        hdr_new = float(np.clip(hdr_new, HDR_FLOOR, HDR_CEIL))
        return round(hdr_new, 4)

    # ── 5. Main Analysis ──────────────────────────────────────────────

    def analyze(self, target_date: str | None = None) -> dict:
        """Phân tích toàn bộ: PCA → SDI → Volume Profile → HDR unlock."""
        df = self._load_market_data(target_date)

        matrix = self._compute_liquidity_matrix(df)
        if matrix.size == 0:
            return {"status": "INSUFFICIENT_DATA", "message": "Không đủ dữ liệu thị trường để phân tích PCA."}

        pca_result = self._compute_streaming_pca(matrix)
        self._last_pca_result = pca_result
        self._last_liquidity_matrix = matrix

        if pca_result.get("status") != "ok":
            return {"status": "ERROR", "pca": pca_result}

        sdi = pca_result["sdi"]
        equilibrium = self._detect_equilibrium(sdi)

        volume_profile = self._compute_volume_profile(df)

        hdr_new = self._compute_hdr_unlock(pca_result, equilibrium, volume_profile)
        current_hdr = HDR_CEIL

        panic_active = sdi >= SDI_PANIC_THRESHOLD

        result = {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "target_date": target_date or datetime.now().strftime("%Y-%m-%d"),
            "panic_active": bool(panic_active),
            "current_hdr": float(current_hdr),
            "pca": {
                "sdi": float(pca_result["sdi"]),
                "lambda_max": float(pca_result["lambda_max"]),
                "eigenvalues": [float(v) for v in pca_result["eigenvalues"]],
                "pc1_variance": float(pca_result["pc1_variance"]),
                "pc2_variance": float(pca_result["pc2_variance"]),
                "pc3_variance": float(pca_result["pc3_variance"]),
                "loadings": pca_result["loadings"],
                "n_observations": int(pca_result["n_observations"]),
            },
            "sdi_history": [float(v) for v in self._sdi_history],
            "equilibrium": {
                "confirmed": equilibrium.get("equilibrium", False),
                "reason": equilibrium.get("reason", "N/A"),
                "sdi_current": equilibrium.get("sdi_current"),
                "ema10_sdi": equilibrium.get("ema10_sdi"),
                "std_sdi": equilibrium.get("std_sdi"),
                "threshold": equilibrium.get("threshold"),
                "monotonic_decrease_months": equilibrium.get("monotonic_decrease_months", 0),
            },
            "volume_profile": {
                "poc_price": volume_profile.get("poc_price"),
                "val": volume_profile.get("val"),
                "vah": volume_profile.get("vah"),
                "price_current": volume_profile.get("price_current"),
                "price_in_value_area": volume_profile.get("price_in_value_area", False),
                "volume_ratio": volume_profile.get("volume_ratio", 0),
                "volume_converged": volume_profile.get("volume_converged", False),
                "vol_value_area": volume_profile.get("vol_value_area"),
                "vol_total_5d": volume_profile.get("vol_total_5d"),
            },
            "hdr_unlock": {
                "hdr_recommended": hdr_new,
                "hdr_current": current_hdr,
                "hdr_delta": round(hdr_new - current_hdr, 4),
                "eta_unlock": self.eta_unlock,
                "hdr_floor": HDR_FLOOR,
                "hdr_ceiling": HDR_CEIL,
            },
            "governor_action": self._determine_action(panic_active, equilibrium, volume_profile, hdr_new),
        }

        return result

    def _determine_action(self, panic_active: bool, equilibrium: dict, volume_profile: dict, hdr_new: float) -> str:
        """Xác định hành động Governor dựa trên trạng thái hiện tại."""
        if not panic_active:
            return "NORMAL_MARKET — Không cần can thiệp."

        if equilibrium.get("equilibrium") and volume_profile.get("volume_converged"):
            return f"UNLOCK_PARTIAL: Giảm HDR từ 1.0 xuống {hdr_new:.2f}. Cho phép giải ngân {(1.0 - hdr_new) * 100:.0f}% NAV."

        if equilibrium.get("equilibrium") and not volume_profile.get("volume_converged"):
            return "HOLD: SDI hội tụ nhưng Volume Profile chưa xác nhận. Chờ thêm khối lượng."

        return "HOLD: Governor giữ nguyên HDR=1.0 (Cash-only). Chờ SDI hội tụ + Volume Profile."


def run_absorption_detection(target_date: str | None = None, show_details: bool = False) -> dict:
    """Wrapper function để gọi từ CLI."""
    detector = AbsorptionDetector()
    result = detector.analyze(target_date)

    if show_details:
        print("\n" + "=" * 60)
        print("  PTCK — ABSORPTION DETECTOR")
        print("  Phát hiện Cân bằng Hấp thụ thị trường")
        print("=" * 60)

        print(f"\n  📅 Ngày: {result['target_date']}")
        print(f"  🚨 Panic active: {'CÓ' if result['panic_active'] else 'KHÔNG'}")
        print(f"  💰 HDR hiện tại: {result['current_hdr']:.2f}")
        print(f"  📊 SDI: {result['pca']['sdi']:.4f}  (ngưỡng panic: {SDI_PANIC_THRESHOLD})")

        eq = result["equilibrium"]
        print("\n  📈 Equilibrium:")
        print(f"     Xác nhận: {'CÓ' if eq['confirmed'] else 'KHÔNG'}")
        print(f"     Lý do: {eq['reason']}")
        print(f"     EMA10(SDI): {eq.get('ema10_sdi', 'N/A')}  |  σ_SDI: {eq.get('std_sdi', 'N/A')}")
        print(f"     Ngưỡng: SDI < {eq.get('threshold', 'N/A')}")

        vp = result["volume_profile"]
        print("\n  📊 Volume Profile:")
        print(f"     POC: {vp.get('poc_price', 'N/A')}  |  VAL: {vp.get('val', 'N/A')}  |  VAH: {vp.get('vah', 'N/A')}")
        print(
            f"     Giá hiện tại: {vp.get('price_current', 'N/A')} {'✅ trong VA' if vp.get('price_in_value_area') else '❌ ngoài VA'}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        )
        print(f"     Volume Ratio: {vp.get('volume_ratio', 0):.2%}  (ngưỡng: {VOLUME_RATIO_THRESHOLD:.0%})")
        print(f"     Hội tụ khối lượng: {'CÓ' if vp.get('volume_converged') else 'CHƯA'}")

        hdr = result["hdr_unlock"]
        print("\n  🔓 HDR Unlock:")
        print(f"     HDR hiện tại: {hdr['hdr_current']:.4f}")
        print(f"     HDR đề xuất:  {hdr['hdr_recommended']:.4f}")
        print(f"     Delta:        {hdr['hdr_delta']:+.4f}")

        print("\n  🎯 Hành động Governor:")
        print(f"     {result['governor_action']}")
        print()

        print("  ⚙️ PCA Loadings (PC1):")
        for k, v in result["pca"]["loadings"].items():
            print(f"     {k}: {v:+.4f}")
        print(f"  Eigenvalues: {[f'{v:.4f}' for v in result['pca']['eigenvalues']]}")
        print(
            f"  PC Variance: PC1={result['pca']['pc1_variance']:.2%}  "
            f"PC2={result['pca']['pc2_variance']:.2%}  "
            f"PC3={result['pca']['pc3_variance']:.2%}"
        )
        print("=" * 60)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PTCK Absorption Detector")
    parser.add_argument("--date", help="Ngày phân tích (YYYY-MM-DD)")
    parser.add_argument("--quiet", action="store_true", help="Chỉ in JSON, không in chi tiết")
    args = parser.parse_args()

    result = run_absorption_detection(target_date=args.date, show_details=not args.quiet)
    if args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
