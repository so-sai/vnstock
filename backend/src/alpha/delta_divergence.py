"""
delta_divergence.py — Delta Divergence Index (DDI)

Δ_SA = dS/dt - α · AC_latency

Đo độ lệch giữa tốc độ stress (dS/dt) và năng lực hấp thụ (AC).
  Δ_SA > 0 kéo dài → stress vượt adaptation → healing illusion
  Δ_SA < 0          → hệ thống đang hấp thụ stress hiệu quả
"""

import sys
from pathlib import Path
from typing import Optional

import numpy as np


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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path


PROJECT_ROOT = _hydrate_path()

# ── α mặc định cho từng regime ──
ALPHA_DEFAULT = {
    "TRENDING": 0.30,
    "RANGING":  0.50,
    "CRISIS":   0.70,
}

# ── Cấu trúc weight cho forward-return Sortino ──
WEIGHT_EPOCH = {
    "2008": 0.20,
    "2011": 0.15,
    "2015": 0.10,
    "2020": 0.25,
    "2022": 0.15,
    "2023": 0.15,
}


def _canonical_alpha(params: dict) -> dict:
    """Đồng nhất α về round(,4) để deterministic hash."""
    return {k: round(v, 4) for k, v in params.items()}


class DeltaDivergenceIndex:
    """Tính Δ_SA từ snapshot hiện tại."""

    ALPHA_DEFAULT = ALPHA_DEFAULT

    def calculate(self, snapshot: dict, alpha: Optional[dict] = None) -> dict:
        r = snapshot.get("regime", {})
        c = snapshot.get("cau_truc", {})
        ew = snapshot.get("canh_bao_som", {})

        regime_status = r.get("trang_thai", "RANGING")
        alpha_regime = (alpha or self.ALPHA_DEFAULT).get(regime_status, 0.5)

        # dS/dt: proxy từ entropy change hoặc early warning score
        entropy = c.get("entropy")
        dS_dt = 0.0
        if entropy is not None:
            dS_dt = min(1.0, max(0.0, (entropy - 1.0) / 3.0))
        ew_diem = ew.get("diem", 0)
        ew_factor = min(1.0, ew_diem / 30.0)
        dS_dt = max(dS_dt, ew_factor)

        # AC_latency: proxy từ ATR ratio + breadth stability
        atr_ratio = r.get("ty_le_atr", 1.0)
        breadth_pct = r.get("do_rong")
        if breadth_pct is not None:
            ac_latency = max(0.1, atr_ratio * (1.0 - min(1.0, (breadth_pct / 100.0))))
        else:
            ac_latency = max(0.1, atr_ratio)

        delta_sa = dS_dt - alpha_regime * ac_latency
        # Ép về bool Python thuần (defense-in-depth): phép so sánh numpy trả về
        # np.bool_ → json.dumps ném TypeError, làm sập chu trình EOD tự động.
        is_healing_illusion = bool(delta_sa > 0.0 and dS_dt > 0.3)

        return {
            "delta_sa": round(delta_sa, 4),
            "dS_dt": round(dS_dt, 4),
            "ac_latency": round(ac_latency, 4),
            "alpha_regime": alpha_regime,
            "regime": regime_status,
            "healing_illusion": is_healing_illusion,
            "action_filter": "caution" if delta_sa > 0.1 else ("block" if delta_sa > 0.3 else "pass"),
        }


class AlphaOptimizer:
    """Tối ưu α theo regime dùng Sortino ratio + walk-forward validation.

    LAS (Layer Adaptive Search) — thay thế Bayesian khi không có scikit-optimize.
    """

    LAMBDA_REG = 0.2

    def optimize(self, regime_label: str, alpha_prev: float,
                 historical_returns: np.ndarray,
                 historical_stress: np.ndarray,
                 n_splits: int = 5) -> dict:
        from scipy.optimize import minimize_scalar as _minimize
        from sklearn.model_selection import TimeSeriesSplit

        tscv = TimeSeriesSplit(n_splits=n_splits)

        def _sortino(returns: np.ndarray) -> float:
            rf = 0.0
            excess = returns - rf
            downside = excess[excess < 0]
            sigma_d = np.std(downside) if len(downside) > 1 else 1e-6
            return float(np.mean(excess) / sigma_d) if sigma_d > 0 else 0.0

        def _objective(alpha: float) -> float:
            # Filter signal: Δ_SA = stress_t - alpha * (1 - stress_t)
            #   stress_t là historical_stress normalized
            signal = historical_stress - alpha * (1.0 - historical_stress)
            active = signal <= 0.0  # pass filter khi Δ_SA <= 0
            filtered_returns = historical_returns.copy()
            filtered_returns[~active] = 0.0

            sortino = _sortino(filtered_returns)
            penalty = self.LAMBDA_REG * (alpha - alpha_prev) ** 2
            return -(sortino - penalty)

        # Grid coarse → fine refinement
        best = None
        best_val = float("inf")
        for lo, hi in [(0.05, 0.95)]:
            res = _minimize(_objective, bounds=(lo, hi), method="bounded",
                                  options={"xatol": 0.005, "maxiter": 50})
            if res.fun < best_val:
                best_val = res.fun
                best = res.x

        alpha_opt = round(float(best), 4) if best is not None else alpha_prev

        # Walk-forward cross-val
        fold_scores = []
        for train_idx, val_idx in tscv.split(historical_returns):
            train_r, val_r = historical_returns[train_idx], historical_returns[val_idx]
            train_s, val_s = historical_stress[train_idx], historical_stress[val_idx]

            sig_train = train_s - alpha_opt * (1.0 - train_s)
            sig_val = val_s - alpha_opt * (1.0 - val_s)
            active_train, active_val = sig_train <= 0.0, sig_val <= 0.0

            r_train = train_r.copy()
            r_train[~active_train] = 0.0
            r_val = val_r.copy()
            r_val[~active_val] = 0.0

            fold_scores.append({
                "train_sortino": round(_sortino(r_train), 4),
                "val_sortino": round(_sortino(r_val), 4),
            })

        return {
            "regime": regime_label,
            "alpha_optimized": alpha_opt,
            "alpha_previous": round(alpha_prev, 4),
            "sortino_prev": round(_sortino(historical_returns), 4),
            "sortino_optimized": round(_sortino(historical_returns * (
                (historical_stress - alpha_opt * (1.0 - historical_stress)) <= 0.0
            ).astype(float)), 4),
            "walk_forward": fold_scores,
            "n_splits": n_splits,
        }


def build_params_registry() -> dict:
    """Thu thập tất cả tham số vận hành thành 1 dict để hash."""
    from src.engine.confidence_layer import TRỌNG_SỐ as CS

    registry = {
        "regime_weights": {
            "b_score": 0.5,
            "t_score": 0.3,
            "v_score": 0.2,
            "breadth_suspended": {"t_score": 0.6, "v_score": 0.4},
        },
        "confidence_weights": dict(CS),
        "ddi_alpha": dict(ALPHA_DEFAULT),
        "alpha_optimizer": {
            "lambda_reg": AlphaOptimizer.LAMBDA_REG,
        },
    }
    return registry
