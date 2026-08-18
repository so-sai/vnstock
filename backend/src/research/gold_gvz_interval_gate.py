"""gold_gvz_interval_gate.py — READ-ONLY Gate: GVZ như σ_dyn cho Conformal PI.

Bước 2 theo quyết định user (không mở ETF, không mở M3 song song): kiểm tra GVZ
có cải thiện **OOS interval coverage** sau v0.3B FAIL (coverage80 0.735 / Q2 0.581,
dynamic vol 20d lagged không theo kịp regime vol shift) hay không.

    v0.3B FAIL → GVZ feature → Interval Gate → Forward Paper Gate → sizing

4 cấu hình σ_dyn (a priori, KHÔNG chọn theo kết quả; σ20 là PRIMARY baseline):
  1. sigma20     — rolling std logR20 (lag 20)        [baseline v0.3B]
  2. ewma20      — sqrt(EWMA20 logR20²)               [descriptive]
  3. gvz         — implied vol (GVZ) scaled -> logR20  [candidate]
  4. sigma20+gvz — sqrt(sigma20² + gvz²)              [candidate combine]

GVZ: PIT-safe via gvz_adapter (pub = obs + 1 trading day, align ffill vào panel).
Scale từ định nghĩa chỉ số (a priori, không fit): GVZ ≈ annualized 30d implied
vol % → sigma_gvz = (GVZ/100) * sqrt(20/252)  (đơn vị 20-day log-return vol).

Conformal: calibration = OOS rows <= 2024-12-31 FROZEN; test = 2025+ pure OOS.
Interval I_t = mu_hat ± q*sigma_dyn.

GATE:
  c1. coverage80 test trong [0.75, 0.85];
  c2. coverage90 test trong [0.86, 0.94];
  c3. midpoint MAE test < naive (không xấu đi);
  c4. robustness loại extreme (1%/5%) trong [0.65, 0.95];
  c5. regime80 không sụp (>=0.60);
  c6. calibration frozen <= 2024-12-31.
VERDICT: PASS nếu GVZ (3) hoặc σ20+GVZ (4) đạt c1..c6 và coverage-gap (|cov - target|)
không xấu hơn sigma20 (1). Nếu không cải thiện OOS → đóng FAIL (như v0.3B).

No tuning theo 2025-2026 (chỉ descriptive/pure OOS). READ-ONLY.

Usage (từ project root):
  python -X utf8 backend/src/research/gold_gvz_interval_gate.py
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
    ewma20_vol,
    frozen_quantiles,
    interval_coverage,
    robustness_extreme,
    sigma20_rolling,
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

OUT_CSV = DATA_DIR / "reports" / "gold_gvz_interval_gate.csv"

# Scale a priori: GVZ ≈ annualized 30d implied vol % -> 20-day log-return vol.
GVZ_TO_LOG20 = np.sqrt(20 / 252) / 100.0


def gvz_sigma(panel: pd.DataFrame, df: pd.DataFrame) -> pd.Series:
    """σ_dyn = (GVZ_PIT/100)*sqrt(20/252), align ffill vào panel index."""
    gvz = pit_series(load_gvz(CACHE_FILE))
    aligned = pit_align(gvz, panel.index)
    return (aligned * GVZ_TO_LOG20).rename("gvz").reindex(df.index)


def sigma20_plus_gvz(panel: pd.DataFrame, df: pd.DataFrame) -> pd.Series:
    """σ_dyn = sqrt(sigma20² + gvz²) — kết hợp realized + implied (a priori)."""
    s20 = sigma20_rolling(panel, H).reindex(df.index)
    gvz = df["gvz"] if "gvz" in df else gvz_sigma(panel, df)
    return np.sqrt(s20**2 + gvz**2).rename("sigma20_gvz")


def build_frame(panel: pd.DataFrame) -> pd.DataFrame:
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
    out["gvz_n_pit"] = int(gvz_sigma(panel, df).notna().sum())
    for p in LEVELS:
        k = int(p * 100)
        q = qs[p]
        out[f"q_{k}"] = q
        out[f"coverage_{k}_cal"] = interval_coverage(cal, sig, q)
        out[f"coverage_{k}_test"] = interval_coverage(test, sig, q)
    ev_m = evaluate_magnitude(test, "pred", H)
    test_n = test.copy()
    test_n["pred_naive"] = 0.0
    ev_n = evaluate_magnitude(test_n, "pred_naive", H)
    out["mid_mae_test"] = ev_m["mae"]
    out["naive_mae_test"] = ev_n["mae"]
    out["mid_rmse_test"] = ev_m["rmse"]
    out["naive_rmse_test"] = ev_n["rmse"]
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
    chk = {
        "coverage80": lo80 <= c80 <= hi80,
        "coverage90": lo90 <= c90 <= hi90,
        "midpoint_mae": g["mid_mae_test"] < g["naive_mae_test"],
        "robust": GATE_ROBUST[0] <= g["robust_1pct"] <= GATE_ROBUST[1]
        and GATE_ROBUST[0] <= g["robust_5pct"] <= GATE_ROBUST[1],
        "regime80": all(v >= REGIME_MIN_80 for v in g["regime_80"].values()) if g["regime_80"] else False,
        "frozen": g["max_cal_date"] <= CALIB_CUTOFF,
    }
    return chk


def main() -> None:
    print("=" * 112)
    print("  GOLD H20 GVZ INTERVAL GATE — GVZ như σ_dyn (implied vol) vs realized vol")
    print("=" * 112)

    panel = load_v02_panel()
    panel = add_log_return_target(panel, H)
    df = build_frame(panel)
    df["gvz"] = gvz_sigma(panel, df)
    df["sigma20_gvz"] = sigma20_plus_gvz(panel, df)
    print(f"  Panel: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")
    print(f"  GVZ PIT align non-NA: {int(df['gvz'].notna().sum())} | GVZ scale={GVZ_TO_LOG20:.6f}")

    methods = ("sigma20", "ewma20", "gvz", "sigma20_gvz")
    for method in methods:
        tag = {
            "sigma20": "PRIMARY baseline (a priori)",
            "ewma20": "descriptive",
            "gvz": "CANDIDATE",
            "sigma20_gvz": "CANDIDATE combine",
        }[method]
        print(f"\n  ── {method} [{tag}] ──")
        g = gate_eval(df, panel, method)
        print(f"  calibration n={g['n_cal']} (max {g['max_cal_date']}) | test n={g['n_test']}")
        for p in LEVELS:
            k = int(p * 100)
            print(f"  q_{k}={g[f'q_{k}']:.3f}  cov: cal={g[f'coverage_{k}_cal']:.3f} test={g[f'coverage_{k}_test']:.3f}")
        print(
            f"  midpoint MAE={g['mid_mae_test']:.4f} (naive {g['naive_mae_test']:.4f}) "
            f"RMSE={g['mid_rmse_test']:.4f} (naive {g['naive_rmse_test']:.4f})"
        )
        print(f"  robust80 loại 1%: {g['robust_1pct']:.3f}, 5%: {g['robust_5pct']:.3f}")
        print("  regime80: " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["regime_80"].items())))
        print("  year80:   " + "  ".join(f"{k}:{v:.3f}" for k, v in sorted(g["year_80"].items())))

    # ── GATE verdict ──
    print("\n" + "=" * 112)
    print("  GATE VERDICT (frozen calibration <= 2024, 2025+ pure OOS):")
    base = gate_eval(df, panel, "sigma20")
    cands = []
    for method in ("gvz", "sigma20_gvz"):
        g = gate_eval(df, panel, method)
        chk = check_row(g)
        gap = {p: abs(g[f"coverage_{int(p * 100)}_test"] - p) for p in LEVELS}
        base_gap = {p: abs(base[f"coverage_{int(p * 100)}_test"] - p) for p in LEVELS}
        improved = all(gap[p] <= base_gap[p] + 0.005 for p in LEVELS) and any(gap[p] < base_gap[p] - 0.005 for p in LEVELS)
        pass_all = all(chk.values())
        cands.append((method, pass_all, improved, chk, gap, base_gap))
        print(f"\n  ── {method} ──")
        for k, v in chk.items():
            print(f"    [{'PASS' if v else 'FAIL'}] {k}")
        print(
            f"    gap vs target: 80={gap[0.80]:.3f} (base {base_gap[0.80]:.3f}), "
            f"90={gap[0.90]:.3f} (base {base_gap[0.90]:.3f})"
        )
        print(
            f"    → {'all checks PASS' if pass_all else 'checks FAIL'}; "
            f"coverage-gap {'improved' if improved else 'not improved'} vs sigma20"
        )

    ok_cand = [m for m, p, imp, *_ in cands if p and imp]
    print(f"\n  KẾT LUẬN: {'GVZ INTERVAL GATE PASS' if ok_cand else 'GVZ INTERVAL GATE FAIL'}")
    if ok_cand:
        print(f"  candidate(s) PASS: {ok_cand}")
    else:
        print("  Không cấu hình GVZ cải thiện OOS coverage-gap so với σ20 (v0.3B).")
        print("  → Giữ kết luận: Gold Engine dự báo expected return/magnitude, chưa dự báo uncertainty distribution đủ tốt.")

    # ── Save ──
    rows = [gate_eval(df, panel, m) for m in methods]
    for r in rows:
        r["regime_80"] = str(r["regime_80"])
        r["year_80"] = str(r["year_80"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")


if __name__ == "__main__":
    main()
