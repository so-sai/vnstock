"""gold_forecast_engine_v02.py — Gold v0.2 Model Comparison: M1 vs M1+M2 (CB/IFS).

Trả lời: thêm `CB_IFS_*` (central-bank reserve-demand, PIT IFS vintage) vào M1
monetary có cải thiện OOS không? Kiểm định bằng walk-forward time-series,
KHÔNG random split, KHÔNG tuning theo 2025.

Kế thừa pipeline v0.1 (gold_forecast_engine_v01):
  - load_macro_series / build_panel / walk_forward / _accuracy / M1_FEATURES.
  - CB feature = CB_IFS_* từ gold_cb_feature_audit (build_cb_features + _pit_latest_step)
    — IFS_GOLD_RESERVE_CHANGE GLOBAL PIT-step (latest vintage per obs, pub<=t).

Boundary (nhiễm):
  - IS 2022-2024 (walk-forward block OOS) → dùng để phán quyết nâng cấp.
  - 2025-2026 chỉ DESCRIPTIVE (đã nhiễm từ các thí nghiệm khác — cấm chọn tham số).
  - H20 primary; H60/H120 diagnostic (không reject nếu H20 thắng).

Metrics:
  - AUC overall + per-year ΔAUC (2022..2025).
  - Log-Loss / Brier (calibration).
  - Spearman corr giữa prob vector M1 và M1+M2 (redundancy).
  - Directional hit rate tại vùng tự tin cao (P>0.60 | P<0.40).

Usage (từ project root):
  python -X utf8 backend/src/research/gold_forecast_engine_v02.py
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

from src.research.gold_cb_feature_audit import build_cb_features
from src.research.gold_forecast_engine_v01 import (
    M1_FEATURES,
    _accuracy,
    build_panel,
    load_macro_series,
    walk_forward,
)

HORIZONS = (20, 60, 120)
CB_FEATURES = ("CB_IFS_level", "CB_IFS_z", "CB_IFS_sum3")
# Primary feature cho verdict v0.2: CB_IFS_z (expanding zscore, PIT-safe).
CB_PRIMARY = "CB_IFS_z"
# Vùng xác suất "tự tin cao" cho directional hit rate.
HIGH_P = 0.60
LOW_P = 0.40
OUT_CSV = DATA_DIR / "reports" / "gold_v02_model_comparison.csv"


def _connect():
    import sqlite3

    return sqlite3.connect(str(GOLD_H2_DB))


def load_v02_panel() -> pd.DataFrame:
    """Panel usable (M1 đủ + R120) ghép CB features PIT."""
    import sqlite3

    conn = sqlite3.connect(str(SCREENER_DB))
    series = load_macro_series(conn)
    conn.close()
    usable = build_panel(series).dropna(subset=["GOLD"]).dropna(subset=[*M1_FEATURES, "R120"])
    conn_h2 = sqlite3.connect(str(GOLD_H2_DB))
    feats = build_cb_features(usable, conn_h2)
    conn_h2.close()
    return usable.join(feats)


def _logloss(y: np.ndarray, p: np.ndarray) -> float | None:
    m = ~np.isnan(p)
    if m.sum() < 2:
        return None
    yv, pv = y[m], p[m]
    eps = 1e-12
    pv = np.clip(pv, eps, 1 - eps)
    return float(-np.mean(yv * np.log(pv) + (1 - yv) * np.log(1 - pv)))


def _brier(y: np.ndarray, p: np.ndarray) -> float | None:
    m = ~np.isnan(p)
    if m.sum() < 2:
        return None
    return float(np.mean((y[m] - p[m]) ** 2))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3:
        return np.nan
    from scipy.stats import spearmanr

    rho, _ = spearmanr(a[m], b[m])
    return float(rho)


def compare_models(o1: pd.DataFrame, o2: pd.DataFrame, h: int) -> dict:
    """So sánh 2 model trên CÙNG tập OOS (intersection của 2 prediction).

    Trả về dict metrics: n, auc_1/auc_2, delta_auc, logloss_1/2, brier_1/2,
    spearman_prob, hit_high_1/2, hit_high_n, delta_auc_per_year.
    """
    both = o1[["y", "prob"]].join(o2[["prob"]], rsuffix="_2", how="inner").dropna(subset=["prob", "prob_2"])
    n = len(both)
    res = {"horizon": h, "n_oos": n}
    if n == 0:
        res.update(
            auc_m1=np.nan,
            auc_m2=np.nan,
            delta_auc=np.nan,
            acc_m1=np.nan,
            acc_m2=np.nan,
            base_rate=np.nan,
            logloss_m1=None,
            logloss_m2=None,
            brier_m1=None,
            brier_m2=None,
            spearman_prob=np.nan,
        )
        res.update(_high_conf_hit(both))
        res["delta_auc_per_year"] = _per_year_delta_auc(o1, o2, h)
        return res
    res["base_rate"] = float(both["y"].mean())
    res["acc_m1"] = float((both["prob"] >= 0.5).astype(float).eq(both["y"]).mean())
    res["acc_m2"] = float((both["prob_2"] >= 0.5).astype(float).eq(both["y"]).mean())
    from sklearn.metrics import roc_auc_score

    try:
        res["auc_m1"] = float(roc_auc_score(both["y"], both["prob"]))
        res["auc_m2"] = float(roc_auc_score(both["y"], both["prob_2"]))
        res["delta_auc"] = res["auc_m2"] - res["auc_m1"]
    except ValueError:
        res["auc_m1"] = np.nan
        res["auc_m2"] = np.nan
        res["delta_auc"] = np.nan
    # calibration trên intersection
    res["logloss_m1"] = _logloss(both["y"].values, both["prob"].values)
    res["logloss_m2"] = _logloss(both["y"].values, both["prob_2"].values)
    res["brier_m1"] = _brier(both["y"].values, both["prob"].values)
    res["brier_m2"] = _brier(both["y"].values, both["prob_2"].values)
    res["spearman_prob"] = _spearman(both["prob"].values, both["prob_2"].values)
    # hit rate vùng tự tin cao
    res.update(_high_conf_hit(both))
    # per-year ΔAUC
    res["delta_auc_per_year"] = _per_year_delta_auc(o1, o2, h)
    return res


def _high_conf_hit(df: pd.DataFrame) -> dict:
    """Directional hit rate chỉ tại P>HIGH_P | P<LOW_P (cả 2 model)."""
    out = {}
    for tag, col in (("m1", "prob"), ("m2", "prob_2")):
        sel = df[(df[col] > HIGH_P) | (df[col] < LOW_P)]
        if len(sel) < 3:
            out[f"hit_high_{tag}"] = np.nan
            out[f"hit_high_n_{tag}"] = int(len(sel))
            continue
        pred = (sel[col] >= 0.5).astype(float)
        out[f"hit_high_{tag}"] = float((pred == sel["y"]).mean())
        out[f"hit_high_n_{tag}"] = int(len(sel))
    return out


def _per_year_delta_auc(o1: pd.DataFrame, o2: pd.DataFrame, h: int) -> dict:
    """ΔAUC (M2−M1) theo từng năm trên intersection OOS."""
    out = {}
    joined = o1[["y", "prob"]].join(o2[["prob"]], rsuffix="_2", how="inner").dropna(subset=["prob", "prob_2"])
    for year in sorted(set(joined.index.year)):
        jy = joined[joined.index.year == year]
        if len(jy) < 3 or jy["y"].nunique() < 2:
            continue
        try:
            from sklearn.metrics import roc_auc_score

            a1 = float(roc_auc_score(jy["y"], jy["prob"]))
            a2 = float(roc_auc_score(jy["y"], jy["prob_2"]))
            out[str(year)] = a2 - a1
        except ValueError:
            continue
    return out


def _fmt_yearly(d: dict) -> str:
    if not d:
        return "(no valid year)"
    return "  ".join(f"{y}:{v:+.3f}" for y, v in sorted(d.items()))


def main() -> None:
    print("=" * 112)
    print("  GOLD v0.2 MODEL COMPARISON — M1 (monetary) vs M1+M2 (CB/IFS PIT)")
    print("=" * 112)

    panel = load_v02_panel()
    print(f"  Panel usable: {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")
    print(f"  CB coverage: {int(panel['CB_IFS_z'].notna().sum())} ngày CB_IFS_z")
    by_year = panel.index.year.value_counts().sort_index()
    print(f"  Ngày theo năm: {dict(by_year)}")

    rows = []
    for h in HORIZONS:
        print(f"\n  ── HORIZON H{h} ──")
        o1 = walk_forward(panel, M1_FEATURES, h)
        m1 = _accuracy(o1, h)
        print(
            "    M1_monetary     "
            f"n={m1['n']:>4d} acc={m1['acc'] * 100:5.1f}% "
            f"base={m1['base'] * 100:5.1f}% auc={m1['auc']:.3f}"
        )

        for cb in CB_FEATURES:
            o2 = walk_forward(panel, M1_FEATURES + [cb], h)
            m2 = _accuracy(o2, h)
            d_auc = np.nan
            if m1["n"] and m2["n"] and not np.isnan(m1["auc"]) and not np.isnan(m2["auc"]):
                d_auc = m2["auc"] - m1["auc"]
            print(f"    M2 +{cb:<13s} n={m2['n']:>4d} auc={m2['auc']:.3f}  dAUC={d_auc:+.3f}")
            base_row = {
                "horizon": h,
                "model": f"M1_{cb}" if False else cb,
                "n_oos": m2["n"],
                "acc": m2["acc"],
                "base_rate": m2["base"],
                "auc": m2["auc"],
                "delta_auc": d_auc,
                "delta_acc": m2["acc"] - m1["acc"] if m1["n"] and m2["n"] else np.nan,
            }
            rows.append(base_row)

        # ── comparison chi tiết cho CB_PRIMARY ──
        o2 = walk_forward(panel, M1_FEATURES + [CB_PRIMARY], h)
        comp = compare_models(o1, o2, h)
        print(f"\n    COMPARE (M1 vs M1+{CB_PRIMARY}):")
        print(f"      n={comp['n_oos']}  auc1={comp['auc_m1']:.3f} auc2={comp['auc_m2']:.3f} dAUC={comp['delta_auc']:+.3f}")
        if comp.get("logloss_m1") is not None:
            print(
                f"      logloss: M1={comp['logloss_m1']:.4f} M2={comp['logloss_m2']:.4f}  "
                f"brier: M1={comp['brier_m1']:.4f} M2={comp['brier_m2']:.4f}"
            )
        print(f"      spearman(prob) = {comp['spearman_prob']:.3f}  (≈1 → redundant, ≈0 → độc lập)")
        if comp.get("hit_high_m1") is not None and not np.isnan(comp["hit_high_m1"]):
            print(
                f"      hit-rate vùng tự tin cao: M1={comp['hit_high_m1']:.3f} (n={comp['hit_high_n_m1']}) "
                f"M2={comp['hit_high_m2']:.3f} (n={comp['hit_high_n_m2']})"
            )
        print(f"      per-year dAUC: {_fmt_yearly(comp['delta_auc_per_year'])}")
        rows.append(
            {
                "horizon": h,
                "model": "compare_m1_vs_m1plus_" + CB_PRIMARY,
                "n_oos": comp["n_oos"],
                "auc": comp["auc_m1"],
                "auc_2": comp["auc_m2"],
                "delta_auc": comp["delta_auc"],
                "logloss_m1": comp.get("logloss_m1"),
                "logloss_m2": comp.get("logloss_m2"),
                "brier_m1": comp.get("brier_m1"),
                "brier_m2": comp.get("brier_m2"),
                "spearman_prob": comp.get("spearman_prob"),
                "hit_high_m1": comp.get("hit_high_m1"),
                "hit_high_m2": comp.get("hit_high_m2"),
                "per_year_delta_auc": _fmt_yearly(comp["delta_auc_per_year"]),
            }
        )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")

    print("\n" + "=" * 112)
    print("  VERDICT HƯỚNG DẪN:")
    print("    - H20 primary: dAUC > 0 + per-year ổn định + spearman không ≈1  → PROMISING v0.2")
    print("    - H60/H120 diagnostic (không reject nếu H20 thắng).")
    print("    - 2025-2026 descriptive — cấm tuning theo 2025.")
    print("=" * 112)


if __name__ == "__main__":
    main()
