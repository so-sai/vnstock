"""opportunity_score_gate4.py — READ-ONLY walk-forward stability + incremental discrimination.

Gate 4 câu hỏi chính (guardrail từ Gate 3A):
    ROE + momentum có thêm information CONDITIONAL ON VALUATION,
    hay chỉ làm score "đẹp" hơn?

So sánh trên CÙNG gate:
    M_value   : valuation-only baseline (Gate 3A đã PASS, IC +0.088 H20)
    M_cluster : 1 representative/cluster: VALUATION=PE_z_ts, QUALITY=ROE_z_ts,
                BEHAVIOR=ret_120d → rank-average (equal-weight, không fit)

Protocol:
  1. M_value vs M_cluster — daily RankIC / top-decile spread / monotonicity /
     per-year / per-regime / IS vs OOS.
  2. INCREMENTAL double-sort: trong TỪNG valuation decile (nội ngày), ROE/ret_120d
     còn phân biệt được không? → RankIC "residual" sau khi control valuation.
  3. WALK-FORWARD expanding (2022→2023, 2022-23→2024, 2022-24→2025 descriptive)
     cho M_value và M_cluster; IC/IR, %pos, per-window.
  4. Verdict: M_cluster > M_value (incremental)? Nếu KHÔNG → giữ M_value.

TUYỆT ĐỐI: không đưa vào Governor / Selection Layer / budget ≤20. 2025 = descriptive.

READ-ONLY. Usage:
  python -X utf8 backend/src/research/opportunity_score_gate4.py
"""

from __future__ import annotations

import argparse
import math
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
REPLAY_DIR = DATA_DIR / "replays" / "replay_58_ts"

sys.path.insert(0, str(BACKEND / "src"))
from research.opportunity_score_gate3 import (
    CLUSTERS,
    _rank_within_day,
    build_scores,
    diagnose_score,
)
from research.opportunity_score_research import (
    HORIZONS,
    MIN_SYMBOLS_PER_DAY,
    build_feature_matrix,
    load_pool,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

YEARS = ("2022", "2023", "2024", "2025")
IS_YEARS = ("2022", "2023", "2024")
OOS_YEARS = ("2025",)

# 1 representative mỗi cluster (evidence independence)
CLUSTER_REPS = {
    "valuation": "PE_z_ts",
    "quality": "ROE_z_ts",
    "behavior": "ret_120d",
}


def cluster_score_rep(df: pd.DataFrame) -> pd.Series:
    """M_cluster = rank-average của 1 representative/cluster (available-feature norm)."""
    cols = {rep: CLUSTERS[c][rep] for c, rep in CLUSTER_REPS.items()}
    return _rank_within_day(df, cols).mean(axis=1)


def build_gate4_scores(df: pd.DataFrame) -> pd.DataFrame:
    """M_value (baseline) + M_cluster (composite). Cùng index như df."""
    out = df.copy()
    base = build_scores(out)
    out["M_value"] = base["M_value"]
    out["M_cluster"] = cluster_score_rep(out)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# INCREMENTAL DOUBLE-SORT: feature có còn signal trong valuation decile?
# ═══════════════════════════════════════════════════════════════════════════


def residual_ic_within_valuation(df: pd.DataFrame, feature: str, target: str, n_deciles: int = 5) -> dict:
    """Daily RankIC của feature trong TỪNG valuation quintile (control valuation).

    Không pooled — tính per-day, gộp quintile x ngày, rồi aggregate.
    feature: 'ROE_z_ts' (pos) / 'ret_120d' (pos) / 'M_cluster' (pos).
    """
    rows = []
    for date, g in df.groupby("date", sort=True):
        g = g.dropna(subset=["M_value", feature, target])
        if len(g) < MIN_SYMBOLS_PER_DAY:
            continue
        val_q = pd.qcut(g["M_value"].rank(method="first"), n_deciles, labels=False, duplicates="drop")
        for q in range(n_deciles):
            sub = g[val_q == q]
            if len(sub) < 8:  # đủ nhỏ trong quintile
                continue
            rho = _spearman(sub[feature], sub[target])
            if rho is not None:
                rows.append({"date": date, "quintile": q, "ic": rho})
    if not rows:
        return {"feature": feature, "n_obs": 0, "error": "no_rows"}
    day = pd.DataFrame(rows)
    ics = day.dropna(subset=["ic"])
    if ics.empty:
        return {"feature": feature, "n_obs": 0, "error": "no_ic"}
    by_q = ics.groupby("quintile")["ic"]
    return {
        "feature": feature,
        "n_obs": int(len(ics)),
        "mean_ic_control_val": round(float(ics["ic"].mean()), 4),
        "median_ic_control_val": round(float(ics["ic"].median()), 4),
        "pct_positive": round(float((ics["ic"] > 0).mean() * 100), 1),
        "per_quintile": {int(q): round(float(v), 4) for q, v in by_q.mean().items()},
    }


def _spearman(x, y) -> float | None:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3 or np.unique(x).size < 2 or np.unique(y).size < 2:
        return None
    from scipy.stats import spearmanr

    try:
        rho = spearmanr(x, y).statistic
    except ValueError, TypeError:
        return None
    if rho is None or math.isnan(rho):
        return None
    return float(rho)


# ═══════════════════════════════════════════════════════════════════════════
# WALK-FORWARD (expanding) — M_value vs M_cluster
# ═══════════════════════════════════════════════════════════════════════════


def walk_forward_models(df: pd.DataFrame) -> list[dict]:
    """Expanding: train 2022→test 2023; 2022-23→2024; 2022-24→2025(desc)."""
    scored = build_gate4_scores(df)
    out = []
    for test_year in YEARS[1:]:
        test = scored[scored["year"].astype(str) == test_year]
        for model in ("M_value", "M_cluster"):
            d = diagnose_score(test, model, "r20")
            d["method"] = "walk-forward"
            d["model"] = model
            d["test_year"] = test_year
            d["train_years"] = "+".join(YEARS[: YEARS.index(test_year)])
            out.append(d)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 4 — walk-forward stability + incremental discrimination (READ-ONLY)")
    ap.add_argument("--pool", choices=("all", "deploy"), default="all")
    ap.add_argument("--prefix", default="replay_58")
    args = ap.parse_args()

    pool = load_pool(REPLAY_DIR, prefix=args.prefix, pool=args.pool)
    print(f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} dates={pool['date'].nunique()}")
    df = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(df)} cols={len(df.columns)}")

    scored = build_gate4_scores(df)
    print(f"[score] M_value n={scored['M_value'].notna().sum():,}  M_cluster n={scored['M_cluster'].notna().sum():,}")

    # ── 1. M_value vs M_cluster ────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  1) M_value (valuation-only) vs M_cluster (valuation+ROE+ret_120d)")
    print("     Câu hỏi: cluster-composite có incremental discrimination không?")
    print("=" * 132)
    all_rows = []
    for horizon in HORIZONS:
        target = f"r{horizon}"
        print(f"\n### Horizon H{horizon}")
        print(
            f"{'model':<10}{'n':>7}{'IC':>8}{'tstat':>8}{'%pos':>6}{'IS_IC':>8}{'OOS(desc)':>10}{'topDecile%':>11}{'mono':>8}"
        )
        print("-" * 132)
        for model in ("M_value", "M_cluster"):
            d = diagnose_score(scored, model, target)
            all_rows.append(d)
            if d.get("error"):
                print(f"{model:<10}{d['n_obs']:>7}  {d['error']}")
                continue
            oos = f"{d['ic_OOS_2025_desc']:+.3f}" if d["ic_OOS_2025_desc"] is not None else "   -"
            spread = f"{d['top_decile_spread_pct']:+.2f}" if d["top_decile_spread_pct"] is not None else "   -"
            mono = f"{d['mono_mean']:+.3f}" if d["mono_mean"] is not None else "  -"
            print(
                f"{model:<10}{d['n_obs']:>7}{d['pooled_ic']:>+8.3f}{d['ic_tstat']:>8.1f}"
                f"{d['pct_days_pos']:>6.0f}{d['ic_IS_2022_24']:>+8.3f}{oos:>10}"
                f"{spread:>11}{mono:>8}"
            )

    # ── 2. Incremental double-sort ─────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  2) INCREMENTAL DISCRIMINATION — feature còn signal trong valuation quintile?")
    print("     RankIC(feature, Y) SAU KHI control M_value (double-sort nội ngày)")
    print("=" * 132)
    for feature, label in (("ROE_z_ts", "Quality (ROE)"), ("ret_120d", "Momentum (ret_120d)")):
        for horizon in (20, 120):
            d = residual_ic_within_valuation(scored, feature, f"r{horizon}")
            all_rows.append({**d, "score": f"resid_{feature}", "target": f"r{horizon}"})
            if d.get("error"):
                print(f"  {label:<20} H{horizon}: {d['error']}")
                continue
            q = d["per_quintile"]
            qs = "  ".join(f"q{q_ + 1}={v:+.3f}" for q_, v in sorted(q.items()))
            print(
                f"  {label:<20} H{horizon}: control-val IC={d['mean_ic_control_val']:+.4f} "
                f"(pos={d['pct_positive']:.0f}%)  [{qs}]"
            )

    # ── 3. Walk-forward ────────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  3) WALK-FORWARD expanding — M_value vs M_cluster (IC trên test window)")
    print("     2025 = descriptive (ĐÃ NHIỄM)")
    print("=" * 132)
    wf = walk_forward_models(df)
    wf_frame = pd.DataFrame(wf)
    for _, r in wf_frame.iterrows():
        if r.get("error") or pd.isna(r.get("pooled_ic")):
            print(f"  [{r['test_year']}] {r['model']:<9} train={r['train_years']:<15} ERROR")
            continue
        spread = r["top_decile_spread_pct"]
        spread_s = f"{spread:+.2f}" if spread is not None else "    -"
        print(
            f"  [{r['test_year']}] {r['model']:<9} train={r['train_years']:<15} "
            f"IC={r['pooled_ic']:+.3f} tstat={r['ic_tstat']:6.1f} "
            f"%pos={r['pct_days_pos']:3.0f} spread={spread_s} mono="
            f"{r['mono_mean'] if not pd.isna(r['mono_mean']) else 0.0:+.3f}"
        )
    for row in wf:
        all_rows.append(row)

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  VERDICT GATE 4 — incremental discrimination của cluster composite")
    print("=" * 132)
    base20 = diagnose_score(scored, "M_value", "r20")
    clus20 = diagnose_score(scored, "M_cluster", "r20")
    delta = (clus20["pooled_ic"] or 0.0) - (base20["pooled_ic"] or 0.0)
    print(f"  M_value   H20 IC={base20['pooled_ic']:+.4f} tstat={base20['ic_tstat']:6.1f}")
    print(f"  M_cluster H20 IC={clus20['pooled_ic']:+.4f} tstat={clus20['ic_tstat']:6.1f}")
    print(f"  Δ IC (cluster − value) = {delta:+.4f}")
    resid_roe = residual_ic_within_valuation(scored, "ROE_z_ts", "r20")
    resid_mom = residual_ic_within_valuation(scored, "ret_120d", "r20")
    if "error" not in resid_roe and "error" not in resid_mom:
        print(
            f"  ROE    residual IC (control val) H20 = {resid_roe['mean_ic_control_val']:+.4f} "
            f"pos={resid_roe['pct_positive']:.0f}%"
        )
        print(
            f"  ret120 residual IC (control val) H20 = {resid_mom['mean_ic_control_val']:+.4f} "
            f"pos={resid_mom['pct_positive']:.0f}%"
        )

    result = pd.DataFrame(all_rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_score_gate4_audit_{args.pool}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")

    print("\n" + "=" * 132)
    print("  GUARDRAIL: kết quả là score-level evidence. Chưa áp budget/Governor.")
    print("  Nếu M_cluster KHÔNG incremental → giữ M_value, không thêm feature vô nghĩa.")
    print("=" * 132)


if __name__ == "__main__":
    main()
