"""gold_forecast_engine_v01.py — READ-ONLY Gold Forecast Engine v0.1 audit.

Research track độc lập (không nằm trong equity pipeline). Trả lời:

    Các "narrative" về USD có incremental predictive power cho vàng
    (H20/H60/H120) sau khi kiểm soát monetary baseline (M1) hay không,
    kiểm định bằng walk-forward time-series, KHÔNG random split.

Tại sao KHÔNG dùng RankIC (khác Equity):
    Equity có 57 mã/ngày -> cross-sectional RankIC. Gold chỉ có 1 observation/
    ngày -> phải dùng time-series predictive framework:
        X_t -> P(R_Gold(t->t+H) > 0),  H in {20, 60, 120}

p_gain lesson (Equity Gate 1): biến nhìn thuyết phục in-sample nhưng FAIL
walk-forward -> FAIL. Gold Engine phải có gate tương đương: nếu biến chỉ dự
báo tốt một giai đoạn thuận lợi nhưng mất power khi walk-forward -> FAIL.

Data audit (macro_history, screener_cache.db, snapshot 2026-08-14):
  - H1 Monetary:  DXY, US10Y, TIP_PRICE (real-yield proxy), US2Y  -> DENSE
  - H3 Fiscal:    TermPremium proxy = US10Y - US2Y               -> proxy only
  - H4 Geo:       WTI_OIL                                       -> proxy only
  - H5 Dedollar:  USD_CNY                                       -> proxy only
  - H2 Reserve:   CB GoldBuying / ETF flows / Reserve div       -> KHONG CO -> đứng ngoài

Baselines:
    M0 = persistence / historical base rate  (P(R>0))
    M1 = monetary-only (DXY z, DXY mom5, US10Y d5, TIP mom5)
    M2 = M1 + term premium
    M3 = M1 + oil
    M4 = M1 + USD_CNY

READ-ONLY: không ghi replay DB / screener / financial_facts. Không sửa
macro ingestion.

Usage (từ project root):
  python -X utf8 backend/src/research/gold_forecast_engine_v01.py
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

HORIZONS = (20, 60, 120)
FEATURE_DEFS = {
    "DXY_z": ("DXY", "zscore_level"),
    "DXY_mom5": ("DXY", "momentum5"),
    "US10Y_d5": ("US10Y", "diff5"),
    "TIP_mom5": ("TIP_PRICE", "momentum5"),
    "TERM_PREMIUM": ("term_premium", "level"),
    "WTI_mom5": ("WTI_OIL", "momentum5"),
    "USDCNY_mom5": ("USD_CNY", "momentum5"),
}

# Monetary-only (H1) — cốt lõi, luôn là baseline điều khiển
M1_FEATURES = ["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5"]
INCREMENTAL_BLOCKS = {
    "M2_term": ["TERM_PREMIUM"],
    "M3_oil": ["WTI_mom5"],
    "M4_cny": ["USDCNY_mom5"],
}

WALK_FORWARD_BLOCK = 20  # refit mỗi block, đánh giá trên block OOS kế tiếp
MIN_TRAIN = 250  # tối thiểu ngày train cho expanding window


def _connect():
    import sqlite3

    conn = sqlite3.connect(str(SCREENER_DB))
    return conn


def load_macro_series(conn) -> dict[str, pd.Series]:
    """Load từ macro_history, dedup theo MAX(rowid) per (date, variable).

    macro_history có duplicate rows (đặc biệt TIP_PRICE 2 giá trị/ngày, GOLD_XAU
    lặp lại cùng giá trị). Giữ bản có rowid lớn nhất (bản ghi cuối) — nhất quán
    với backfill_transmission_pit.fetch_macro_pit.
    """
    rows = conn.execute(
        "SELECT date, variable, value, rowid FROM macro_history WHERE variable IN (?,?,?,?,?,?,?,?)",
        (
            "GOLD_XAU",
            "DXY",
            "US10Y",
            "US2Y",
            "TIP_PRICE",
            "WTI_OIL",
            "USD_CNY",
            "USD_VND",
        ),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["date", "variable", "value", "rowid"])
    df = df.dropna(subset=["value"])
    # dedup: giữ rowid lớn nhất per (variable, date)
    df = df.sort_values("rowid").groupby(["variable", "date"], as_index=False).last()
    series = {}
    for var in df["variable"].unique():
        sub = df[df["variable"] == var].sort_values("date")
        series[var] = pd.Series(sub["value"].values, index=pd.to_datetime(sub["date"].values))
    return series


def build_panel(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Ghép các chuỗi macro theo ngày, tính feature + target forward return."""
    gold = series["GOLD_XAU"]
    idx = gold.index
    dxy = series["DXY"].reindex(idx, method="ffill")
    us10y = series["US10Y"].reindex(idx, method="ffill")
    us2y = series["US2Y"].reindex(idx, method="ffill")
    tip = series["TIP_PRICE"].reindex(idx, method="ffill")
    wti = series["WTI_OIL"].reindex(idx, method="ffill")
    usdcny = series["USD_CNY"].reindex(idx, method="ffill")

    base = pd.DataFrame(
        {
            "GOLD": gold.values,
            "DXY": dxy.values,
            "US10Y": us10y.values,
            "TIP": tip.values,
            "WTI": wti.values,
            "USD_CNY": usdcny.values,
            "TERM_PREMIUM": (us10y - us2y).values,
        },
        index=idx,
    )

    # rolling stats cần >=5 obs; PIT an toàn vì rolling dùng quá khứ
    dxy_s = base["DXY"]
    us10y_s = base["US10Y"]
    tip_s = base["TIP"]
    wti_s = base["WTI"]
    cny_s = base["USD_CNY"]
    df = pd.DataFrame(
        {
            **base,
            "DXY_z": _zscore_ffill(dxy_s).values,
            "DXY_mom5": dxy_s.pct_change(5).values,
            "US10Y_d5": us10y_s.diff(5).values,
            "TIP_mom5": tip_s.pct_change(5).values,
            "WTI_mom5": wti_s.pct_change(5).values,
            "USDCNY_mom5": cny_s.pct_change(5).values,
        },
        index=idx,
    )

    # ── Target forward return ──
    g = df["GOLD"]
    target_cols = {}
    for h in HORIZONS:
        fwd = g.shift(-h) / g - 1.0
        target_cols[f"R{h}"] = fwd
        target_cols[f"R{h}_pos"] = fwd > 0
    df = pd.concat([df, pd.DataFrame(target_cols, index=df.index)], axis=1)
    return df


def _zscore_ffill(s: pd.Series) -> pd.Series:
    """Z-score theo rolling expanding window (PIT, không dùng toàn bộ sample)."""
    mu = s.expanding(min_periods=20).mean()
    sd = s.expanding(min_periods=20).std()
    return (s - mu) / sd.replace(0, np.nan)


def walk_forward(df: pd.DataFrame, features: list[str], h: int) -> pd.DataFrame:
    """Expanding-window walk-forward logistic: train->predict block OOS kế tiếp.

    Trả về DataFrame OOS predictions (chỉ các ngày được predict ngoài train).
    """
    from sklearn.linear_model import LogisticRegression

    y = df[f"R{h}_pos"]
    X = df[features].astype(float)
    out = pd.DataFrame(index=df.index)
    out["y"] = y
    out["pred"] = np.nan
    out["prob"] = np.nan

    n = len(df)
    start = MIN_TRAIN
    while start < n - h:
        end = min(start + WALK_FORWARD_BLOCK, n - h)
        # train: [0, start)
        X_tr = X.iloc[:start].dropna()
        y_tr = y.reindex(X_tr.index)
        mask_tr = y_tr.notna() & X_tr.notna().all(axis=1)
        X_tr = X_tr[mask_tr]
        y_tr = y_tr[mask_tr]
        if X_tr.shape[0] < 100 or y_tr.nunique() < 2:
            start = end
            continue
        # đảm bảo không lookahead: drop các dòng thiếu feature trong train
        clf = LogisticRegression(max_iter=500)
        try:
            clf.fit(X_tr, y_tr)
        except ValueError:
            start = end
            continue
        # predict block OOS: [start, end)
        X_te = X.iloc[start:end]
        mask_te = X_te.notna().all(axis=1)
        if mask_te.any():
            prob = clf.predict_proba(X_te[mask_te])[:, 1]
            out.loc[X_te[mask_te].index, "prob"] = prob
            out.loc[X_te[mask_te].index, "pred"] = (prob >= 0.5).astype(float)
        start = end
    return out


def _accuracy(out: pd.DataFrame, h: int) -> dict:
    """Directional accuracy trên OOS predictions."""
    valid = out.dropna(subset=["prob", "y"])
    if len(valid) == 0:
        return {"n": 0, "acc": np.nan, "base": np.nan, "auc": np.nan}
    acc = float((valid["pred"] == valid["y"]).mean())
    base = float(valid["y"].mean())
    try:
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(valid["y"], valid["prob"]))
    except ValueError:
        auc = np.nan
    return {"n": len(valid), "acc": acc, "base": base, "auc": auc}


def main():
    print("=" * 120)
    print("  GOLD FORECAST ENGINE v0.1 — walk-forward incremental audit")
    print("=" * 120)
    conn = _connect()
    series = load_macro_series(conn)
    conn.close()

    # ── Coverage audit ──
    print("\n  DATA AVAILABILITY AUDIT (macro_history):")
    for var in ("GOLD_XAU", "DXY", "US10Y", "US2Y", "TIP_PRICE", "WTI_OIL", "USD_CNY"):
        s = series.get(var)
        if s is not None and len(s):
            print(f"    {var:<12s} n={len(s):>6d}  {s.index.min().date()} -> {s.index.max().date()}")
        else:
            print(f"    {var:<12s} MISSING")

    df = build_panel(series)
    df = df.dropna(subset=["GOLD"])
    # Loại phần đầu (rolling warmup) và cuối (target H120 chưa đủ)
    usable = df.dropna(subset=["DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5", "R120"])
    print(f"\n  Panel usable: {len(usable)} ngày  ({usable.index.min().date()} -> {usable.index.max().date()})")
    if len(usable) < MIN_TRAIN + 100:
        print("  FAIL: không đủ dữ liệu walk-forward.")
        return

    # ── Walk-forward per model ──
    models = {
        "M1_monetary": M1_FEATURES,
    }
    for name, extra in INCREMENTAL_BLOCKS.items():
        models[name] = M1_FEATURES + extra

    rows = []
    for h in HORIZONS:
        print(f"\n  ── HORIZON H{h} ──")
        results = {}
        for mname, feats in models.items():
            out = walk_forward(usable, feats, h)
            m = _accuracy(out, h)
            results[mname] = m
            if m["n"]:
                d = f"n={m['n']:>4d} acc={m['acc'] * 100:5.1f}% base={m['base'] * 100:5.1f}% auc={m['auc']:.3f}"
                print(f"    {mname:<14s} {d}")
            else:
                print(f"    {mname:<14s} NO OOS PREDICTIONS")
            rows.append(
                {
                    "horizon": h,
                    "model": mname,
                    "n_oos": m["n"],
                    "acc": m["acc"],
                    "base_rate": m["base"],
                    "auc": m["auc"],
                }
            )

        # ── Incremental vs M1 ──
        m1 = results["M1_monetary"]
        if m1["n"]:
            for mname in models:
                if mname == "M1_monetary":
                    continue
                m = results[mname]
                if m["n"] and m1["n"]:
                    d_acc = m["acc"] - m1["acc"]
                    d_auc = np.nan
                    if not np.isnan(m["auc"]) and not np.isnan(m1["auc"]):
                        d_auc = m["auc"] - m1["auc"]
                    auc_s = f"{d_auc:+.3f}" if d_auc == d_auc else "N/A"
                    print(f"    Δ {mname:<14s} acc={d_acc * 100:+.2f}pp  auc={auc_s}")
                    rows.append(
                        {
                            "horizon": h,
                            "model": f"{mname}_vs_M1",
                            "n_oos": m["n"],
                            "delta_acc": d_acc,
                            "delta_auc": d_auc,
                        }
                    )

    # ── Verdict ──
    print("\n" + "=" * 120)
    print("  VERDICT — narrative nào có incremental predictive power OOS?")
    print("=" * 120)
    print("  Lưu ý: accuracy là directional; nếu acc ~ base rate hoặc Δ(M2/M3/M4 − M1) ≤ 0")
    print("  ở mọi horizon -> block đó KHÔNG có bằng chứng incremental (giống p_gain FAIL).")
    print("  OOS 2025-2026 KHÔNG dùng để chọn threshold/feature (đã nhiễm từ các thí nghiệm khác).")
    print("  H2 (Reserve: CB GoldBuying / ETF flows) KHÔNG có data -> đứng ngoài, không mô phỏng.")

    # ── Save ──
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "gold_forecast_engine_v01_audit.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
