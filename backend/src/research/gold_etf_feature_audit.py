"""gold_etf_feature_audit.py — Gate E1: WGC ETF flows incremental-power audit.

HAI LANE ĐỘC LẬP — KHÔNG trộn evidence:

  Lane A — STRICT PIT (official verdict)
      publication_date = 2026-08-16 (fetch date, operational vintage)
      → 0 obs usable trong bất kỳ backtest lịch sử nào
      → ETF historical predictive power = NOT TESTABLE
      Không impute, không backfill pub date, không đưa Gold v0.2.

  Lane B — LATENT-SIGNAL DIAGNOSTIC (ASSUMPTION-BASED / NON-PIT / RESEARCH ONLY)
      Giả định publication lag M+L (L in {3,7,14,30} ngày) cho mỗi obs month-end.
      Mục đích DUY NHẤT: trả lời "nếu availability gần đúng giả định, ETF flow
      có chứa latent signal hay hoàn toàn vô ích?" → quyết định có săn vintage.
      KHÔNG phải walk-forward chính thức, KHÔNG calibration, KHÔNG production.

Vì M1 v0.1 AUC≈0.5 (negative baseline), KHÔNG đo "M1+ETF vs M1" như incremental.
Thay vào đó đo độc lập:
  1. ETF standalone walk-forward AUC / Spearman vs R_H (OOS)
  2. Sign agreement + top/bottom-decile spread
  3. Stability theo lag (không chọn lag đẹp nhất)
  4. Redundancy: ETF_usd vs ETF_tonnes, regional redundancy, corr M1

Usage (từ backend/):
  python -X utf8 src/research/gold_etf_feature_audit.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ── Sentinel v2.1 (Anchor Fix) ──────────────────────────────────────────────
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
SCREENER_DB = DATA_DIR / "screener_cache.db"
GOLD_H2_DB = DATA_DIR / "gold_h2.db"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

from src.research.gold_forecast_engine_v01 import (
    M1_FEATURES,
    _accuracy,
    build_panel,
    load_macro_series,
    walk_forward,
)

# ── Cấu hình ────────────────────────────────────────────────────────────────
HORIZONS = (20, 60, 120)
LAGS = (3, 7, 14, 30)  # ngày sau month-end (ASSUMED publication lag)
SERIES_ETF = ("ETF_DEMAND_TONNES", "ETF_FLOWS_USD")
REGIONS = ("North America", "Europe", "Asia", "Other")
OUT_CSV = DATA_DIR / "reports" / "gold_etf_e1_latent_signal.csv"


def _connect_h2() -> sqlite3.Connection:
    return sqlite3.connect(str(GOLD_H2_DB))


def _load_usable_panel() -> pd.DataFrame:
    conn = sqlite3.connect(str(SCREENER_DB))
    series_map = load_macro_series(conn)
    conn.close()
    panel = build_panel(series_map).dropna(subset=["GOLD"])
    return panel.dropna(subset=[*M1_FEATURES, "R120"])


def _count_usable_pub(df_rows: pd.DataFrame, max_t: str) -> int:
    """Số vintage ETF có publication_date <= max_t (strict PIT usable)."""
    return int((df_rows["pub"] <= max_t).sum())


def lane_a_pit_evidence() -> dict:
    """Số obs ETF usable dưới strict PIT trong panel walk-forward."""
    conn = _connect_h2()
    rows = conn.execute(
        "SELECT series, entity, observation_date, publication_date FROM gold_h2_series "
        "WHERE series IN ('ETF_DEMAND_TONNES','ETF_FLOWS_USD') "
        "AND publication_date IS NOT NULL"
    ).fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=["series", "entity", "obs", "pub"])
    usable = _load_usable_panel()
    max_t = usable.index.max().date().isoformat()
    usable_pub = _count_usable_pub(df, max_t)
    return {
        "panel_start": usable.index.min().date().isoformat(),
        "panel_end": max_t,
        "panel_days": len(usable),
        "etf_vintage_rows": len(df),
        "etf_rows_pub_le_panel_end": usable_pub,
        "verdict": "NOT TESTABLE",
    }


def _load_etf_values(conn) -> dict:
    """(series, entity) → DataFrame[obs, value] sorted theo obs."""
    out = {}
    for s in SERIES_ETF:
        for e in REGIONS:
            rows = conn.execute(
                "SELECT observation_date, value FROM gold_h2_series "
                "WHERE series=? AND entity=? AND value IS NOT NULL ORDER BY observation_date",
                (s, e),
            ).fetchall()
            if rows:
                out[(s, e)] = pd.DataFrame(rows, columns=["obs", "value"])
    return out


def _step_value(values: pd.DataFrame, dates: pd.DatetimeIndex, lag_days: int) -> np.ndarray:
    """Feature step: value tại t = obs mới nhất có obs+lag <= t (ffill)."""
    obs = pd.to_datetime(values["obs"].values)
    assumed_pub = obs + pd.Timedelta(days=lag_days)
    vals = values["value"].values
    out = np.full(len(dates), np.nan)
    tarr = dates.values
    for i in range(len(assumed_pub)):
        pos = np.searchsorted(tarr, np.datetime64(assumed_pub[i], "ns"))
        out[pos:] = vals[i]
    return out


def build_etf_features(df: pd.DataFrame, etf: dict, lag_days: int) -> pd.DataFrame:
    """Thêm cột ETF cho panel df (mỗi region riêng + global sum)."""
    feats = {}
    for (s, e), vals in etf.items():
        feats[f"{s}__{e}"] = _step_value(vals, df.index, lag_days)
    for s in SERIES_ETF:
        cols = [f"{s}__{e}" for e in REGIONS if (s, e) in etf]
        if len(cols) == 4:
            feats[f"{s}__GLOBAL"] = pd.DataFrame({c: feats[c] for c in cols}).sum(axis=1, min_count=1).values
    return pd.DataFrame(feats, index=df.index)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3:
        return np.nan
    from scipy.stats import spearmanr

    rho, _ = spearmanr(a[m], b[m])
    return float(rho)


def _decile_spread(df: pd.DataFrame, col: str, h: int) -> dict:
    o = df.dropna(subset=[col, f"R{h}"])
    if len(o) < 20:
        return {"n": 0, "spread": np.nan, "top_mean": np.nan, "bottom_mean": np.nan}
    q = o[col].rank(method="first")
    n = len(o)
    top = o[q > 0.9 * n]
    bot = o[q <= 0.1 * n]
    if len(top) < 2 or len(bot) < 2:
        return {"n": len(o), "spread": np.nan, "top_mean": np.nan, "bottom_mean": np.nan}
    return {
        "n": len(o),
        "top_mean": float(top[f"R{h}"].mean()),
        "bottom_mean": float(bot[f"R{h}"].mean()),
        "spread": float(top[f"R{h}"].mean() - bot[f"R{h}"].mean()),
    }


def _sign_agreement(df: pd.DataFrame, col: str, h: int) -> dict:
    o = df.dropna(subset=[col, f"R{h}"])
    if len(o) < 10:
        return {"n": 0, "agreement": np.nan}
    s_feat = np.sign(o[col].values - np.nanmedian(o[col].values))
    s_ret = np.sign(o[f"R{h}"].values)
    return {"n": len(o), "agreement": float((s_feat == s_ret).mean())}


def _redundancy(panel: pd.DataFrame, feats: pd.DataFrame) -> dict:
    """Correlation diagnostics (contemporaneous trên full panel)."""
    out = {}
    for e in REGIONS:
        c_t = f"ETF_DEMAND_TONNES__{e}"
        c_u = f"ETF_FLOWS_USD__{e}"
        if c_t in feats and c_u in feats:
            out[f"spearman_usd_vs_tonnes_{e}"] = _spearman(feats[c_t].values, feats[c_u].values)
    r = []
    for i, a in enumerate(REGIONS):
        for b in REGIONS[i + 1 :]:
            ca, cb = f"ETF_DEMAND_TONNES__{a}", f"ETF_DEMAND_TONNES__{b}"
            if ca in feats and cb in feats:
                r.append(_spearman(feats[ca].values, feats[cb].values))
    out["spearman_tonnes_cross_region_mean"] = float(np.mean(r)) if r else np.nan
    m1_cols = [c for c in ("DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5") if c in panel]
    for c in ("ETF_DEMAND_TONNES__GLOBAL", "ETF_FLOWS_USD__GLOBAL"):
        if c in feats:
            for mc in m1_cols:
                out[f"spearman_{c}_vs_{mc}"] = _spearman(feats[c].values, panel[mc].values)
    return out


def lane_b_latent_signal() -> list[dict]:
    """Diagnostic dưới mọi lag giả định → list rows cho CSV."""
    usable = _load_usable_panel()
    conn = _connect_h2()
    etf = _load_etf_values(conn)
    conn.close()

    rows = []
    for lag in LAGS:
        feats = build_etf_features(usable, etf, lag)
        feats = feats.dropna(axis=1, how="all")
        panel_lag = pd.concat([usable, feats], axis=1)
        for col in feats.columns:
            for h in HORIZONS:
                o = walk_forward(panel_lag, [col], h)
                m = _accuracy(o, h)
                ds = _decile_spread(panel_lag, col, h)
                sa = _sign_agreement(panel_lag, col, h)
                rho = _spearman(panel_lag[col].values, panel_lag[f"R{h}"].values.astype(float))
                rows.append(
                    {
                        "lane": "B_nonpit",
                        "lag_days": lag,
                        "feature": col,
                        "horizon": h,
                        "n_oos": m["n"],
                        "auc": m["auc"],
                        "spearman_vs_Rh": rho,
                        "decile_spread": ds["spread"],
                        "sign_agreement": sa["agreement"],
                    }
                )
        # redundancy ghi 1 lần per lag (đại diện)
        red = _redundancy(usable, feats)
        for k, v in red.items():
            rows.append(
                {
                    "lane": "B_nonpit",
                    "lag_days": lag,
                    "feature": k,
                    "horizon": 0,
                    "n_oos": 0,
                    "auc": np.nan,
                    "spearman_vs_Rh": v,
                    "decile_spread": np.nan,
                    "sign_agreement": np.nan,
                }
            )
    return rows


def main() -> None:
    print("=" * 110)
    print("  GATE E1 — WGC ETF flows: incremental-power audit (2 lanes)")
    print("=" * 110)

    # ── Lane A ──
    ev = lane_a_pit_evidence()
    print("\n  LANE A — STRICT PIT (official)")
    print(f"    panel walk-forward     : {ev['panel_start']} -> {ev['panel_end']} ({ev['panel_days']} ngày)")
    print(f"    ETF vintage rows       : {ev['etf_vintage_rows']}")
    print(f"    ETF rows pub<=panel_end: {ev['etf_rows_pub_le_panel_end']}")
    print(f"    VERDICT                : {ev['verdict']}")

    # ── Lane B ──
    print("\n  LANE B — LATENT-SIGNAL DIAGNOSTIC (ASSUMED PUBLICATION LAG, NON-PIT)")
    print("    NOT FOR PRODUCTION — research only, trả lời 'có đáng săn vintage không'.")
    rows = lane_b_latent_signal()
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")

    # summary AUC theo lag (mean global tonnes/usd, H20)
    df = pd.DataFrame(rows)
    print("\n  AUC summary (ETF_*__GLOBAL, H20, mean across lag):")
    sub = df[(df["feature"].str.endswith("__GLOBAL")) & (df["horizon"] == 20)]
    for lag in LAGS:
        a = sub[(sub["lag_days"] == lag)]["auc"].dropna()
        if len(a):
            print(f"    lag M+{lag:>2d}d  auc={a.mean():.3f}")
    print("\n  Spearman GLOBAL tonnes vs M1 (mean across lag):")
    sel = df[df["feature"].str.startswith("spearman_ETF_DEMAND_TONNES__GLOBAL_vs")]
    for k, v in sel.groupby("feature")["spearman_vs_Rh"].mean().items():
        print(f"    {k}: {v:.3f}")
    print("\n  Lưu ý: không chọn lag đẹp nhất. Chỉ dùng để quyết định vintage acquisition.")


if __name__ == "__main__":
    main()
