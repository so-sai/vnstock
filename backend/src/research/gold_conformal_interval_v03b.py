"""gold_conformal_interval_v03b.py — Phase A-Fix: Locally-Adaptive Conformal PI.

Sửa interval under-coverage (v0.3A: coverage80 0.740, heteroskedasticity,
extreme-move 0.045) bằng kiến trúc 2 tầng:

    Forecast → Dynamic Volatility → Normalized Residual → Frozen Conformal
    Quantile → Prediction Interval

A1 — Dynamic scale (σ_dyn), PIT-safe, chỉ dùng logR20 ĐÃ REALIZED (j <= t-20):
  - sigma20:  rolling std của logR20 (window=20, lag=20)  → PRIMARY (a priori)
  - ewma20:   sqrt(EWMA(span=20) của logR20^2)            → descriptive robustness
  KHÔNG chọn method theo kết quả (cấm model-selection contamination).

A2 — Locally-Adaptive Conformal:
  - calibration set: OOS rows có date <= 2024-12-31 (IS, walk-forward);
  - score s_t = |y_t - mu_hat_t| / sigma_dyn,t trên calibration;
  - q_alpha = empirical quantile của s trên calibration → FROZEN;
  - interval 2025+ (pure OOS, KHÔNG dùng để chọn q):
        I_t = mu_hat_t ± q_alpha * sigma_dyn,t

GATE (cứng):
  1. coverage80 trên 2025+ trong [0.75, 0.85];
  2. coverage90 trên 2025+ trong [0.86, 0.94];
  3. midpoint MAE không xấu đi (vs naive trên cùng 2025+ subset);
  4. coverage không chỉ nhờ vài extreme moves (loại top/bottom 1%, 5% vẫn trong
     ngưỡng rộng [0.65, 0.95]);
  5. coverage theo từng vol regime không sụp (>=0.60 cho 80%);
  6. calibration frozen trước 2025 (max(cal.date) <= 2024-12-31).

No tuning theo 2025. READ-ONLY (không sửa checkpoint v0.2/v0.3/v0.3A).

Usage (từ project root):
  python -X utf8 backend/src/research/gold_conformal_interval_v03b.py
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

from src.research.gold_forecast_engine_v01 import M1_FEATURES
from src.research.gold_forecast_engine_v02 import CB_PRIMARY, load_v02_panel
from src.research.gold_magnitude_forecast_v03 import (
    H,
    add_log_return_target,
    evaluate_magnitude,
    walk_forward_regression,
)

CALIB_CUTOFF = "2024-12-31"
WINDOW = 20
SPAN = 20
LEVELS = (0.80, 0.90)
GATE_80 = (0.75, 0.85)
GATE_90 = (0.86, 0.94)
GATE_ROBUST = (0.65, 0.95)
REGIME_MIN_80 = 0.60
OUT_CSV = DATA_DIR / "reports" / "gold_conformal_interval_v03b.csv"


def sigma20_rolling(panel: pd.DataFrame, h: int = 20, window: int = WINDOW) -> pd.Series:
    """σ_dyn = rolling std của logR_h đã realized (lag=h, window). PIT-safe."""
    s = panel[f"logR{h}"]
    return s.shift(h).rolling(window).std()


def ewma20_vol(panel: pd.DataFrame, h: int = 20, span: int = SPAN) -> pd.Series:
    """σ_dyn = sqrt(EWMA(span) của logR_h^2 đã realized). PIT-safe."""
    s = panel[f"logR{h}"].shift(h)
    var = (s**2).ewm(span=span, adjust=False).mean()
    return var.pow(0.5)


def conformal_scores(df: pd.DataFrame, sigma_dyn: pd.Series) -> pd.Series:
    """s = |y - mu_hat| / sigma_dyn trên rows đầy đủ (sigma_dyn > 0)."""
    sub = df.dropna(subset=["y", "pred"])
    sig = sigma_dyn.reindex(sub.index)
    ok = sig.fillna(0) > 0
    sub = sub[ok]
    sig = sig[ok]
    return ((sub["y"] - sub["pred"]).abs() / sig.clip(lower=1e-12)).rename("score")


def frozen_quantiles(scores: pd.Series, levels: tuple[float, ...] = LEVELS) -> dict:
    """Empirical quantile của calibration scores → FROZEN."""
    return {p: float(np.quantile(scores.values, p)) for p in levels}


def interval_coverage(df: pd.DataFrame, sigma_dyn: pd.Series, q: float) -> float:
    """Coverage của I = mu_hat ± q*sigma_dyn."""
    sub = df.dropna(subset=["y", "pred"])
    sig = sigma_dyn.reindex(sub.index)
    ok = sig.fillna(0) > 0
    sub = sub[ok]
    sig = sig[ok]
    if len(sub) == 0:
        return np.nan
    z = (sub["y"] - sub["pred"]).abs() / sig.clip(lower=1e-12)
    return float(np.mean(z <= q))


def coverage_by_year(df: pd.DataFrame, sigma_dyn: pd.Series, q: float) -> dict:
    out = {}
    for year in sorted(set(df.index.year)):
        sub = df[df.index.year == year]
        if len(sub) < 3:
            continue
        sig = sigma_dyn.reindex(sub.index)
        ok = sig.fillna(0) > 0
        if ok.sum() < 3:
            continue
        z = (sub["y"] - sub["pred"]).abs().loc[ok] / sig[ok].clip(lower=1e-12)
        out[str(year)] = float(np.mean(z <= q))
    return out


def coverage_by_regime(df: pd.DataFrame, panel: pd.DataFrame, sigma_dyn: pd.Series, q: float) -> dict:
    from src.research.gold_calibration_audit_v03a import trailing_vol_regime

    regime = trailing_vol_regime(panel, H)
    out = {}
    sub = df.dropna(subset=["y", "pred"])
    sig = sigma_dyn.reindex(sub.index)
    ok = sig.fillna(0) > 0
    sub, sig = sub[ok], sig[ok]
    z = (sub["y"] - sub["pred"]).abs() / sig.clip(lower=1e-12)
    r_align = regime.reindex(sub.index)
    for label in sorted(r_align.dropna().unique()):
        m = r_align == label
        if m.sum() < 3:
            continue
        out[str(label)] = float(np.mean(z[m] <= q))
    return out


def robustness_extreme(df: pd.DataFrame, sigma_dyn: pd.Series, q: float, drop_frac: float) -> dict:
    """Loại top/bottom drop_frac |y| cực trị rồi tính coverage."""
    sub = df.dropna(subset=["y", "pred"])
    sig = sigma_dyn.reindex(sub.index)
    ok = sig.fillna(0) > 0
    sub, sig = sub[ok], sig[ok]
    if len(sub) == 0:
        return {"n": 0, "coverage": np.nan}
    thr = sub["y"].abs().quantile(1 - drop_frac)
    keep = sub[sub["y"].abs() <= thr]
    z = (keep["y"] - keep["pred"]).abs() / sig[keep.index].clip(lower=1e-12)
    return {"n": len(keep), "coverage": float(np.mean(z <= q))}


def build_v03b_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Gộp pred (v0.3 walk-forward), y, và cả 2 σ_dyn method."""
    reg = walk_forward_regression(panel, M1_FEATURES + [CB_PRIMARY], H)
    df = pd.DataFrame(index=panel.index)
    df["logR20"] = panel[f"logR{H}"]
    df["y"] = reg["y"]
    df["pred"] = reg["pred"]
    df["sigma20"] = sigma20_rolling(panel, H)
    df["ewma20"] = ewma20_vol(panel, H)
    return df


def gate_eval(df: pd.DataFrame, panel: pd.DataFrame, method: str) -> dict:
    sig = df[method]
    cal = df[df.index <= CALIB_CUTOFF]
    test = df[df.index > CALIB_CUTOFF]
    scores = conformal_scores(cal, sig)
    qs = frozen_quantiles(scores, LEVELS)
    out = {"method": method, "n_cal": int(len(scores)), "n_test": int(len(test.dropna(subset=["y", "pred"])))}
    out["max_cal_date"] = str(cal.index.max().date())
    for p in LEVELS:
        k = int(p * 100)
        q = qs[p]
        out[f"q_{k}"] = q
        out[f"coverage_{k}_cal"] = interval_coverage(cal, sig, q)
        out[f"coverage_{k}_test"] = interval_coverage(test, sig, q)
    # midpoint MAE trên test vs naive
    ev_m = evaluate_magnitude(test, "pred", H)
    test_n = test.copy()
    test_n["pred_naive"] = 0.0
    ev_n = evaluate_magnitude(test_n, "pred_naive", H)
    out["mid_mae_test"] = ev_m["mae"]
    out["naive_mae_test"] = ev_n["mae"]
    # robustness loại extreme
    out["robust_1pct"] = robustness_extreme(test, sig, qs[LEVELS[0]], 0.01)["coverage"]
    out["robust_5pct"] = robustness_extreme(test, sig, qs[LEVELS[0]], 0.05)["coverage"]
    # coverage theo regime (80%) trên test
    cr = coverage_by_regime(test, panel, sig, qs[LEVELS[0]])
    out["regime_80"] = cr
    out["year_80"] = coverage_by_year(test, sig, qs[LEVELS[0]])
    return out


def main() -> None:
    print("=" * 112)
    print("  GOLD H20 CONFORMAL PI (v0.3B) — Locally-Adaptive Conformal + Dynamic Vol")
    print("=" * 112)

    panel = load_v02_panel()
    panel = add_log_return_target(panel, H)
    df = build_v03b_frame(panel)
    print(f"  Panel: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")

    # PRIMARY a priori: sigma20 (không chọn theo kết quả)
    for method in ("sigma20", "ewma20"):
        tag = "PRIMARY (a priori)" if method == "sigma20" else "descriptive robustness"
        print(f"\n  ── {method} [{tag}] ──")
        g = gate_eval(df, panel, method)
        print(f"  calibration n={g['n_cal']} (max date {g['max_cal_date']}) | test n={g['n_test']}")
        for p in LEVELS:
            k = int(p * 100)
            print(
                f"  q_{k}={g[f'q_{k}']:.3f}  coverage_{k}: cal={g[f'coverage_{k}_cal']:.3f} test={g[f'coverage_{k}_test']:.3f}"
            )
        print(f"  midpoint MAE test={g['mid_mae_test']:.4f} (naive {g['naive_mae_test']:.4f})")
        print(f"  robustness80 (loại 1%: {g['robust_1pct']:.3f}, 5%: {g['robust_5pct']:.3f})")
        print("  regime80: " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["regime_80"].items())))
        print("  year80:   " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["year_80"].items())))

    # ── GATE (dựa trên PRIMARY sigma20) ──
    print("\n" + "=" * 112)
    print("  GATE VERDICT (primary = sigma20, frozen calibration <= 2024):")
    g = gate_eval(df, panel, "sigma20")
    c80 = g["coverage_80_test"]
    c90 = g["coverage_90_test"]
    lo80, hi80 = GATE_80
    lo90, hi90 = GATE_90
    chk1 = lo80 <= c80 <= hi80
    chk2 = lo90 <= c90 <= hi90
    chk3 = g["mid_mae_test"] < g["naive_mae_test"]
    chk4 = GATE_ROBUST[0] <= g["robust_1pct"] <= GATE_ROBUST[1] and GATE_ROBUST[0] <= g["robust_5pct"] <= GATE_ROBUST[1]
    chk5 = all(v >= REGIME_MIN_80 for v in g["regime_80"].values()) if g["regime_80"] else False
    chk6 = g["max_cal_date"] <= CALIB_CUTOFF
    print(f"  [{'PASS' if chk1 else 'FAIL'}] coverage80 test {c80:.3f} trong [{lo80}, {hi80}]")
    print(f"  [{'PASS' if chk2 else 'FAIL'}] coverage90 test {c90:.3f} trong [{lo90}, {hi90}]")
    print(f"  [{'PASS' if chk3 else 'FAIL'}] midpoint MAE {g['mid_mae_test']:.4f} < naive {g['naive_mae_test']:.4f}")
    print(f"  [{'PASS' if chk4 else 'FAIL'}] robustness loại extreme 1%={g['robust_1pct']:.3f} 5%={g['robust_5pct']:.3f}")
    print(f"  [{'PASS' if chk5 else 'FAIL'}] regime80 không sụp: {g['regime_80']}")
    print(f"  [{'PASS' if chk6 else 'FAIL'}] calibration frozen <= {CALIB_CUTOFF} (max {g['max_cal_date']})")
    overall = all((chk1, chk2, chk3, chk4, chk5, chk6))
    print(f"\n  KẾT LUẬN: {'CONFORMAL PI PASS' if overall else 'CONFORMAL PI FAIL'}")
    print("  (2025-2026 chỉ là pure OOS validation — không dùng để chọn q.)")

    # ── Save ──
    rows = [gate_eval(df, panel, m) for m in ("sigma20", "ewma20")]
    for r in rows:
        r["regime_80"] = str(r["regime_80"])
        r["year_80"] = str(r["year_80"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")


if __name__ == "__main__":
    main()
