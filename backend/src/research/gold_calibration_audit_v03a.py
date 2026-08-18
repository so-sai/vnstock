"""gold_calibration_audit_v03a.py — READ-ONLY Calibration & Interval Integrity Audit.

Kiểm tra prediction interval của Gold H20 magnitude forecast (v0.3, checkpoint
af2c8a2) TRƯỚC khi mở v0.3 probabilistic price forecast. KHÔNG sửa model —
μ̂ giữ nguyên, chỉ audit + ước lượng adjustment trên TRAIN, freeze cho OOS.

Mục tiêu (theo contract user):
  - empirical coverage 80% / 90% (Gaussian z=1.28 / 1.645);
  - coverage theo năm;
  - coverage theo regime (trailing realized vol tercile — PIT-safe);
  - standardized residual distribution (mean/std/skew/kurtosis);
  - rolling residual volatility vs model resid_std (giả định homoskedastic?);
  - extreme-move coverage (|y| > q90 của |y| OOS);
  - interval width vs realized volatility (spearman);
  - adjustment: empirical |z| quantile fit TRÊN TRAIN per block, freeze → áp
    OOS, kiểm tra coverage đạt gần nominal KHÔNG tuning theo 2025.

Định lệ: không tuning trên 2025; adjustment phải ước lượng trên training window
(cùng train Ridge) và freeze cho OOS.

Usage (từ project root):
  python -X utf8 backend/src/research/gold_calibration_audit_v03a.py
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

from src.research.gold_forecast_engine_v01 import M1_FEATURES, MIN_TRAIN, WALK_FORWARD_BLOCK
from src.research.gold_forecast_engine_v02 import CB_PRIMARY, load_v02_panel
from src.research.gold_magnitude_forecast_v03 import _spearman, add_log_return_target

H = 20
Z_GAUSS = {0.80: 1.2816, 0.90: 1.6449}
P_LEVELS = (0.80, 0.90)
EXTREME_Q = 0.90
OUT_CSV = DATA_DIR / "reports" / "gold_calibration_audit.csv"


def walk_forward_empirical_z(
    panel: pd.DataFrame, features: list[str], h: int = 20, p_levels: tuple[float, ...] = P_LEVELS
) -> pd.DataFrame:
    """Giống walk_forward_regression (v0.3) + thêm empirical |z| quantile frozen.

    Per block: fit Ridge trên train expanding; resid_std từ train residual;
    z_emp[p] = quantile(|train standardized residual|, p) → freeze, áp OOS.
    KHÔNG dùng bất kỳ OOS/2025 data nào để ước lượng.
    """
    from sklearn.linear_model import Ridge

    y = panel[f"logR{h}"]
    X = panel[features].astype(float)
    out = pd.DataFrame(index=panel.index)
    out["y"] = y
    out["pred"] = np.nan
    out["resid_std"] = np.nan
    for p in p_levels:
        out[f"z_emp_{int(p * 100)}"] = np.nan

    n = len(panel)
    start = MIN_TRAIN
    while start < n - h:
        end = min(start + WALK_FORWARD_BLOCK, n - h)
        X_tr = X.iloc[:start].dropna()
        y_tr = y.reindex(X_tr.index)
        mask_tr = y_tr.notna() & X_tr.notna().all(axis=1)
        X_tr = X_tr[mask_tr]
        y_tr = y_tr[mask_tr]
        if X_tr.shape[0] < 100:
            start = end
            continue
        clf = Ridge(alpha=1.0)
        clf.fit(X_tr, y_tr)
        resid = y_tr.values - clf.predict(X_tr.values)
        resid_std = float(np.std(resid))
        z_tr = np.abs(resid / resid_std) if resid_std > 0 else np.zeros_like(resid)
        z_emps = {p: float(np.quantile(z_tr, p)) for p in p_levels}
        X_te = X.iloc[start:end]
        mask_te = X_te.notna().all(axis=1)
        if mask_te.any():
            pred = clf.predict(X_te[mask_te])
            idx_te = X_te[mask_te].index
            out.loc[idx_te, "pred"] = pred
            out.loc[idx_te, "resid_std"] = resid_std
            for p in p_levels:
                out.loc[idx_te, f"z_emp_{int(p * 100)}"] = z_emps[p]
        start = end
    return out


def standardized_residuals(df: pd.DataFrame) -> pd.Series:
    """z = (y - pred) / resid_std trên OOS rows đầy đủ."""
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    return (sub["y"] - sub["pred"]) / sub["resid_std"].clip(lower=1e-12)


def empirical_coverage(df: pd.DataFrame, z_level: float) -> float:
    """Coverage của interval [pred ± z*resid_std] trên OOS đầy đủ."""
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    if len(sub) < 2:
        return np.nan
    z = standardized_residuals(sub).abs()
    return float(np.mean(z <= z_level))


def coverage_by_year(df: pd.DataFrame, z_level: float) -> dict:
    out = {}
    for year in sorted(set(df.index.year)):
        sub = df[df.index.year == year].dropna(subset=["y", "pred", "resid_std"])
        if len(sub) < 3:
            continue
        z = (sub["y"] - sub["pred"]).abs() / sub["resid_std"]
        out[str(year)] = float(np.mean(z <= z_level))
    return out


def trailing_vol_regime(panel: pd.DataFrame, h: int = 20, n_q: int = 3) -> pd.Series:
    """Regime theo trailing realized vol 20d (PIT-safe: chỉ dùng r tới t)."""
    r = panel["GOLD"].pct_change()
    vol = r.rolling(h).std()
    labels = [f"Q{i + 1}" for i in range(n_q)]
    return pd.qcut(vol, q=n_q, labels=labels, duplicates="drop").astype(object)


def coverage_by_regime(df: pd.DataFrame, panel: pd.DataFrame, z_level: float) -> dict:
    regime = trailing_vol_regime(panel, H)
    out = {}
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    z = (sub["y"] - sub["pred"]).abs() / sub["resid_std"]
    r_align = regime.reindex(sub.index)
    for label in sorted(r_align.dropna().unique()):
        m = r_align == label
        if m.sum() < 3:
            continue
        out[str(label)] = float(np.mean(z[m] <= z_level))
    return out


def standardized_resid_stats(df: pd.DataFrame) -> dict:
    z = standardized_residuals(df).values
    if len(z) < 2:
        return {"n": 0, "mean": np.nan, "std": np.nan, "skew": np.nan, "kurtosis": np.nan}
    from scipy.stats import kurtosis, skew

    return {
        "n": len(z),
        "mean": float(np.mean(z)),
        "std": float(np.std(z)),
        "skew": float(skew(z)),
        "kurtosis": float(kurtosis(z, fisher=True)),
    }


def raw_residual_stats(df: pd.DataFrame) -> dict:
    sub = df.dropna(subset=["y", "pred"])
    r = (sub["y"] - sub["pred"]).values
    if len(r) < 2:
        return {"n": 0, "skew": np.nan, "kurtosis": np.nan, "min": np.nan, "max": np.nan}
    from scipy.stats import kurtosis, skew

    return {
        "n": len(r),
        "skew": float(skew(r)),
        "kurtosis": float(kurtosis(r, fisher=True)),
        "min": float(np.min(r)),
        "max": float(np.max(r)),
    }


def rolling_residual_vol(df: pd.DataFrame, window: int = 20) -> dict:
    """Rolling std của residual OOS vs model resid_std (homoskedastic?)."""
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    if len(sub) < window + 2:
        return {
            "ratio_mean": np.nan,
            "ratio_std": np.nan,
            "resid_vol_std": np.nan,
            "model_std_std": np.nan,
            "corr_absresid_std": np.nan,
        }
    resid = (sub["y"] - sub["pred"]).values
    rv = pd.Series(resid).rolling(window).std().dropna()
    ratio = rv.values / sub["resid_std"].values[window - 1 :]
    corr = _spearman(np.abs(resid), sub["resid_std"].values)
    return {
        "ratio_mean": float(np.mean(ratio)),
        "ratio_std": float(np.std(ratio)),
        "resid_vol_std": float(np.std(resid)),
        "model_std_std": float(np.std(sub["resid_std"].values)),
        "corr_absresid_std": corr,
    }


def extreme_move_coverage(df: pd.DataFrame, z_level: float, q: float = EXTREME_Q) -> dict:
    """Coverage chỉ trên các extreme move (|y| > q của |y| OOS)."""
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    if len(sub) < 5:
        return {"n": 0, "coverage": np.nan, "mean_abs_y": np.nan}
    thr = sub["y"].abs().quantile(q)
    ex = sub[sub["y"].abs() > thr]
    if len(ex) < 2:
        return {"n": 0, "coverage": np.nan, "mean_abs_y": float(thr)}
    z = (ex["y"] - ex["pred"]).abs() / ex["resid_std"]
    return {"n": len(ex), "coverage": float(np.mean(z <= z_level)), "mean_abs_y": float(thr)}


def width_vs_realized_vol(df: pd.DataFrame, z_level: float) -> float:
    """Spearman giữa interval width (2*z*resid_std) và |y| thực tế."""
    sub = df.dropna(subset=["y", "pred", "resid_std"])
    width = 2 * z_level * sub["resid_std"].values
    return _spearman(width, np.abs(sub["y"].values))


def audit(df: pd.DataFrame, panel: pd.DataFrame) -> dict:
    out = {}
    for label, z in Z_GAUSS.items():
        out[f"coverage_{int(label * 100)}"] = empirical_coverage(df, z)
    out["cov_by_year_80"] = coverage_by_year(df, Z_GAUSS[0.80])
    out["cov_by_regime_80"] = coverage_by_regime(df, panel, Z_GAUSS[0.80])
    out.update(standardized_resid_stats(df))
    out["raw"] = raw_residual_stats(df)
    out["rolling"] = rolling_residual_vol(df)
    out["extreme_80"] = extreme_move_coverage(df, Z_GAUSS[0.80])
    out["extreme_90"] = extreme_move_coverage(df, Z_GAUSS[0.90])
    out["width_vs_y_spearman"] = width_vs_realized_vol(df, Z_GAUSS[0.80])
    return out


def frozen_adjustment_check(df: pd.DataFrame) -> dict:
    """Coverage OOS khi dùng z_emp frozen (train-only) vs Gaussian z."""
    out = {}
    sub = df.dropna(subset=["y", "pred", "resid_std", "z_emp_80", "z_emp_90"])
    if len(sub) < 2:
        return out
    z = (sub["y"] - sub["pred"]).abs() / sub["resid_std"]
    out["n"] = len(sub)
    for p in P_LEVELS:
        key = f"z_emp_{int(p * 100)}"
        out[f"coverage_empirical_{int(p * 100)}"] = float(np.mean(z <= sub[key].values))
        out[f"z_emp_mean_{int(p * 100)}"] = float(np.mean(sub[key].values))
    return out


def main() -> None:
    print("=" * 112)
    print("  GOLD H20 CALIBRATION AUDIT (v0.3A) — Interval Integrity, READ-ONLY")
    print("=" * 112)

    panel = load_v02_panel()
    panel = add_log_return_target(panel, H)
    reg = walk_forward_empirical_z(panel, M1_FEATURES + [CB_PRIMARY], H)
    print(f"  Panel: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")

    res = audit(reg, panel)
    print("\n  [1] EMPIRICAL COVERAGE (Gaussian z):")
    print(f"      nominal 80% (z=1.2816): {res['coverage_80']:.3f}  (gap {res['coverage_80'] - 0.80:+.3f})")
    print(f"      nominal 90% (z=1.6449): {res['coverage_90']:.3f}  (gap {res['coverage_90'] - 0.90:+.3f})")

    print("\n  [2] COVERAGE 80% BY YEAR:")
    for k, v in sorted(res["cov_by_year_80"].items()):
        print(f"      {k}: {v:.3f}")

    print("\n  [3] COVERAGE 80% BY REGIME (trailing vol tercile):")
    for k, v in sorted(res["cov_by_regime_80"].items()):
        print(f"      {k}: {v:.3f}")

    st = res
    print("\n  [4] STANDARDIZED RESIDUAL DISTRIBUTION (z=(y-pred)/resid_std):")
    print(f"      n={st['n']} mean={st['mean']:.3f} std={st['std']:.3f} skew={st['skew']:.3f} kurtosis={st['kurtosis']:.3f}")
    print("      (kỳ vọng N(0,1): std~1, skew~0, kurtosis~0)")

    raw = res["raw"]
    print(
        f"  [5] RAW RESIDUAL: n={raw['n']} skew={raw['skew']:.3f} kurtosis={raw['kurtosis']:.3f} "
        f"min={raw['min']:.4f} max={raw['max']:.4f}"
    )

    rv = res["rolling"]
    print("\n  [6] ROLLING RESIDUAL VOLATILITY (window=20) vs model resid_std:")
    print(
        f"      ratio_mean={rv['ratio_mean']:.3f} ratio_std={rv['ratio_std']:.3f} "
        f"resid_vol_std={rv['resid_vol_std']:.4f} model_std_std={rv['model_std_std']:.4f} "
        f"corr(|resid|,resid_std)={rv['corr_absresid_std']:.3f}"
    )

    for k in ("extreme_80", "extreme_90"):
        e = res[k]
        print(
            f"  [7] EXTREME-MOVE COVERAGE ({k.split('_')[-1]}%, |y|>q90): n={e['n']} "
            f"coverage={e['coverage']:.3f} thr_abs_y={e['mean_abs_y']:.4f}"
        )

    print(f"  [8] INTERVAL WIDTH vs REALIZED |y| (spearman): {res['width_vs_y_spearman']:.3f}")

    fz = frozen_adjustment_check(reg)
    print("\n  [9] FROZEN ADJUSTMENT (empirical |z| quantile từ TRAIN per block, áp OOS):")
    if fz:
        for p in P_LEVELS:
            k = int(p * 100)
            print(
                f"      nominal {k}%: empirical coverage={fz[f'coverage_empirical_{k}']:.3f} "
                f"(Gaussian {res[f'coverage_{k}']:.3f})  z_emp_mean={fz[f'z_emp_mean_{k}']:.3f}"
            )
    else:
        print("      (không đủ rows)")

    # ── Ghi CSV ──
    row = {
        "coverage_80_gauss": res["coverage_80"],
        "coverage_90_gauss": res["coverage_90"],
        "z_mean": st["mean"],
        "z_std": st["std"],
        "z_skew": st["skew"],
        "z_kurtosis": st["kurtosis"],
        "raw_skew": raw["skew"],
        "raw_kurtosis": raw["kurtosis"],
        "roll_ratio_mean": rv["ratio_mean"],
        "roll_corr_absresid_std": rv["corr_absresid_std"],
        "extreme80_cov": res["extreme_80"]["coverage"],
        "extreme90_cov": res["extreme_90"]["coverage"],
        "width_vs_y_spearman": res["width_vs_y_spearman"],
    }
    if fz:
        for p in P_LEVELS:
            k = int(p * 100)
            row[f"coverage_empirical_{k}"] = fz[f"coverage_empirical_{k}"]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")

    print("\n" + "=" * 112)
    print("  AUDIT KẾT LUẬN (đánh giá, quyết định cuối ở note):")
    print("  - Coverage Gaussian dưới nominal → residual heavy tails / interval hẹp.")
    print("  - z_emp (train-only frozen) có đưa coverage về gần nominal không?")
    print("  - Nếu kurtosis > 0 rõ → chọn empirical quantile / Student-t / conformal.")
    print("  - KHÔNG tuning theo 2025.")
    print("=" * 112)


if __name__ == "__main__":
    main()
