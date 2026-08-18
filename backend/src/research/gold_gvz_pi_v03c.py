"""gold_gvz_pi_v03c.py — READ-ONLY: Prediction Interval voi sigma_dyn = GVZ (frozen).

Sau GVZ Interval Gate PASS (e766a1d): GVZ beat sigma20 lam volatility scaler OOS.
Module nay chuyen PI sang production-form va khoa moi tham so:

    mu_t        = M1 + CB_IFS_z walk-forward  (GIU NGUYEN tu v0.2/v0.3 — guardrail)
    sigma_dyn,t = (GVZ_t / 100) * sqrt(20/252)  (PIT: pub = obs + 1 trading day)
    score s_t   = |y_t - mu_t| / sigma_dyn,t
    q_alpha     = empirical quantile(s) tren calibration <= 2024-12-31 -> FROZEN
    PI_t        = mu_t +/- q_alpha * sigma_dyn,t

GUARDRAILS (khong duoc vi pham):
  1. mu invariant: KHONG retrain/tune nhanh mean; pred = walk_forward_regression
     (M1_FEATURES + CB_PRIMARY) giong het v0.3/v0.3B.
  2. PIT calibration window: chi fit q tren rows <= 2024-12-31 roi freeze.
  3. 2025-2026 CHI descriptive (danh gia), cam can thiep tham so hoi to.
  4. STOP-RULE: neu gate fail -> ket luan "chua du bao duoc uncertainty
     distribution du tot", KHONG tune tiep interval tren lich su, chuyen ngay
     sang Forward Paper Ledger.

GATE (giong v0.3B, ap tren 2025+ pure OOS):
  1. coverage80 test trong [0.75, 0.85];
  2. coverage90 test trong [0.86, 0.94];
  3. midpoint MAE test < naive;
  4. robustness loai extreme (1%/5%) trong [0.65, 0.95];
  5. regime80 khong sup (>= 0.60);
  6. frozen (max(cal date) <= 2024-12-31).

READ-ONLY: khong ghi replay DB / screener / Governor / gold_h2.db.

Usage (tu project root):
  python -X utf8 backend/src/research/gold_gvz_pi_v03c.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Path hydration (Sentinel v2.1 Anchor) ──────────────────────────────────
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "AGENTS.md").exists() and (PROJECT_ROOT / "backend").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
for _p in [str(BACKEND / "src"), str(BACKEND), str(PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

from src.research.gold_conformal_interval_v03b import (
    CALIB_CUTOFF,
    GATE_80,
    GATE_90,
    GATE_ROBUST,
    LEVELS,
    REGIME_MIN_80,
    conformal_scores,
    coverage_by_regime,
    coverage_by_year,
    frozen_quantiles,
    interval_coverage,
    robustness_extreme,
)
from src.research.gold_forecast_engine_v01 import M1_FEATURES
from src.research.gold_forecast_engine_v02 import CB_PRIMARY, load_v02_panel
from src.research.gold_magnitude_forecast_v03 import (
    H,
    add_log_return_target,
    evaluate_magnitude,
    walk_forward_regression,
)
from src.research.gvz_adapter import CACHE_FILE, load_gvz, pit_align, pit_series

OUT_CSV = DATA_DIR / "reports" / "gold_gvz_pi_v03c.csv"

# Scale a priori tu dinh nghia chi so (da dung o gate, khong doi).
GVZ_TO_LOG20 = np.sqrt(20 / 252) / 100.0


def gvz_sigma(panel: pd.DataFrame, df: pd.DataFrame) -> pd.Series:
    """sigma_dyn = (GVZ_PIT/100)*sqrt(20/252), align ffill vao panel index."""
    gvz = pit_series(load_gvz(CACHE_FILE))
    aligned = pit_align(gvz, panel.index)
    return (aligned * GVZ_TO_LOG20).rename("gvz").reindex(df.index)


def build_frame(panel: pd.DataFrame, gvz_series: pd.Series | None = None) -> pd.DataFrame:
    """Frame voi mu invariant (walk-forward v0.2) + sigma_dyn = GVZ.

    gvz_series: cho phep inject series GVZ (test); mac dinh doc tu cache.
    """
    reg = walk_forward_regression(panel, M1_FEATURES + [CB_PRIMARY], H)
    df = pd.DataFrame(index=panel.index)
    df["logR20"] = panel[f"logR{H}"]
    df["y"] = reg["y"]
    df["pred"] = reg["pred"]
    df["gvz"] = gvz_sigma(panel, df) if gvz_series is None else gvz_series.reindex(df.index)
    return df


def frozen_scores(df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame, dict]:
    """Chia calibration (<= cutoff) / test (> cutoff), score tren calibration."""
    cal = df[df.index <= CALIB_CUTOFF]
    test = df[df.index > CALIB_CUTOFF]
    scores = conformal_scores(cal, cal["gvz"])
    qs = frozen_quantiles(scores, LEVELS)
    return scores, test, qs


def pi_halfwidth(df: pd.DataFrame, q: float) -> pd.Series:
    """PI_t = mu_t +/- q*sigma_dyn,t (tra ve nua do rong)."""
    sig = df["gvz"].reindex(df.index).fillna(0)
    return q * sig.clip(lower=1e-12)


def evaluate(df: pd.DataFrame, scores: pd.Series, test: pd.DataFrame, qs: dict, panel: pd.DataFrame) -> dict:
    sig = df["gvz"]
    cal = df[df.index <= CALIB_CUTOFF]
    out = {"n_cal": int(len(scores)), "n_test": int(len(test.dropna(subset=["y", "pred"])))}
    out["max_cal_date"] = str(cal.index.max().date())
    for p in LEVELS:
        k = int(p * 100)
        out[f"q_{k}"] = qs[p]
        out[f"coverage_{k}_cal"] = interval_coverage(cal, sig, qs[p])
        out[f"coverage_{k}_test"] = interval_coverage(test, sig, qs[p])
    if "logR20" in test.columns:
        ev_m = evaluate_magnitude(test, "pred", H)
        test_n = test.copy()
        test_n["pred_naive"] = 0.0
        ev_n = evaluate_magnitude(test_n, "pred_naive", H)
        out["mid_mae_test"] = ev_m["mae"]
        out["naive_mae_test"] = ev_n["mae"]
        out["mid_rmse_test"] = ev_m["rmse"]
        out["naive_rmse_test"] = ev_n["rmse"]
    else:
        m = test.dropna(subset=["y", "pred"])
        out["mid_mae_test"] = float((m["y"] - m["pred"]).abs().mean())
        out["mid_rmse_test"] = float(np.sqrt(((m["y"] - m["pred"]) ** 2).mean()))
        out["naive_mae_test"] = float(m["y"].abs().mean())
        out["naive_rmse_test"] = float(np.sqrt((m["y"] ** 2).mean()))
    out["robust_1pct"] = robustness_extreme(test, sig, qs[LEVELS[0]], 0.01)["coverage"]
    out["robust_5pct"] = robustness_extreme(test, sig, qs[LEVELS[0]], 0.05)["coverage"]
    out["regime_80"] = coverage_by_regime(test, panel, sig, qs[LEVELS[0]])
    out["year_80"] = coverage_by_year(test, sig, qs[LEVELS[0]])
    return out


def check_row(g: dict) -> dict:
    c80 = g["coverage_80_test"]
    c90 = g["coverage_90_test"]
    lo80, hi80 = GATE_80
    lo90, hi90 = GATE_90
    return {
        "coverage80": lo80 <= c80 <= hi80,
        "coverage90": lo90 <= c90 <= hi90,
        "midpoint_mae": g["mid_mae_test"] < g["naive_mae_test"],
        "robust": GATE_ROBUST[0] <= g["robust_1pct"] <= GATE_ROBUST[1]
        and GATE_ROBUST[0] <= g["robust_5pct"] <= GATE_ROBUST[1],
        "regime80": all(v >= REGIME_MIN_80 for v in g["regime_80"].values()) if g["regime_80"] else False,
        "frozen": g["max_cal_date"] <= CALIB_CUTOFF,
    }


def main() -> None:
    print("=" * 112)
    print("  GOLD H20 PREDICTION INTERVAL v0.3C - sigma_dyn = GVZ (frozen calibration)")
    print("=" * 112)

    panel = load_v02_panel()
    panel = add_log_return_target(panel, H)
    df = build_frame(panel)
    print(f"  Panel: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngay)")
    print("  mu invariant: M1 + CB_IFS_z walk-forward (KHONG retrain)")
    print(f"  sigma_dyn = (GVZ/100)*sqrt(20/252), scale={GVZ_TO_LOG20:.6f}, PIT pub=obs+1")

    scores, test, qs = frozen_scores(df)
    g = evaluate(df, scores, test, qs, panel)
    print(f"  calibration n={g['n_cal']} (max {g['max_cal_date']}) | test n={g['n_test']}")
    for p in LEVELS:
        k = int(p * 100)
        print(f"  q_{k}={g[f'q_{k}']:.3f}  cov: cal={g[f'coverage_{k}_cal']:.3f} test={g[f'coverage_{k}_test']:.3f}")
    print(
        f"  midpoint MAE={g['mid_mae_test']:.4f} (naive {g['naive_mae_test']:.4f}) "
        f"RMSE={g['mid_rmse_test']:.4f} (naive {g['naive_rmse_test']:.4f})"
    )
    print(f"  robust80 loai 1%: {g['robust_1pct']:.3f}, 5%: {g['robust_5pct']:.3f}")
    print("  regime80: " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["regime_80"].items())))
    print("  year80:   " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["year_80"].items())))

    # ── Gate verdict + stop-rule ──
    print("\n" + "=" * 112)
    chk = check_row(g)
    for k, v in chk.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    overall = all(chk.values())
    print(f"\n  KET LUAN: {'GVZ PI v0.3C CALIBRATION PASS' if overall else 'GVZ PI v0.3C CALIBRATION FAIL'}")
    if overall:
        print("  -> freeze q80/q90. Buoc tiep theo: Forward Paper Gate voi PI GVZ.")
    else:
        print("  STOP-RULE: khong tune tiep interval tren lich su.")
        print("  -> Giu ket luan: du bao duoc expected return/magnitude, chua du bao")
        print("     duoc uncertainty distribution du tot. Chuyen Forward Paper Ledger.")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    row = dict(g)
    row["regime_80"] = str(row["regime_80"])
    row["year_80"] = str(row["year_80"])
    pd.DataFrame([row]).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")


if __name__ == "__main__":
    main()
