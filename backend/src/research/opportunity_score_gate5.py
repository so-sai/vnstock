"""opportunity_score_gate5.py — READ-ONLY selection alpha: top-K vs rejected.

Gate 5 câu hỏi chính:
    Nếu lấy đúng K mã tốt nhất TRONG CÙNG NGÀY theo M_value, chúng có thực sự
    tốt hơn phần còn lại không? (feature alpha → SELECTION alpha?)

Không áp budget ≤20, không threshold, không đưa vào Governor/Selection Layer.

Protocol:
  1. M_value_all  : toàn bộ valuation features (PE_z, PE_pct, PB_z, PS_z,
                    EV_EBITDA_z, val_zone) — available-feature normalization.
  2. M_value_dedup: loại PE_pct (redundant với PE_z_ts, corr 0.90 Gate 3A) —
                    sensitivity: representation có robust không?
  3. TOP-K vs REJECTED, K = 1, 3, 5, 10 — per-day cross-sectional rank:
       - top-K mean forward return (r20) vs phần còn lại trong ngày
       - spread top-K − rest, % ngày spread>0
       - WinRate (top-K có r20>0), MeanR20, PF (profit factor tổng)
       - monotonicity quintile/decile nội ngày
       - per-year (2022-24 IS, 2025 descriptive OOS), per-regime
  4. So sánh baselines: random top-K (noise floor), p_gain top-K (failed control),
     M_value (main).

READ-ONLY. Usage:
  python -X utf8 backend/src/research/opportunity_score_gate5.py
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

VALUATION_ALL = dict(CLUSTERS["valuation"])  # 6 features
VALUATION_DEDUP = {k: v for k, v in VALUATION_ALL.items() if k != "PE_pct"}  # 5 features
N_RANDOM_SEEDS = 30
RNG = np.random.default_rng(7)


def value_score(df: pd.DataFrame, features: dict[str, str]) -> pd.Series:
    """Available-feature normalized valuation score (đồng bộ với Gate 3A)."""
    rk = _rank_within_day(df, features)
    rk["__n"] = rk.notna().sum(axis=1)
    with np.errstate(invalid="ignore"):
        s = rk.sum(axis=1, min_count=1) / rk["__n"]
    s[rk["__n"] == 0] = np.nan
    return s


def build_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["M_value_all"] = value_score(out, VALUATION_ALL)
    out["M_value_dedup"] = value_score(out, VALUATION_DEDUP)
    out["M_p_gain"] = out["p_gain"].groupby(out["date"]).rank(pct=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# TOP-K vs REJECTED
# ═══════════════════════════════════════════════════════════════════════════


def top_k_stats(df: pd.DataFrame, score: str, target: str, k: int) -> dict:
    """Per-day top-K vs rest. Trả dict aggregate (equal-weight mỗi ngày).

    top   : mean target của K mã rank cao nhất trong ngày
    rest  : mean target của phần còn lại trong ngày
    bottom: mean target của K mã rank thấp nhất (contrast)
    """
    days = []
    for date, g in df.groupby("date", sort=True):
        g = g.dropna(subset=[score, target])
        n = len(g)
        if n < MIN_SYMBOLS_PER_DAY:
            continue
        kk = min(k, n)
        order = g[score].rank(method="first", ascending=False)
        top = g.loc[order <= kk, target]
        rest = g.loc[order > kk, target]
        bot = g.loc[order > (n - kk), target]
        if len(top) < 1 or len(rest) < 1:
            continue
        days.append(
            {
                "date": date,
                "year": str(date)[:4],
                "regime": g["regime"].iloc[0] if g["regime"].notna().any() else "UNKNOWN",
                "n": n,
                "top": float(top.mean()),
                "rest": float(rest.mean()),
                "bottom": float(bot.mean()) if len(bot) >= 1 else float("nan"),
                "spread": float(top.mean() - rest.mean()),
                "win_top": float((top > 0).mean()) if len(top) >= 1 else float("nan"),
            }
        )
    if not days:
        return {"score": score, "target": target, "k": k, "error": "no_days"}
    d = pd.DataFrame(days)
    ics = d.dropna(subset=["spread"])
    if ics.empty:
        return {"score": score, "target": target, "k": k, "error": "no_spread"}

    # profit factor: sum of positive top-k returns / |sum of negative|
    top_all = d["top"].to_numpy()
    gains = top_all[top_all > 0].sum()
    losses = -top_all[top_all < 0].sum()
    pf = gains / losses if losses > 0 else float("inf")

    per_year = {str(y): round(float(g["spread"].mean()), 4) for y, g in d.groupby("year") if len(g) >= 10}
    per_regime = {str(r): round(float(g["spread"].mean()), 4) for r, g in d.groupby("regime") if len(g) >= 10}
    is_d = d[d["year"].isin(IS_YEARS)]
    oos_d = d[d["year"].isin(OOS_YEARS)]
    return {
        "score": score,
        "target": target,
        "k": k,
        "n_days": len(d),
        "mean_top": round(float(d["top"].mean()), 4),
        "mean_rest": round(float(d["rest"].mean()), 4),
        "mean_bottom": round(float(d["bottom"].mean()), 4),
        "mean_spread": round(float(ics["spread"].mean()), 4),
        "pct_days_pos": round(float((ics["spread"] > 0).mean() * 100), 1),
        "win_rate_top": round(float(d["win_top"].mean() * 100), 1),
        "mean_r20_top": round(float(d["top"].mean() * 100), 2),
        "mean_r20_rest": round(float(d["rest"].mean() * 100), 2),
        "pf_top": round(float(pf), 2),
        "spread_IS_2022_24": round(float(is_d["spread"].mean()), 4) if len(is_d) >= 10 else None,
        "spread_OOS_2025": round(float(oos_d["spread"].mean()), 4) if len(oos_d) >= 10 else None,
        "per_year": per_year,
        "per_regime": per_regime,
    }


def monotonic_buckets_score(df: pd.DataFrame, score: str, target: str, n_buckets: int = 5) -> dict:
    """Monotonic bucket nội ngày: Spearman(bucket rank, mean target) trung bình ngày."""
    monos = []
    for date, g in df.groupby("date", sort=True):
        g = g.dropna(subset=[score, target])
        n = len(g)
        if n < MIN_SYMBOLS_PER_DAY:
            continue
        r = g[score].rank(method="first").astype(int)
        bucket = np.floor((r - 1) / n * n_buckets).clip(0, n_buckets - 1)
        bm = g.groupby(bucket)[target].mean()
        if len(bm) < 3:
            continue
        rho = _spearman(np.arange(len(bm)), bm.values)
        if rho is not None:
            monos.append(rho)
    if not monos:
        return {"mean_mono": None, "pct_positive": None}
    arr = np.array(monos)
    return {"mean_mono": round(float(arr.mean()), 3), "pct_positive": round(float((arr > 0).mean() * 100), 1)}


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


def random_top_k(df: pd.DataFrame, target: str, k: int) -> dict:
    """Noise floor: top-K ngẫu nhiên nội ngày, trung bình N_RANDOM_SEEDS lần."""
    spreads = []
    wins = []
    for _ in range(N_RANDOM_SEEDS):
        rnd = pd.Series(RNG.random(len(df)), index=df.index)
        rnd_score = f"__rnd_{_}"

        scored = df[["date", "year", "regime", target]].copy()
        scored[rnd_score] = rnd.groupby(df["date"]).rank(pct=True)
        d = top_k_stats(scored, rnd_score, target, k)
        if d.get("error"):
            continue
        spreads.append(d["mean_spread"])
        wins.append(d["win_rate_top"])
    if not spreads:
        return {"score": "random", "target": target, "k": k, "error": "no_runs"}
    return {
        "score": "random",
        "target": target,
        "k": k,
        "n_days": d["n_days"],
        "mean_top": None,
        "mean_rest": None,
        "mean_bottom": None,
        "mean_spread": round(float(np.mean(spreads)), 4),
        "pct_days_pos": None,
        "win_rate_top": round(float(np.mean(wins)), 1),
        "mean_r20_top": None,
        "mean_r20_rest": None,
        "pf_top": None,
        "spread_IS_2022_24": None,
        "spread_OOS_2025": None,
        "per_year": None,
        "per_regime": None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

K_VALUES = (1, 3, 5, 10)


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 5 — top-K vs rejected (selection alpha) — READ-ONLY")
    ap.add_argument("--pool", choices=("all", "deploy"), default="all")
    ap.add_argument("--prefix", default="replay_58")
    args = ap.parse_args()

    pool = load_pool(REPLAY_DIR, prefix=args.prefix, pool=args.pool)
    print(f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} dates={pool['date'].nunique()}")
    df = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(df)} cols={len(df.columns)}")

    scored = build_scores(df)
    print(
        f"[score] M_value_all n={scored['M_value_all'].notna().sum():,}  "
        f"M_value_dedup n={scored['M_value_dedup'].notna().sum():,}"
    )

    all_rows = []

    # ── 1. Top-K vs rejected (H20 primary) ─────────────────────────────────
    print("\n" + "=" * 132)
    print("  1) TOP-K vs REJECTED — per-day cross-sectional rank (Y = R20)")
    print("     Câu hỏi: K mã tốt nhất trong ngày có tốt hơn phần còn lại không?")
    print("=" * 132)
    for model in ("M_value_all", "M_value_dedup", "M_p_gain", "random"):
        print(f"\n### {model}")
        print(
            f"{'K':>3}{'n_days':>7}{'meanTop%':>9}{'meanRest%':>10}{'spread%':>9}"
            f"{'%days+':>7}{'winTop%':>8}{'PF':>7}{'IS_spread':>11}{'OOS_spread(desc)':>16}{'mono':>8}"
        )
        print("-" * 132)
        for k in K_VALUES:
            if model == "random":
                d = random_top_k(scored, "r20", k)
            else:
                d = top_k_stats(scored, model, "r20", k)
                if not d.get("error"):
                    mono = monotonic_buckets_score(scored, model, "r20")
                    d["mono"] = mono["mean_mono"]
            all_rows.append(d)
            if d.get("error"):
                print(f"{k:>3}{d.get('n_days', 0):>7}  {d['error']}")
                continue
            is_s = f"{d['spread_IS_2022_24']:+.4f}" if d["spread_IS_2022_24"] is not None else "   -"
            oos = f"{d['spread_OOS_2025']:+.4f}" if d["spread_OOS_2025"] is not None else "   -"
            mono = f"{d['mono']:+.3f}" if d.get("mono") is not None else "   -"
            top = f"{d['mean_r20_top']:+.2f}" if d["mean_r20_top"] is not None else "    -"
            rest = f"{d['mean_r20_rest']:+.2f}" if d["mean_r20_rest"] is not None else "    -"
            sp = f"{d['mean_spread']:+.3f}" if d["mean_spread"] is not None else "    -"
            pf = f"{d['pf_top']:.2f}" if d["pf_top"] is not None else "  -"
            wr = f"{d['win_rate_top']:.0f}" if d["win_rate_top"] is not None else " -"
            pos = f"{d['pct_days_pos']:.0f}" if d["pct_days_pos"] is not None else " -"
            print(f"{k:>3}{d['n_days']:>7}{top:>9}{rest:>10}{sp:>9}{pos:>7}{wr:>8}{pf:>7}{is_s:>11}{oos:>16}{mono:>8}")

    # ── 2. Horizons + year/regime detail (M_value_all, K=5) ────────────────
    print("\n" + "=" * 132)
    print("  2) M_value_all K=5 theo horizon + year/regime (IS=2022-24, OOS=2025 desc)")
    print("=" * 132)
    for horizon in HORIZONS:
        target = f"r{horizon}"
        d = top_k_stats(scored, "M_value_all", target, 5)
        all_rows.append(d)
        if d.get("error"):
            print(f"  H{horizon}: {d['error']}")
            continue
        is_s = f"{d['spread_IS_2022_24']:+.4f}" if d["spread_IS_2022_24"] is not None else "   -"
        oos = f"{d['spread_OOS_2025']:+.4f}" if d["spread_OOS_2025"] is not None else "   -"
        print(
            f"  H{horizon}: spread={d['mean_spread']:+.4f} (%pos={d['pct_days_pos']:.0f}) "
            f"top={d['mean_r20_top']:+.2f}% rest={d['mean_r20_rest']:+.2f}% "
            f"IS={is_s} OOS={oos}"
        )
        print(f"    per_year : {d['per_year']}")
        print(f"    per_regime: {d['per_regime']}")

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  VERDICT GATE 5 — valuation có selection alpha không?")
    print("=" * 132)
    k5 = top_k_stats(scored, "M_value_all", "r20", 5)
    k5r = random_top_k(scored, "r20", 5)
    k5p = top_k_stats(scored, "M_p_gain", "r20", 5)
    if not k5.get("error") and not k5r.get("error") and not k5p.get("error"):
        print(f"  M_value_all K=5 : spread={k5['mean_spread']:+.4f}  (random floor={k5r['mean_spread']:+.4f})")
        print(f"  p_gain      K=5 : spread={k5p['mean_spread']:+.4f}  (failed control)")
        delta = k5["mean_spread"] - k5r["mean_spread"]
        print(f"  Δ vs random = {delta:+.4f}  → {'YES: selection alpha' if delta > 0.005 else 'không đủ > noise floor'}")
        print(f"  IS spread = {k5['spread_IS_2022_24']:+.4f} | OOS desc = {k5['spread_OOS_2025']:+.4f}")

    result = pd.DataFrame(all_rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_score_gate5_audit_{args.pool}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")

    print("\n" + "=" * 132)
    print("  GUARDRAIL: chưa áp budget ≤20 / threshold / Governor.")
    print("  Nếu top-K KHÔNG beat rest → dừng, điều tra representation, KHÔNG đổ lỗi budget.")
    print("=" * 132)


if __name__ == "__main__":
    main()
