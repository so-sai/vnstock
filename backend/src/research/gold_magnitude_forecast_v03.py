"""gold_magnitude_forecast_v03.py — READ-ONLY Gate: Gold H20 Magnitude Forecast.

Trả lời: với M1+CB_IFS_z (v0.2, H20 direction PROMISING, AUC 0.574), model có
dự báo được ĐỘ LỚN forward return H20 không?

    Direction:  P(R20 > 0 | M1, CB_IFS_z)        ← đã có (v0.2)
    Magnitude:  E[log R20 | M1, CB_IFS_z]        ← GATE NÀY

READ-ONLY: không ghi replay DB / screener / Governor / gold_h2.db. KHÔNG sửa
checkpoint v0.2 (b96a03d) — module mới, import có kiểm soát.

3 BASELINES (bắt buộc theo contract user):
  1. Naive:                    E[logR20] = 0
  2. Historical expanding mean E[logR20]_t = mean(logR20[0..t-h])  (PIT-safe)
  3. M1 + CB_IFS_z regression  walk-forward expanding (Ridge)

GATE PASS khi (tất cả):
  - OOS MAE/RMSE tốt hơn naive;
  - sign consistency không suy giảm so với direction model (v0.2);
  - improvement tồn tại ở nhiều năm (>=2/3 IS năm: 2022, 2023, 2024);
  - không phụ thuộc vài extreme gold moves (robustness: loại top/bottom 1% |logR20|);
  - prediction interval coverage hợp lý (80% interval, coverage ~0.75-0.85).

Nếu magnitude FAIL nhưng direction PASS → giữ "Gold H20 Direction Forecast",
KHÔNG ép thành price target.

Boundary (nhiễm): IS 2022-2024 (walk-forward block OOS), 2025-2026 descriptive.
H20 primary; H60/H120 diagnostic (không reject nếu H20 thắng).

Usage (từ project root):
  python -X utf8 backend/src/research/gold_magnitude_forecast_v03.py
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
SCREENER_DB = DATA_DIR / "screener_cache.db"
GOLD_H2_DB = DATA_DIR / "gold_h2.db"
for _p in [str(BACKEND / "src"), str(BACKEND), str(PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

from src.research.gold_forecast_engine_v01 import (
    M1_FEATURES,
    MIN_TRAIN,
    WALK_FORWARD_BLOCK,
    _accuracy,
    walk_forward,
)
from src.research.gold_forecast_engine_v02 import CB_PRIMARY, load_v02_panel

H = 20
HORIZONS_DIAG = (60, 120)
INTERVAL_Z = 1.28  # 80% interval
OUT_CSV = DATA_DIR / "reports" / "gold_magnitude_forecast_gate.csv"


def add_log_return_target(panel: pd.DataFrame, h: int = 20) -> pd.DataFrame:
    """Thêm target forward log-return: logR_h = log(GOLD_{t+h}/GOLD_t)."""
    g = panel["GOLD"]
    out = panel.copy()
    out[f"logR{h}"] = np.log(g.shift(-h) / g)
    return out


def expanding_mean_baseline(panel: pd.DataFrame, h: int = 20) -> pd.Series:
    """Historical expanding mean (PIT-safe): tại t chỉ dùng logR đã realized.

    logR_j realized tại j+h → tại t chỉ dùng j <= t-h. Tương đương shifting logR
    đi h rồi expanding mean.
    """
    s = panel[f"logR{h}"]
    return s.shift(h).expanding(min_periods=20).mean()


def walk_forward_regression(panel: pd.DataFrame, features: list[str], h: int = 20) -> pd.DataFrame:
    """Expanding-window walk-forward Ridge regression: train->predict block OOS.

    Target liên tục y=logR_h. Trả về DataFrame OOS: y, pred, resid_std (train
    residual std per block — dùng cho prediction interval).
    """
    from sklearn.linear_model import Ridge

    y = panel[f"logR{h}"]
    X = panel[features].astype(float)
    out = pd.DataFrame(index=panel.index)
    out["y"] = y
    out["pred"] = np.nan
    out["resid_std"] = np.nan

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
        # predict block OOS
        X_te = X.iloc[start:end]
        mask_te = X_te.notna().all(axis=1)
        if mask_te.any():
            pred = clf.predict(X_te[mask_te])
            out.loc[X_te[mask_te].index, "pred"] = pred
            out.loc[X_te[mask_te].index, "resid_std"] = resid_std
        start = end
    return out


def _mae_rmse(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    m = ~np.isnan(p)
    if m.sum() < 2:
        return np.nan, np.nan
    return float(np.mean(np.abs(y[m] - p[m]))), float(np.sqrt(np.mean((y[m] - p[m]) ** 2)))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3:
        return np.nan
    from scipy.stats import spearmanr

    rho, _ = spearmanr(a[m], b[m])
    return float(rho)


def evaluate_magnitude(df: pd.DataFrame, pred_col: str, h: int = 20) -> dict:
    """Đánh giá magnitude model trên OOS rows (có pred != NaN)."""
    sub = df.dropna(subset=[pred_col, f"logR{h}"])
    if len(sub) < 2:
        return {
            "n": 0,
            "mae": np.nan,
            "rmse": np.nan,
            "spearman": np.nan,
            "sign_acc": np.nan,
            "coverage80": np.nan,
            "mean_abs": np.nan,
        }
    y = sub[f"logR{h}"].values
    p = sub[pred_col].values
    mae, rmse = _mae_rmse(y, p)
    # sign consistency: sign(pred) == sign(actual)
    sign_acc = float(np.mean(np.sign(p) == np.sign(y))) if np.any(p != 0) else np.nan
    # prediction interval (80%) từ resid_std per block
    cov = np.nan
    if "resid_std" in sub and sub["resid_std"].notna().all():
        lo = p - INTERVAL_Z * sub["resid_std"].values
        hi = p + INTERVAL_Z * sub["resid_std"].values
        cov = float(np.mean((y >= lo) & (y <= hi)))
    return {
        "n": len(sub),
        "mae": mae,
        "rmse": rmse,
        "spearman": _spearman(p, y),
        "sign_acc": sign_acc,
        "coverage80": cov,
        "mean_abs": float(np.mean(np.abs(y))),
    }


def robustness_extreme(panel: pd.DataFrame, pred_col: str, h: int = 20, drop_pct: float = 0.01) -> dict:
    """Loại top/bottom |logR20| extremes rồi đánh giá lại (chống extreme-move phụ thuộc)."""
    sub = panel.dropna(subset=[pred_col, f"logR{h}"]).copy()
    thr_hi = sub[f"logR{h}"].abs().quantile(1 - drop_pct)
    keep = sub[sub[f"logR{h}"].abs() <= thr_hi]
    return evaluate_magnitude(keep, pred_col, h)


def _per_year_mae(df: pd.DataFrame, pred_col: str, h: int = 20) -> dict:
    out = {}
    for year in sorted(set(df.index.year)):
        sub = df[(df.index.year == year) & df[pred_col].notna()].dropna(subset=[f"logR{h}"])
        if len(sub) < 3:
            continue
        mae = float(np.mean(np.abs(sub[f"logR{h}"].values - sub[pred_col].values)))
        out[str(year)] = mae
    return out


def main() -> None:
    print("=" * 112)
    print("  GOLD H20 MAGNITUDE GATE — E[logR20] vs naive / expmean / M1+CB_IFS_z")
    print("=" * 112)

    panel = load_v02_panel()
    panel = add_log_return_target(panel, H)
    print(f"  Panel: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")

    # ── Baselines ──
    panel["pred_naive"] = 0.0
    panel["pred_expmean"] = expanding_mean_baseline(panel, H)

    # ── Model regression ──
    reg = walk_forward_regression(panel, M1_FEATURES + [CB_PRIMARY], H)
    panel["pred_model"] = reg["pred"]
    panel["resid_std"] = reg["resid_std"]

    # ── Direction model (v0.2) để so sign consistency ──
    o_dir = walk_forward(panel, M1_FEATURES + [CB_PRIMARY], H)
    d_acc = _accuracy(o_dir, H)

    models = ("naive", "expmean", "model")
    print("\n  OOS MAGNITUDE EVALUATION (walk-forward):")
    results = {}
    for m in models:
        ev = evaluate_magnitude(panel, f"pred_{m}", H)
        results[m] = ev
        line = (
            f"    {m:<10s} n={ev['n']:>4d} mae={ev['mae']:.4f} rmse={ev['rmse']:.4f} "
            f"spearman={ev['spearman']:.3f} sign_acc={ev['sign_acc']:.3f}"
        )
        if ev["coverage80"] is not None and not np.isnan(ev["coverage80"]):
            line += f" cov80={ev['coverage80']:.3f}"
        print(line)

    # ── Common-OOS comparison (chỉ rows model có pred — để so sánh công bằng) ──
    common = panel.dropna(subset=["pred_model", "logR20"]).copy()
    print(f"\n  COMMON-OOS COMPARISON (n={len(common)}, chỉ rows cả 3 model có giá trị):")
    common_res = {}
    for m in models:
        ev = evaluate_magnitude(common, f"pred_{m}", H)
        common_res[m] = ev
        print(
            f"    {m:<10s} mae={ev['mae']:.4f} rmse={ev['rmse']:.4f} "
            f"spearman={ev['spearman']:.3f} sign_acc={ev['sign_acc']:.3f}"
        )

    # ── vs naive ──
    print("\n  IMPROVEMENT vs NAIVE:")
    for m in ("expmean", "model"):
        r = results[m]
        n_ = results["naive"]
        if not np.isnan(r["mae"]) and not np.isnan(n_["mae"]):
            print(
                f"    {m:<10s} dMAE={r['mae'] - n_['mae']:+.4f} ({r['mae'] / n_['mae'] * 100 - 100:+.1f}%)  "
                f"dRMSE={r['rmse'] - n_['rmse']:+.4f}"
            )

    # ── Sign consistency vs direction ──
    print("\n  SIGN CONSISTENCY:")
    print(f"    direction model (v0.2) acc={d_acc['acc'] * 100:.1f}%  auc={d_acc['auc']:.3f}")
    for m in ("expmean", "model"):
        sa = results[m]["sign_acc"]
        print(f"    magnitude {m:<10s} sign_acc={sa * 100:.1f}%" if sa == sa else f"    magnitude {m:<10s} sign_acc=N/A")

    # ── Per-year MAE ──
    print("\n  PER-YEAR MAE (đơn vị: log return):")
    for m in ("naive", "expmean", "model"):
        py = _per_year_mae(panel, f"pred_{m}", H)
        print(f"    {m:<10s} " + "  ".join(f"{y}:{v:.4f}" for y, v in sorted(py.items())))

    # ── Robustness ──
    print("\n  ROBUSTNESS (loại 1% cực trị |logR20|):")
    for m in ("naive", "expmean", "model"):
        rb = robustness_extreme(panel, f"pred_{m}", H)
        print(f"    {m:<10s} n={rb['n']:>4d} mae={rb['mae']:.4f} rmse={rb['rmse']:.4f}")

    # ── Diagnostic H60/H120 ──
    print("\n  DIAGNOSTIC H60/H120 (model regression only):")
    for dh in HORIZONS_DIAG:
        p2 = add_log_return_target(panel, dh)
        reg2 = walk_forward_regression(p2, M1_FEATURES + [CB_PRIMARY], dh)
        p2["pred_model"] = reg2["pred"]
        ev = evaluate_magnitude(p2, "pred_model", dh)
        n_naive = _mae_rmse(p2[f"logR{dh}"].values, np.zeros(len(p2)))
        print(
            f"    H{dh}: mae={ev['mae']:.4f} (naive {n_naive[0]:.4f}) rmse={ev['rmse']:.4f} "
            f"spearman={ev['spearman']:.3f} sign_acc={ev['sign_acc']:.3f}"
        )

    # ── Save ──
    rows = []
    for m in models:
        r = results[m]
        rows.append({"model": m, **{k: v for k, v in r.items() if k != "n"}})
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")

    # ── Gate verdict (dựa trên COMMON-OOS so sánh công bằng) ──
    print("\n" + "=" * 112)
    print("  GATE VERDICT (tự chấm điểm, quyết định cuối ở note):")
    rm = common_res["model"]
    rn = common_res["naive"]
    chk1 = rm["mae"] < rn["mae"] and rm["rmse"] < rn["rmse"]
    chk2 = rm["sign_acc"] == rm["sign_acc"] and (rm["sign_acc"] >= d_acc["acc"] - 0.02)
    py_m = _per_year_mae(panel, "pred_model", H)
    py_n = _per_year_mae(panel, "pred_naive", H)
    is_years = [y for y in ("2022", "2023", "2024") if y in py_m and y in py_n]
    wins = sum(1 for y in is_years if py_m[y] < py_n[y])
    chk3 = wins >= 2
    rb = robustness_extreme(panel, "pred_model", H)
    chk4 = rb["mae"] < robustness_extreme(panel, "pred_naive", H)["mae"]
    chk5 = rm["coverage80"] == rm["coverage80"] and 0.70 <= rm["coverage80"] <= 0.88
    for name, ok in (
        ("MAE/RMSE < naive", chk1),
        ("sign_acc >= direction - 0.02", chk2),
        ("thắng naive >=2/3 năm IS", chk3),
        ("robustness loại extreme vẫn thắng", chk4),
        ("coverage80 hợp lý (0.70-0.88)", chk5),
    ):
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    overall = all((chk1, chk2, chk3, chk4, chk5))
    print(f"\n  KẾT LUẬN: {'MAGNITUDE PASS' if overall else 'MAGNITUDE FAIL — giữ Direction Forecast'}")
    print("  (2025-2026 descriptive — không tuning theo 2025.)")
    print("=" * 112)


if __name__ == "__main__":
    main()
