"""opportunity_score_gate3.py — READ-ONLY score construction & walk-forward audit.

Gate 3A mục tiêu (SAU Gate 2 — feature-level PASS cho valuation family):
  1. REDUNDANCY / CLUSTER audit: pairwise cross-sectional Spearman nội ngày giữa
     các feature — PE_z_ts / PE_pct / val_zone_ts có thể là CÙNG MỘT latent
     variable. Nếu corr ≈ ±1 thì rank-average tất cả = đếm valuation 6 lần.
  2. SCORE construction theo cluster (available-feature normalization):
       cluster_score = mean( rank_within_day(x_i) | x_i available )  — KHÔNG
       quy missing về 0; thiếu feature nào thì trung bình trên feature có sẵn.
  3. MODEL baselines — so sánh trên cùng diagnostic:
       M_random  : noise floor (rank ngẫu nhiên nội ngày, trung bình 30 lần)
       M_p_gain  : failed control (Gate 1)
       M_value   : VALUATION cluster only (PE_z, PB_z, PS_z, EV_EBITDA_z,
                   PE_pct, val_zone) — alpha thực sự đến từ đâu?
       M_rankavg : rank-average PASS features (valuation + ROE + ret_120d),
                   không fitting trọng số
       M_IC      : w_k = IC_k / sum|IC_k|, IC ước lượng CHỈ trên training
                   window (walk-forward expanding, OOS), 2025 = descriptive
                   (ĐÃ NHIỄM — không dùng chọn feature/weight)
  4. DIAGNOSTIC mỗi model × mỗi horizon: daily RankIC, top-decile spread,
     quintile monotonicity nội ngày, per-year, per-regime, IS (2022-2024)
     vs OOS (2025).

TUYỆT ĐỐI: không đưa score vào Governor, không áp budget ≤20, không dùng
kết quả này làm EXECUTE signal. Đây là tầng score alpha, chưa phải
portfolio/selection alpha.

Contamination boundary (bất biến):
  - IS evidence  : 2022-2024
  - OOS/descriptive : 2025 (ĐÃ NHIỄM — chỉ mô tả, không chọn feature/weight)

READ-ONLY: không ghi replay DB / screener_cache.db / financial_facts.db.

Usage (từ project root):
  python -X utf8 backend/src/research/opportunity_score_gate3.py
  python -m backend.src.research.opportunity_score_gate3
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
OOS_YEARS = ("2025",)  # ĐÃ NHIỄM — descriptive only
N_RANDOM_SEEDS = 30
RNG = np.random.default_rng(42)

# PASS features từ Gate 2 (feature-level evidence). sign: chiều rank "tốt hơn".
# 'neg' → giá trị THẤP tốt hơn (valuation rẻ); 'pos' → giá trị CAO tốt hơn.
CLUSTERS = {
    "valuation": {
        "PE_z_ts": "neg",
        "PE_pct": "neg",
        "PB_z_ts": "neg",
        "PS_z_ts": "neg",
        "EV_EBITDA_z_ts": "neg",
        "val_zone_ts": "pos",
    },
    "quality": {"ROE_z_ts": "pos"},
    "behavior": {"ret_120d": "pos"},
}


def _spearman(x, y) -> float | None:
    """Spearman giữa 2 dãy 1D (Series hoặc ndarray), None nếu thiếu / suy biến."""
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
# 1. REDUNDANCY / CLUSTER AUDIT
# ═══════════════════════════════════════════════════════════════════════════

FEATURE_ORDER = [f for cluster in CLUSTERS.values() for f in cluster]


def redundancy_audit(df: pd.DataFrame) -> pd.DataFrame:
    """Pairwise cross-sectional Spearman nội ngày, aggregate mean/median.

    Trả matrix-wide frame các cặp feature (a, b) với:
      mean_corr  : trung bình corr nội ngày
      med_corr   : trung vị corr nội ngày
      pct_abs_gt_8 : % ngày có |corr| > 0.8 (hàng ngày có "cùng một latent"?)
      in_cluster : 2 feature có cùng cluster không
    """
    feats = [f for f in FEATURE_ORDER if f in df.columns]
    if len(feats) < 2:
        raise ValueError("Không đủ feature để audit redundancy")

    # rank percentile nội ngày cho mỗi feature (direction chưa đảo — chỉ đo corr)
    ranked = df.groupby("date")[feats].rank(pct=True)
    ranked["date"] = df["date"].values

    pairs = []
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            a, b = feats[i], feats[j]
            sub = ranked[[a, b, "date"]].dropna()
            per_day = []
            for date, g in sub.groupby("date", sort=True):
                if len(g) < MIN_SYMBOLS_PER_DAY:
                    continue
                rho = _spearman(g[a], g[b])
                if rho is not None:
                    per_day.append(rho)
            if not per_day:
                continue
            arr = np.array(per_day)
            cluster_of = {f: c for c, m in CLUSTERS.items() for f in m}
            pairs.append(
                {
                    "feature_a": a,
                    "feature_b": b,
                    "in_cluster": cluster_of[a] == cluster_of[b],
                    "mean_corr": round(float(arr.mean()), 4),
                    "med_corr": round(float(np.median(arr)), 4),
                    "pct_abs_gt_8": round(float((np.abs(arr) > 0.8).mean() * 100), 1),
                    "n_days": len(arr),
                }
            )
    return pd.DataFrame(pairs).sort_values("mean_corr", key=lambda s: s.abs(), ascending=False)


# ═══════════════════════════════════════════════════════════════════════════
# 2. SCORE CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════


def _rank_within_day(df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    """Rank percentile nội ngày cho từng feature, sign-normalized.

    'neg' → rank của giá trị NGHỊCH ĐẢO (rẻ = rank cao = "tốt hơn").
    'pos' → rank trực tiếp.
    Trả frame chỉ chứa các cột rank (cùng index như df).
    """
    out = {}
    for feat, sign in cols.items():
        if feat not in df.columns:
            continue
        v = df[feat] * (-1.0) if sign == "neg" else df[feat]
        r = v.groupby(df["date"]).rank(pct=True)
        out[f"__r_{feat}"] = r
    return pd.DataFrame(out, index=df.index)


def cluster_score(df: pd.DataFrame, cluster_name: str) -> pd.Series:
    """Available-feature normalized cluster score (KHÔNG quy missing về 0).

    score = mean( rank_within_day(x_i) | x_i available trong ngày đó ).
    """
    rk = _rank_within_day(df, CLUSTERS[cluster_name])
    rk["__n_avail"] = rk.notna().sum(axis=1)
    with np.errstate(invalid="ignore"):
        score = rk.sum(axis=1, min_count=1) / rk["__n_avail"]
    score[rk["__n_avail"] == 0] = np.nan
    return score


def build_scores(df: pd.DataFrame, ic_weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Tính các score column trên df (đã có các feature).

    ic_weights: dict feature → w (từ training window). Nếu None → bỏ M_IC.
    Trả df COPY với thêm cột: M_value, M_rankavg, M_random, (M_IC nếu weights).
    M_p_gain = rank(p_gain) nội ngày — control.
    """
    out = df.copy()

    val = cluster_score(out, "valuation")
    out["M_value"] = val

    # M_rankavg: trung bình rank của các feature PASS (valuation + ROE + ret_120d)
    feats_avg = {}
    for cluster in CLUSTERS.values():
        feats_avg.update(cluster)
    out["M_rankavg"] = _rank_within_day(out, feats_avg).mean(axis=1)

    # M_random: rank ngẫu nhiên nội ngày (noise floor)
    out["M_random"] = np.nan
    for _ in range(N_RANDOM_SEEDS):
        rnd = pd.Series(RNG.random(len(out)), index=out.index)
        out["M_random"] = out["M_random"].fillna(rnd.groupby(out["date"]).rank(pct=True))

    # M_p_gain: control (failed ở Gate 1) — rank trực tiếp
    if "p_gain" in out.columns:
        out["M_p_gain"] = out["p_gain"].groupby(out["date"]).rank(pct=True)
    else:
        out["M_p_gain"] = np.nan

    if ic_weights:
        sign_of = {f: s for c in CLUSTERS.values() for f, s in c.items()}
        feats_w = [f for f in ic_weights if f in out.columns and f in sign_of]
        cols = {f: sign_of[f] for f in feats_w}
        rk = _rank_within_day(out, cols)
        # available-feature weighted normalization: w_sum_i * r_i / sum(w_i | i available)
        # KHÔNG quy missing về 0; NaN của 1 feature không kéo NaN cả score.
        num = None
        den = np.zeros(len(rk))
        for f in feats_w:
            c = ic_weights[f] * rk[f"__r_{f}"]
            num = c if num is None else num.add(c, fill_value=0.0)
            den += ic_weights[f] * rk[f"__r_{f}"].notna().astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            out["M_IC"] = np.where(den > 0, num / den, np.nan)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 3. MODEL DIAGNOSTICS (một pass duy nhất qua các ngày)
# ═══════════════════════════════════════════════════════════════════════════


def diagnose_score(df: pd.DataFrame, score: str, target: str) -> dict:
    """Daily RankIC + top-decile spread + quintile mono cho MỘT score column.

    Trả dict các metric pooled + per-year + per-regime + IS/OOS.
    """
    sub = df.dropna(subset=[score, target])
    n_obs = int(len(sub))
    if n_obs < MIN_SYMBOLS_PER_DAY:
        return {"score": score, "target": target, "n_obs": 0, "error": "insufficient"}

    rows = []
    for date, g in sub.groupby("date", sort=True):
        n = len(g)
        if n < MIN_SYMBOLS_PER_DAY:
            continue
        r = g[score].rank(method="first")
        rho = _spearman(g[score], g[target])
        top = g.loc[r > n * 0.9, target]
        bot = g.loc[r <= n * 0.1, target]
        spread = float(top.mean() - bot.mean()) if len(top) >= 2 and len(bot) >= 2 else None
        if n >= MIN_SYMBOLS_PER_DAY * 2:
            bucket = np.floor((r - 1) / n * 5).clip(0, 4)
            bm = g.groupby(bucket)[target].mean()
            mono = _spearman(np.arange(len(bm)), bm.values) if len(bm) >= 3 else None
        else:
            mono = None
        rows.append(
            {
                "date": date,
                "year": str(date)[:4],
                "regime": g["regime"].iloc[0] if "regime" in g.columns and g["regime"].notna().any() else "UNKNOWN",
                "n": n,
                "ic": rho,
                "spread": spread,
                "mono": mono,
            }
        )
    if not rows:
        return {"score": score, "target": target, "n_obs": n_obs, "error": "no_daily_ic"}
    day = pd.DataFrame(rows)

    ics = day.dropna(subset=["ic"])
    if ics.empty:
        return {"score": score, "target": target, "n_obs": n_obs, "error": "no_daily_ic"}
    pooled = float(ics["ic"].mean())
    n_days = len(ics)
    std = float(ics["ic"].std(ddof=1))
    t_stat = pooled / (std / math.sqrt(n_days)) if std > 0 else 0.0
    pct_pos = float((ics["ic"] > 0).mean())

    per_year = {str(y): round(float(g["ic"].mean()), 4) for y, g in ics.groupby("year") if len(g) >= 10}
    per_regime = {str(r): round(float(g["ic"].mean()), 4) for r, g in ics.groupby("regime") if len(g) >= 10}

    is_ics = ics[ics["year"].isin(IS_YEARS)]
    oos_ics = ics[ics["year"].isin(OOS_YEARS)]
    ic_is = round(float(is_ics["ic"].mean()), 4) if len(is_ics) >= 10 else None
    ic_oos = round(float(oos_ics["ic"].mean()), 4) if len(oos_ics) >= 10 else None

    spreads = day.dropna(subset=["spread"])["spread"]
    monos = day.dropna(subset=["mono"])["mono"]
    return {
        "score": score,
        "target": target,
        "n_obs": n_obs,
        "n_days": n_days,
        "pooled_ic": round(pooled, 4),
        "ic_tstat": round(t_stat, 2),
        "pct_days_pos": round(pct_pos * 100, 1),
        "per_year": per_year,
        "per_regime": per_regime,
        "ic_IS_2022_24": ic_is,
        "ic_OOS_2025_desc": ic_oos,
        "top_decile_spread_pct": round(float(spreads.mean() * 100), 2) if len(spreads) else None,
        "top_decile_pct_pos": round(float((spreads > 0).mean() * 100), 0) if len(spreads) else None,
        "mono_mean": round(float(monos.mean()), 3) if len(monos) else None,
        "mono_pct_pos": round(float((monos > 0).mean() * 100), 0) if len(monos) else None,
    }


def estimate_ic_weights(df: pd.DataFrame, train_years: list[str]) -> dict[str, float]:
    """w_k = IC_k / sum|IC_k| với IC_k = mean daily RankIC trên training window.

    IC ước lượng trên RANK ĐÃ sign-normalize (cùng hướng với scoring):
    feature 'neg' → rank(-x) — nên IC kỳ vọng DƯƠNG cho feature PASS. Mọi
    feature PASS sẽ có w > 0. CHỈ estimate trên train (walk-forward).
    """
    train = df[df["year"].astype(str).isin(train_years)]
    sign_of = {f: s for c in CLUSTERS.values() for f, s in c.items()}
    ics = {}
    for feat in FEATURE_ORDER:
        if feat not in train.columns:
            continue
        v = train[feat] * (-1.0) if sign_of[feat] == "neg" else train[feat]
        rows = []
        for date, g in train.groupby("date"):
            g = g.dropna(subset=[feat, "r20"])
            if len(g) < MIN_SYMBOLS_PER_DAY:
                continue
            rho = _spearman(v.loc[g.index], g["r20"])
            if rho is not None:
                rows.append(rho)
        if rows:
            ics[feat] = float(np.mean(rows))
    total = sum(abs(v) for v in ics.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in ics.items()}


# ═══════════════════════════════════════════════════════════════════════════
# 4. MAIN
# ═══════════════════════════════════════════════════════════════════════════

MODELS = ("M_random", "M_p_gain", "M_value", "M_rankavg")


def run_walk_forward(df: pd.DataFrame) -> list[dict]:
    """M_IC walk-forward: train expanding (2022→2023, 2022-23→2024, 2022-24→2025).

    2025 = descriptive (ĐÃ NHIỄM). Trả các dict diagnostic cho từng test year.
    """
    out_rows = []
    for test_year in YEARS[1:]:
        train_years = list(YEARS[: YEARS.index(test_year)])
        weights = estimate_ic_weights(df, train_years)
        if not weights:
            continue
        scored = build_scores(df, ic_weights=weights)
        test = scored[scored["year"].astype(str) == test_year]
        d = diagnose_score(test, "M_IC", "r20")
        d["method"] = "walk-forward M_IC"
        d["test_year"] = test_year
        d["train_years"] = "+".join(train_years)
        d["weights"] = {k: round(v, 3) for k, v in sorted(weights.items(), key=lambda kv: -abs(kv[1]))}
        out_rows.append(d)
    return out_rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 3A — score construction & walk-forward audit (READ-ONLY)")
    ap.add_argument("--pool", choices=("all", "deploy"), default="all")
    ap.add_argument("--replay-dir", default="replay_58_ts")
    ap.add_argument("--prefix", default="replay_58")
    args = ap.parse_args()

    pool = load_pool(REPLAY_DIR, prefix=args.prefix, pool=args.pool)
    print(f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} dates={pool['date'].nunique()}")
    df = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(df)} cols={len(df.columns)}")

    # ── 1. Redundancy audit ────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  1) REDUNDANCY AUDIT — pairwise cross-sectional Spearman nội ngày")
    print("     corr ≈ ±1 → cùng latent variable → đếm 1 lần, không rank-average hết")
    print("=" * 132)
    red = redundancy_audit(df)
    print(
        f"{'feature_a':<16}{'feature_b':<16}{'in_cluster':<11}{'mean_corr':>10}"
        f"{'med_corr':>10}{'pct|corr|>0.8':>14}{'n_days':>8}"
    )
    print("-" * 132)
    for _, r in red.iterrows():
        print(
            f"{r['feature_a']:<16}{r['feature_b']:<16}{str(r['in_cluster']):<11}"
            f"{r['mean_corr']:>10.3f}{r['med_corr']:>10.3f}{r['pct_abs_gt_8']:>14.1f}{r['n_days']:>8}"
        )

    # ── 2. Score construction (no weights) ─────────────────────────────────
    scored = build_scores(df)
    print(
        f"\n[score] M_value n={scored['M_value'].notna().sum():,}  "
        f"M_rankavg n={scored['M_rankavg'].notna().sum():,}  "
        f"M_random n={scored['M_random'].notna().sum():,}"
    )

    # ── 3. Model diagnostics ───────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  3) MODEL DIAGNOSTICS — daily RankIC / decile spread / quintile mono")
    print("     IS=2022-2024 (evidence) | OOS=2025 (DESCRIPTIVE, ĐÃ NHIỄM)")
    print("=" * 132)
    all_rows = []
    for horizon in HORIZONS:
        target = f"r{horizon}"
        print(f"\n### Horizon H{horizon}  (Y = R(t→t+{horizon}))")
        print(
            f"{'model':<11}{'n':>7}{'IC':>8}{'tstat':>8}{'%pos':>6}{'IS_IC':>8}"
            f"{'OOS_IC(desc)':>14}{'topDecile%':>11}{'mono':>8}"
        )
        print("-" * 132)
        for model in MODELS:
            d = diagnose_score(scored, model, target)
            all_rows.append(d)
            if d.get("error"):
                print(f"{model:<11}{d['n_obs']:>7}  {d['error']}")
                continue
            ic_oos = f"{d['ic_OOS_2025_desc']:+.3f}" if d["ic_OOS_2025_desc"] is not None else "   -"
            spread = f"{d['top_decile_spread_pct']:+.2f}" if d["top_decile_spread_pct"] is not None else "   -"
            mono = f"{d['mono_mean']:+.3f}" if d["mono_mean"] is not None else "  -"
            print(
                f"{model:<11}{d['n_obs']:>7}{d['pooled_ic']:>+8.3f}{d['ic_tstat']:>8.1f}"
                f"{d['pct_days_pos']:>6.0f}{d['ic_IS_2022_24']:>+8.3f}{ic_oos:>14}"
                f"{spread:>11}{mono:>8}"
            )

    # ── 4. Walk-forward M_IC ───────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  4) WALK-FORWARD M_IC — weights CHỈ từ training window, test OOS")
    print("     2025 = descriptive (ĐÃ NHIỄM — không chọn feature/weight bằng nó)")
    print("=" * 132)
    wf = run_walk_forward(df)
    for d in wf:
        if d.get("error") or d.get("pooled_ic") is None:
            print(f"  [{d['test_year']}] train={d['train_years']}  ERROR: {d.get('error')}")
            all_rows.append({**d, "score": "M_IC", "target": "r20"})
            continue
        spread = d["top_decile_spread_pct"]
        mono = d["mono_mean"]
        spread_s = f"{spread:+.2f}" if spread is not None else "    -"
        mono_s = f"{mono:+.3f}" if mono is not None else "    -"
        print(
            f"  [{d['test_year']}] train={d['train_years']}  IC={d['pooled_ic']:+.3f} "
            f"tstat={d['ic_tstat']:.1f} spread={spread_s} "
            f"mono={mono_s}  w={d['weights']}"
        )
        all_rows.append({**d, "score": "M_IC", "target": "r20"})

    # ── Save ───────────────────────────────────────────────────────────────
    result = pd.DataFrame(all_rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_score_gate3_audit_{args.pool}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    red.to_csv(out_dir / f"opportunity_redundancy_audit_{args.pool}.csv", index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")
    print(f"  Saved: {out_dir / f'opportunity_redundancy_audit_{args.pool}.csv'}")

    print("\n" + "=" * 132)
    print("  LƯU Ý GUARDRAIL:")
    print("  - Đây là score-level evidence, KHÔNG phải trading alpha.")
    print("  - M_IC weights chỉ estimate trên training window (walk-forward).")
    print("  - 2025 = descriptive (contaminated) — không dùng chọn feature/weight.")
    print("  - Chưa đưa score vào Governor / Selection Layer / budget ≤20.")
    print("=" * 132)


if __name__ == "__main__":
    main()
