"""
Go/No-Go Gate — Information Diagnostic
─────────────────────────────────────────
Cost: 0 trials.  No parameter search.  No production changes.

Question: can existing features discriminate ANY simple market event?

Targets:
  A) Forward-20d max drawdown >= 5.0%  (downside hazard)
  B) Forward-10d return >= +2.0%       (upside thrust)

Method: Logistic Regression, Purged K-Fold CV (K=5, embargo 10d),
        AUC-ROC + permutation p-value (n_perm=100).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)

DB_PATH = pathlib.Path("backend/data/screener_cache.db")
LEDGER_PATH = pathlib.Path("backend/src/ingestion/ledger/ingestion-ledger.jsonl")

# ── Feature definitions ──────────────────────────────────────────────
# (variable, source_table, computation)
MACRO_VARS = ["US10Y", "DXY", "USD_CNY", "GOLD_XAU", "VIX"]


def load_vni_close(con: sqlite3.Connection) -> pd.Series:
    df = pd.read_sql(
        "SELECT date, close FROM daily_ohlcv WHERE symbol = 'VNINDEX' ORDER BY date",
        con,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].sort_index()


def load_breadth(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT date, breadth_pct, ad_ratio FROM market_wide_breadth_daily "
        "WHERE label = 'ALL_EXCHANGES_UNWEIGHTED' ORDER BY date",
        con,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def load_macro_long(con: sqlite3.Connection, variables: list[str]) -> pd.DataFrame:
    """Load macro_history in long format, pivot to wide."""
    placeholders = ",".join(["?"] * len(variables))
    df = pd.read_sql(
        f"SELECT date, variable, value FROM macro_history WHERE variable IN ({placeholders}) AND is_stale = 0 ORDER BY date",
        con,
        params=variables,
    )
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot_table(index="date", columns="variable", values="value", aggfunc="last")
    return wide.sort_index()


def build_features(vni: pd.Series, breadth: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """Merge all data, compute derived features."""
    # Forward returns and drawdowns
    vni_df = vni.to_frame("vni_close")
    vni_df["fwd_ret_10"] = vni_df["vni_close"].pct_change(10).shift(-10)
    vni_df["fwd_ret_20"] = vni_df["vni_close"].pct_change(20).shift(-20)

    # Forward 20-day max drawdown
    fwd_dd_20 = pd.Series(np.nan, index=vni.index, name="fwd_dd_20")
    closes = vni.values
    for i in range(len(closes) - 20):
        window = closes[i + 1 : i + 21]
        peak = closes[i]
        dd = (window.min() - peak) / peak
        fwd_dd_20.iloc[i] = dd
    vni_df["fwd_dd_20"] = fwd_dd_20

    # Merge breadth
    merged = vni_df.join(breadth, how="inner")

    # Merge macro
    merged = merged.join(macro, how="inner")

    # Derived features
    if "US10Y" in merged.columns:
        merged["US10Y_delta20"] = merged["US10Y"] - merged["US10Y"].shift(20)
    if "DXY" in merged.columns:
        merged["DXY_roc20"] = merged["DXY"].pct_change(20)
    if "USD_CNY" in merged.columns:
        merged["USD_CNY_roc20"] = merged["USD_CNY"].pct_change(20)
    if "GOLD_XAU" in merged.columns:
        merged["GOLD_roc20"] = merged["GOLD_XAU"].pct_change(20)

    return merged


# ── Purged K-Fold ────────────────────────────────────────────────────
def purged_kfold(n: int, k: int = 5, embargo: int = 10):
    """Generate purged K-Fold splits. Embargo = 10 sessions after each test fold."""
    fold_size = n // k
    indices = np.arange(n)
    splits = []
    for i in range(k):
        test_start = i * fold_size
        test_end = min((i + 1) * fold_size, n)
        test_idx = indices[test_start:test_end]

        embargo_end = min(test_end + embargo, n)

        train_mask = np.ones(n, dtype=bool)
        train_mask[test_start:embargo_end] = False
        train_idx = indices[train_mask]

        splits.append((train_idx, test_idx))
    return splits


def evaluate_feature_set(
    X: np.ndarray,
    y: np.ndarray,
    n_perm: int = 100,
    seed: int = 42,
) -> dict:
    """Run purged K-fold CV, compute AUC + permutation p-value."""
    rng = np.random.RandomState(seed)
    splits = purged_kfold(len(X), k=5, embargo=10)

    # Actual AUC
    y_true_all = []
    y_prob_all = []

    for train_idx, test_idx in splits:
        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        clf = LogisticRegression(max_iter=500, C=0.5, random_state=42)
        clf.fit(X_tr_s, y_tr)
        probs = clf.predict_proba(X_te_s)[:, 1]

        y_true_all.extend(y_te)
        y_prob_all.extend(probs)

    y_true_all = np.array(y_true_all)
    y_prob_all = np.array(y_prob_all)

    auc_real = roc_auc_score(y_true_all, y_prob_all)

    # Permutation test
    auc_perm = []
    for _ in range(n_perm):
        y_perm = rng.permutation(y_true_all)
        try:
            auc_perm.append(roc_auc_score(y_perm, y_prob_all))
        except ValueError:
            auc_perm.append(0.5)

    auc_perm = np.array(auc_perm)
    p_value = float(np.mean(auc_perm >= auc_real))

    return {
        "n": int(len(y)),
        "prevalence": float(y.mean()),
        "auc_real": round(float(auc_real), 4),
        "auc_perm_mean": round(float(auc_perm.mean()), 4),
        "auc_perm_std": round(float(auc_perm.std()), 4),
        "p_value": round(float(p_value), 4),
    }


# ── Main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Go/No-Go Gate")
    parser.add_argument("--out", type=str, default=None, help="JSON output path")
    a = parser.parse_args()

    con = sqlite3.connect(str(DB_PATH))

    # Load data
    vni = load_vni_close(con)
    breadth = load_breadth(con)
    macro = load_macro_long(con, MACRO_VARS)
    con.close()

    # Build feature matrix
    df = build_features(vni, breadth, macro)

    # Select clean feature columns
    feature_cols = []
    for c in [
        "US10Y_delta20",
        "US10Y",
        "DXY_roc20",
        "USD_CNY_roc20",
        "GOLD_roc20",
        "VIX",
        "breadth_pct",
        "ad_ratio",
    ]:
        if c in df.columns:
            feature_cols.append(c)

    print(f"Features: {feature_cols}")
    print(f"Rows after merge: {len(df)}")

    # Drop NaN rows for features + targets
    valid = df[feature_cols + ["fwd_dd_20", "fwd_ret_10"]].dropna()
    print(f"Rows after dropna: {len(valid)}")

    X = valid[feature_cols].values

    # Target A: forward 20d drawdown >= 5%
    y_down = (valid["fwd_dd_20"].values <= -0.05).astype(int)

    # Target B: forward 10d return >= 2%
    y_up = (valid["fwd_ret_10"].values >= 0.02).astype(int)

    print("\n=== Target A: Fwd20d DD >= 5% ===")
    print(f"  prevalence: {y_down.mean():.3f} ({y_down.sum()}/{len(y_down)})")
    res_a = evaluate_feature_set(X, y_down)
    print(f"  AUC-ROC:    {res_a['auc_real']:.4f}")
    print(f"  Perm mean:  {res_a['auc_perm_mean']:.4f} ± {res_a['auc_perm_std']:.4f}")
    print(f"  p-value:    {res_a['p_value']:.4f}")

    print("\n=== Target B: Fwd10d Ret >= +2% ===")
    print(f"  prevalence: {y_up.mean():.3f} ({y_up.sum()}/{len(y_up)})")
    res_b = evaluate_feature_set(X, y_up)
    print(f"  AUC-ROC:    {res_b['auc_real']:.4f}")
    print(f"  Perm mean:  {res_b['auc_perm_mean']:.4f} ± {res_b['auc_perm_std']:.4f}")
    print(f"  p-value:    {res_b['p_value']:.4f}")

    # Verdict
    auc_a = res_a["auc_real"]
    auc_b = res_b["auc_real"]
    p_a = res_a["p_value"]
    p_b = res_b["p_value"]

    if auc_a <= 0.53 and auc_b <= 0.53 and p_a > 0.05 and p_b > 0.05:
        verdict = "NO_GO"
        verdict_detail = (
            "AUC <= 0.53 on both targets. "
            "Existing macro features have NO discrimination. "
            "KILL macro prediction hypothesis for this feature set."
        )
    elif auc_a >= 0.58 and auc_b >= 0.60 and p_a < 0.01 and p_b < 0.01:
        verdict = "GO_FULL"
        verdict_detail = "AUC >= 0.58(A) / 0.60(B), p < 0.01 on both. Features have real discrimination. Proceed to design."
    elif auc_a >= 0.58 and p_a < 0.05:
        verdict = "GO_RESTRICTED"
        verdict_detail = (
            f"AUC(A)={auc_a:.4f} >= 0.58, p={p_a:.4f} < 0.05. "
            "Macro useful for risk-sentinel (downside only). "
            "No buy signal capability."
        )
    else:
        verdict = "MARGINAL"
        verdict_detail = (
            f"AUC(A)={auc_a:.4f}, AUC(B)={auc_b:.4f}. "
            "Not strong enough for GO, not weak enough for NO-GO. "
            "Further analysis required."
        )

    print(f"\n{'=' * 50}")
    print(f"VERDICT: {verdict}")
    print(f"  {verdict_detail}")
    print(f"{'=' * 50}")

    result = {
        "event": "gonogo_gate_evaluated",
        "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "features": feature_cols,
        "n_rows": int(len(valid)),
        "target_a_downside_dd5pct": res_a,
        "target_b_upside_thrust2pct": res_b,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "trial_counted": False,
        "governor_changes": 0,
    }

    # Write to ledger
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"\nLedger written to {LEDGER_PATH}")

    if a.out:
        out_path = pathlib.Path(a.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
