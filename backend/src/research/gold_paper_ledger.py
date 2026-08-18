"""gold_paper_ledger.py — Forward Paper Gate cho Gold v0.2/v0.3 (FROZEN model).

Mục đích (quyết định checkpoint d2aa5eb): chứng minh forecast "được ghi TRƯỚC
khi biết outcome" có giá trị ngoài mẫu hay không. Model FROZEN — cùng artifact
v0.2 (direction logistic) + v0.3 (magnitude Ridge), walk-forward PIT-safe, KHÔNG
retrain/tune theo kết quả ledger. Thay model = mở version mới, không sửa ledger.

Mỗi forecast_date lưu: μ̂=E[logR20], P(up), P̂(t+20)=P_t·e^{μ̂}, interval
descriptive (KHÔNG sizing), mature_date (t+20 phiên). Khi mature → điền realized.

Ledger: SQLite `backend/data/gold_paper_ledger.db` table `gold_paper_forecasts`,
append-only. `is_backfill=1` cho các ngày outcome đã biết TRƯỚC khi tạo ledger
(lịch sử OOS); `is_backfill=0` cho forecast mới sau khi panel có thêm ngày mới
(những ngày này outcome chưa xảy ra tại thời điểm ghi). forecast_ts ghi thời điểm
tạo ledger (append-only, không cập nhật lại sau khi biết outcome).

--init   : tạo schema + backfill toàn bộ matured forecast (historical OOS);
--update : thêm forecast cho các ngày mới (pending) + điền realized đã mature;
--report : gate metrics trên matured forecasts (direction/magnitude/calibration/
           bias/spearman/regime/rolling decay) + so với v0.2 / naive / expmean.
(chạy cả 3 nếu không truyền subcommand)

Nguồn dữ liệu: screener_cache.db macro_history (GOLD_XAU + M1) và gold_h2.db
(CB_IFS_z) — panel tới 2026-08-18 sau khi chạy `ptck.py daily-update`.

Usage:
  python -X utf8 backend/src/research/gold_paper_ledger.py [--init|--update|--report]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sqlite3
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
LEDGER_DB = DATA_DIR / "gold_paper_ledger.db"
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
    build_panel,
    load_macro_series,
    walk_forward,
)
from src.research.gold_forecast_engine_v02 import CB_PRIMARY, _brier, _logloss, _spearman
from src.research.gold_gvz_pi_v03c import GVZ_TO_LOG20
from src.research.gold_magnitude_forecast_v03 import (
    H,
    add_log_return_target,
    walk_forward_regression,
)
from src.research.gvz_adapter import CACHE_FILE, load_gvz, pit_align, pit_series

MODEL_VERSION = "v0.2-m1-cb-h20-frozen"
INTERVAL_Z = 1.2816  # 80% descriptive interval (KHÔNG sizing)
# PI GVZ v0.3C FROZEN (commit 7e52546): q fit trên calibration <= 2024-12-31.
PI_Q80 = 1.073
PI_Q90 = 1.507
LEDGER_TABLE = "gold_paper_forecasts"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_vintage TEXT,
    gold_t REAL,
    mu_hat REAL,
    p_up REAL,
    p_hat_20 REAL,
    lo80 REAL,
    hi80 REAL,
    pi80_lower REAL,
    pi80_upper REAL,
    pi90_lower REAL,
    pi90_upper REAL,
    target_p80_low REAL,
    target_p80_high REAL,
    target_p90_low REAL,
    target_p90_high REAL,
    is_covered_80 INTEGER,
    is_covered_90 INTEGER,
    is_backfill INTEGER DEFAULT 0,
    forecast_ts TEXT,
    mature_date TEXT,
    realized_r20 REAL,
    realized_p20 REAL,
    realized_ts TEXT,
    UNIQUE(forecast_date, model_version)
);
"""

# Cột PI GVZ (v0.3C frozen) + target price + settlement — ALTER cho DB cũ.
_PI_COLUMNS = {
    "pi80_lower": "REAL",
    "pi80_upper": "REAL",
    "pi90_lower": "REAL",
    "pi90_upper": "REAL",
    "target_p80_low": "REAL",
    "target_p80_high": "REAL",
    "target_p90_low": "REAL",
    "target_p90_high": "REAL",
    "is_covered_80": "INTEGER",
    "is_covered_90": "INTEGER",
}


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(LEDGER_DB))


def _ensure_pi_columns(conn: sqlite3.Connection) -> None:
    """ALTER TABLE thêm cột PI GVZ cho DB cũ (idempotent)."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({LEDGER_TABLE})")}
    for col, typ in _PI_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE {LEDGER_TABLE} ADD COLUMN {col} {typ}")


def init_ledger() -> None:
    conn = _connect()
    conn.execute(_SCHEMA)
    _ensure_pi_columns(conn)
    conn.commit()
    conn.close()
    print(f"[ledger] schema ready: {LEDGER_DB} ({LEDGER_TABLE})")


def build_paper_panel() -> pd.DataFrame:
    """Panel cho paper: KHÔNG dropna R120 (khác load_v02_panel) → tới 2026-08-18."""
    import sqlite3

    conn = sqlite3.connect(str(SCREENER_DB))
    series = load_macro_series(conn)
    conn.close()
    usable = build_panel(series).dropna(subset=["GOLD"])
    conn_h2 = sqlite3.connect(str(GOLD_H2_DB))
    feats = build_cb_features(usable, conn_h2)
    conn_h2.close()
    return usable.join(feats)


def _extend_forward_reg(panel: pd.DataFrame, features: list[str], h: int) -> pd.DataFrame:
    """Prediction cho các ngày cuối (pending, target chưa realized) bằng model fit
    trên [0, n-h) — PIT-safe, frozen procedure (cùng Ridge alpha=1.0)."""
    from sklearn.linear_model import Ridge

    y = panel[f"logR{h}"]
    X = panel[features].astype(float)
    n = len(panel)
    n_h = n - h
    X_tr = X.iloc[:n_h].dropna()
    y_tr = y.reindex(X_tr.index)
    mask_tr = y_tr.notna() & X_tr.notna().all(axis=1)
    X_tr, y_tr = X_tr[mask_tr], y_tr[mask_tr]
    if X_tr.shape[0] < 100:
        return pd.DataFrame(index=panel.index[n_h:], columns=["pred", "resid_std"])
    clf = Ridge(alpha=1.0)
    clf.fit(X_tr, y_tr)
    resid = y_tr.values - clf.predict(X_tr.values)
    resid_std = float(np.std(resid))
    X_te = X.iloc[n_h:]
    out = pd.DataFrame(index=X_te.index)
    out["pred"] = clf.predict(X_te)
    out["resid_std"] = resid_std
    return out


def _extend_forward_log(panel: pd.DataFrame, features: list[str], h: int) -> pd.DataFrame:
    """P(up) cho pending dates bằng logistic fit trên [0, n-h)."""
    from sklearn.linear_model import LogisticRegression

    y = panel[f"R{h}_pos"]
    X = panel[features].astype(float)
    n = len(panel)
    n_h = n - h
    X_tr = X.iloc[:n_h].dropna()
    y_tr = y.reindex(X_tr.index)
    mask_tr = y_tr.notna() & X_tr.notna().all(axis=1)
    X_tr, y_tr = X_tr[mask_tr], y_tr[mask_tr]
    out = pd.DataFrame(index=panel.index[n_h:])
    out["prob"] = np.nan
    if X_tr.shape[0] < 100 or y_tr.nunique() < 2:
        return out
    clf = LogisticRegression(max_iter=500)
    try:
        clf.fit(X_tr, y_tr)
    except ValueError:
        return out
    X_te = X.iloc[n_h:]
    mask_te = X_te.notna().all(axis=1)
    if mask_te.any():
        out.loc[X_te[mask_te].index, "prob"] = clf.predict_proba(X_te[mask_te])[:, 1]
    return out


def generate_forecasts(panel: pd.DataFrame) -> pd.DataFrame:
    """Toàn bộ forecast OOS + pending (frozen walk-forward), trả DataFrame."""
    features = M1_FEATURES + [CB_PRIMARY]
    panel = add_log_return_target(panel, H)
    reg = walk_forward_regression(panel, features, H)
    log = walk_forward(panel, features, H)
    # pending: tiếp nối bằng model frozen fit [0, n-h)
    reg_ext = _extend_forward_reg(panel, features, H)
    log_ext = _extend_forward_log(panel, features, H)
    reg = pd.concat([reg.dropna(subset=["pred"]), reg_ext])
    log = pd.concat([log.dropna(subset=["prob"]), log_ext])

    # σ_dyn = GVZ PIT (pub = obs + 1 trading day), align ffill vào panel index.
    gvz_pub = pit_series(load_gvz(CACHE_FILE))
    gvz_aligned = pit_align(gvz_pub, panel.index)
    sigma_gvz = (gvz_aligned * GVZ_TO_LOG20).reindex(panel.index)

    out = pd.DataFrame(index=panel.index)
    out["mu_hat"] = reg["pred"]
    out["p_up"] = log["prob"]
    out["resid_std"] = reg["resid_std"]
    out["gold_t"] = panel["GOLD"]
    out["realized_r20"] = panel["R20"]
    out["p_hat_20"] = out["gold_t"] * np.exp(out["mu_hat"])
    out["lo80"] = out["mu_hat"] - INTERVAL_Z * out["resid_std"]
    out["hi80"] = out["mu_hat"] + INTERVAL_Z * out["resid_std"]
    # PI GVZ v0.3C frozen: PI_t = mu_hat ± q * σ_dyn,t
    sig = sigma_gvz.clip(lower=1e-12)
    out["pi80_lower"] = out["mu_hat"] - PI_Q80 * sig
    out["pi80_upper"] = out["mu_hat"] + PI_Q80 * sig
    out["pi90_lower"] = out["mu_hat"] - PI_Q90 * sig
    out["pi90_upper"] = out["mu_hat"] + PI_Q90 * sig
    # Target price-space (về mức giá P̂ ± band) — descriptive, KHÔNG sizing.
    g = out["gold_t"]
    out["target_p80_low"] = g * np.exp(out["pi80_lower"])
    out["target_p80_high"] = g * np.exp(out["pi80_upper"])
    out["target_p90_low"] = g * np.exp(out["pi90_lower"])
    out["target_p90_high"] = g * np.exp(out["pi90_upper"])
    return out.dropna(subset=["mu_hat"])


def _trading_calendar(panel: pd.DataFrame) -> pd.DatetimeIndex:
    return panel.index[panel["GOLD"].notna()]


def sync_ledger(panel: pd.DataFrame, forecasts: pd.DataFrame) -> dict:
    """Insert forecast mới (append-only) + điền realized đã mature. Trả stats."""
    conn = _connect()
    conn.execute(_SCHEMA)
    _ensure_pi_columns(conn)
    cal = _trading_calendar(panel)
    pos = {d: i for i, d in enumerate(cal)}
    now = _dt.datetime.now().isoformat(timespec="seconds")
    inserted = matured = pi_filled = 0
    existing = {
        r[0] for r in conn.execute(f"SELECT forecast_date FROM {LEDGER_TABLE} WHERE model_version=?", (MODEL_VERSION,))
    }
    rows = []
    for date in forecasts.index:
        d = str(date.date())
        if d in existing:
            continue
        i = pos.get(date)
        mature_idx = i + H if i is not None else None
        mature_date = str(cal[mature_idx].date()) if mature_idx is not None and mature_idx < len(cal) else None
        realized_r20 = forecasts.at[date, "realized_r20"]
        is_backfill = 1 if (mature_date is not None and pd.notna(realized_r20)) else 0
        rows.append(
            (
                d,
                MODEL_VERSION,
                str(panel.index.max().date()),
                float(forecasts.at[date, "gold_t"]),
                float(forecasts.at[date, "mu_hat"]),
                None if pd.isna(forecasts.at[date, "p_up"]) else float(forecasts.at[date, "p_up"]),
                float(forecasts.at[date, "p_hat_20"]),
                float(forecasts.at[date, "lo80"]),
                float(forecasts.at[date, "hi80"]),
                float(forecasts.at[date, "pi80_lower"]),
                float(forecasts.at[date, "pi80_upper"]),
                float(forecasts.at[date, "pi90_lower"]),
                float(forecasts.at[date, "pi90_upper"]),
                float(forecasts.at[date, "target_p80_low"]),
                float(forecasts.at[date, "target_p80_high"]),
                float(forecasts.at[date, "target_p90_low"]),
                float(forecasts.at[date, "target_p90_high"]),
                is_backfill,
                now,
                mature_date,
                None if pd.isna(realized_r20) else float(realized_r20),
                None,
                None,
            )
        )
        inserted += 1
    if rows:
        conn.executemany(
            f"INSERT OR IGNORE INTO {LEDGER_TABLE} "
            f"(forecast_date, model_version, feature_vintage, gold_t, mu_hat, p_up, p_hat_20, "
            f"lo80, hi80, pi80_lower, pi80_upper, pi90_lower, pi90_upper, "
            f"target_p80_low, target_p80_high, target_p90_low, target_p90_high, "
            f"is_backfill, forecast_ts, mature_date, realized_r20, realized_p20, realized_ts) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    # backfill PI GVZ cho các rows cũ (chưa có cột trước migration) — PIT-clean,
    # cùng công thức frozen v0.3C, chỉ điền, KHÔNG đụng mu_hat/p_up/forecast_ts.
    if forecasts["pi80_lower"].notna().any():
        for r in conn.execute(
            f"SELECT forecast_date FROM {LEDGER_TABLE} WHERE model_version=? AND pi80_lower IS NULL",
            (MODEL_VERSION,),
        ):
            fd = pd.Timestamp(r[0])
            if fd in forecasts.index and pd.notna(forecasts.at[fd, "pi80_lower"]):
                conn.execute(
                    f"UPDATE {LEDGER_TABLE} SET pi80_lower=?, pi80_upper=?, pi90_lower=?, pi90_upper=?, "
                    f"target_p80_low=?, target_p80_high=?, target_p90_low=?, target_p90_high=? "
                    f"WHERE forecast_date=? AND model_version=?",
                    (
                        float(forecasts.at[fd, "pi80_lower"]),
                        float(forecasts.at[fd, "pi80_upper"]),
                        float(forecasts.at[fd, "pi90_lower"]),
                        float(forecasts.at[fd, "pi90_upper"]),
                        float(forecasts.at[fd, "target_p80_low"]),
                        float(forecasts.at[fd, "target_p80_high"]),
                        float(forecasts.at[fd, "target_p90_low"]),
                        float(forecasts.at[fd, "target_p90_high"]),
                        str(fd.date()),
                        MODEL_VERSION,
                    ),
                )
                pi_filled += 1
    # điền realized_p20 + is_covered_80/90 cho các forecast đã mature (roll over).
    # Settlement: realized_p20 (giá close tại t+20) nằm trong target band price-space.
    cal_dates = {str(d.date()): d for d in cal}
    for r in conn.execute(
        f"SELECT forecast_date, mature_date, target_p80_low, target_p80_high, "
        f"target_p90_low, target_p90_high FROM {LEDGER_TABLE} "
        f"WHERE model_version=? AND realized_r20 IS NOT NULL AND realized_p20 IS NULL",
        (MODEL_VERSION,),
    ):
        fd, md, p80l, p80h, p90l, p90h = r
        if md and md in cal_dates:
            p20 = float(panel["GOLD"].loc[cal_dates[md]])
            cov80 = 1 if (p80l is not None and p80l <= p20 <= p80h) else 0
            cov90 = 1 if (p90l is not None and p90l <= p20 <= p90h) else 0
            if p80l is None:
                cov80 = None
            if p90l is None:
                cov90 = None
            conn.execute(
                f"UPDATE {LEDGER_TABLE} SET realized_p20=?, realized_ts=?, is_covered_80=?, is_covered_90=? "
                f"WHERE forecast_date=? AND model_version=?",
                (p20, now, cov80, cov90, fd, MODEL_VERSION),
            )
            matured += 1
    conn.commit()
    conn.close()
    return {
        "inserted": inserted,
        "matured_filled": matured,
        "pi_filled": pi_filled,
        "total": len(existing) + inserted,
    }


def load_ledger() -> pd.DataFrame:
    conn = _connect()
    df = pd.read_sql_query(
        f"SELECT * FROM {LEDGER_TABLE} WHERE model_version=? ORDER BY forecast_date", conn, params=(MODEL_VERSION,)
    )
    conn.close()
    df["forecast_date"] = pd.to_datetime(df["forecast_date"])
    return df


def _naive_expmean(panel: pd.DataFrame, h: int = H) -> pd.Series:
    """Expanding-mean baseline PIT-safe (v0.3): mean của logR realized."""
    s = add_log_return_target(panel, h)[f"logR{h}"]
    return s.shift(h).expanding(min_periods=20).mean()


def report_gate(panel: pd.DataFrame, forecasts: pd.DataFrame) -> dict:
    ledger = load_ledger()
    matured = ledger.dropna(subset=["realized_r20"])
    matured_fwd = matured[matured["is_backfill"] == 0]
    res = {
        "n_total": len(ledger),
        "n_matured": len(matured),
        "n_backfill": int((matured["is_backfill"] == 1).sum()),
        "n_forward_matured": len(matured_fwd),
        "n_pending": int((ledger["realized_r20"].isna()).sum()),
        "first_date": str(ledger["forecast_date"].min().date()) if len(ledger) else None,
        "last_date": str(ledger["forecast_date"].max().date()) if len(ledger) else None,
    }
    if len(matured) < 5:
        print("[report] chưa đủ matured forecasts để đánh giá.")
        return res

    mu = matured["mu_hat"].values
    r20 = matured["realized_r20"].values
    p = matured["p_up"].values
    direction = (r20 > 0).astype(float)
    valid_p = ~np.isnan(p)
    pv, dv = p[valid_p], direction[valid_p]

    # ── Direction vs v0.2 ──
    acc = float(np.mean((pv >= 0.5) == dv)) if len(pv) else np.nan
    try:
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(dv, pv)) if len(pv) >= 5 and dv.ptp() > 0 else np.nan
    except ValueError:
        auc = np.nan
    base = float(dv.mean()) if len(dv) else np.nan
    brier = _brier(dv, pv)
    logloss = _logloss(dv, pv)

    # ── Magnitude vs naive / expanding mean ──
    naive_err = np.abs(r20)
    pred_err = np.abs(mu - r20)
    expmean = _naive_expmean(panel).reindex(matured["forecast_date"]).values
    exp_err = np.abs(expmean - r20) if np.isfinite(expmean).any() else np.full_like(r20, np.nan)
    mae_m, rmse_m = float(np.mean(pred_err)), float(np.sqrt(np.mean((mu - r20) ** 2)))
    mae_n, rmse_n = float(np.mean(naive_err)), float(np.sqrt(np.mean(naive_err**2)))
    mae_e = float(np.nanmean(exp_err)) if np.isfinite(exp_err).any() else np.nan
    sign_acc = float(np.mean(np.sign(mu) == np.sign(r20))) if np.any(mu != 0) else np.nan
    bias = float(np.mean(mu - r20))
    spearman = _spearman(mu, r20)

    # ── Calibration: P(up) vs realized frequency ──
    bins = [0.0, 0.35, 0.5, 0.65, 1.0]
    cal_rows = []
    if len(pv) >= 10:
        labels = pd.cut(pv, bins=bins, right=False)
        for lab, grp in pd.Series(dv).groupby(labels, observed=False):
            if grp.count() >= 3:
                p_mean = float(np.mean(pv[labels == lab]))
                cal_rows.append({"bin": str(lab), "n": int(grp.count()), "p_mean": p_mean, "freq": float(grp.mean())})

    # ── Rolling decay (20/60 obs) ──
    decay = {}
    if len(pred_err) >= 40:
        w = 20
        first = float(np.mean(pred_err[:w]))
        last = float(np.mean(pred_err[-w:]))
        decay = {"roll20_first_mae": first, "roll20_last_mae": last, "dmae_20": float(last - first)}

    # ── PI GVZ v0.3C coverage (settlement thực tế từ ledger is_covered_*) ──
    pi_cov = {}
    pi_m = matured.dropna(subset=["is_covered_80"])
    if len(pi_m) >= 5:
        pi_cov = {
            "n": int(len(pi_m)),
            "cov80": float(pi_m["is_covered_80"].mean()),
            "cov90": float(pi_m["is_covered_90"].mean()),
            "q80": PI_Q80,
            "q90": PI_Q90,
            "settle_p20": "realized close t+20 vs target_p* band",
        }

    res.update(
        n_dir=len(pv),
        acc=acc,
        auc=auc,
        base_rate=base,
        brier=brier,
        logloss=logloss,
        mae_model=mae_m,
        rmse_model=rmse_m,
        mae_naive=mae_n,
        rmse_naive=rmse_n,
        mae_expmean=mae_e,
        dmae_vs_naive=float(mae_m - mae_n),
        sign_acc=sign_acc,
        bias=bias,
        spearman=spearman,
        calibration=cal_rows,
        decay=decay,
        pi=pi_cov,
        fwd=report_forward(matured_fwd, r20),
    )
    return res


def report_forward(matured_fwd: pd.DataFrame, all_r20: np.ndarray) -> dict:
    """Sub-report cho phần forecast THẬT SỰ forward (is_backfill=0 đã mature)."""
    if len(matured_fwd) < 3:
        return {"n": int(len(matured_fwd)), "note": "chưa đủ, đang chờ mature"}
    mu = matured_fwd["mu_hat"].values
    r20 = matured_fwd["realized_r20"].values
    p = matured_fwd["p_up"].values
    dv = (r20 > 0).astype(float)
    valid = ~np.isnan(p)
    pv, dvv = p[valid], dv[valid]
    try:
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(dvv, pv)) if len(pv) >= 5 and dvv.ptp() > 0 else np.nan
    except ValueError:
        auc = np.nan
    return {
        "n": int(len(matured_fwd)),
        "auc": auc,
        "acc": float(((pv >= 0.5) == dvv).mean()) if len(pv) else np.nan,
        "mae": float(np.mean(np.abs(mu - r20))),
        "rmse": float(np.sqrt(np.mean((mu - r20) ** 2))),
        "bias": float(np.mean(mu - r20)),
        "sign_acc": float(np.mean(np.sign(mu) == np.sign(r20))) if np.any(mu != 0) else np.nan,
        "spearman": _spearman(mu, r20),
    }


def main() -> None:
    global LEDGER_DB
    parser = argparse.ArgumentParser(description="Gold Forward Paper Ledger Engine (FROZEN model)")
    parser.add_argument(
        "action",
        nargs="?",
        default="all",
        choices=["all", "init", "update", "report", "gate"],
        help="Action to perform (default: all = init+update+report; gate = alias report)",
    )
    flag = parser.add_mutually_exclusive_group()
    flag.add_argument("--init", action="store_const", const="init", dest="action")
    flag.add_argument("--update", action="store_const", const="update", dest="action")
    flag.add_argument("--all", action="store_const", const="all", dest="action")
    flag.add_argument("--report", action="store_const", const="report", dest="action")
    flag.add_argument("--gate", action="store_const", const="gate", dest="action")
    parser.add_argument(
        "--db",
        type=Path,
        default=LEDGER_DB,
        help="Path to SQLite ledger database (default: backend/data/gold_paper_ledger.db)",
    )
    args = parser.parse_args()
    if args.db != LEDGER_DB:
        LEDGER_DB = args.db

    sub = args.action
    panel = build_paper_panel()
    print(f"[paper] panel {panel.index.min().date()} -> {panel.index.max().date()} ({len(panel)} ngày)")
    forecasts = generate_forecasts(panel)
    print(f"[paper] forecasts generated: {len(forecasts)} ngày")

    if sub == "gate":
        sub = "report"

    if sub in ("all", "init", "update"):
        stats = sync_ledger(panel, forecasts)
        print(f"[paper] ledger sync: {stats}")

    if sub in ("all", "report"):
        res = report_gate(panel, forecasts)
        print(
            f"\n[paper] ledger: total={res['n_total']} matured={res['n_matured']} "
            f"(backfill={res['n_backfill']}, fwd_matured={res['n_forward_matured']}) pending={res['n_pending']}"
        )
        if res.get("auc") is not None and res["auc"] == res["auc"]:
            print(
                f"[gate] DIRECTION: n={res['n_dir']} acc={res['acc'] * 100:.1f}% auc={res['auc']:.3f} "
                f"brier={res['brier']:.4f} logloss={res['logloss']:.4f} (v0.2 ref auc=0.574)"
            )
            print(
                f"[gate] MAGNITUDE: mae={res['mae_model']:.4f} vs naive {res['mae_naive']:.4f} "
                f"vs expmean {res['mae_expmean']:.4f} | dmae_vs_naive={res['dmae_vs_naive']:+.4f}"
            )
            print(f"[gate] sign_acc={res['sign_acc'] * 100:.1f}% bias={res['bias']:+.4f} spearman={res['spearman']:.3f}")
            if res["decay"]:
                print(
                    f"[gate] rolling20 decay: first={res['decay']['roll20_first_mae']:.4f} "
                    f"last={res['decay']['roll20_last_mae']:.4f} d={res['decay']['dmae_20']:+.4f}"
                )
            for c in res["calibration"]:
                print(f"[gate] cal {c['bin']}: n={c['n']} P={c['p_mean']:.3f} freq={c['freq']:.3f}")
            pi = res.get("pi", {})
            if pi.get("n", 0) >= 5:
                print(
                    f"[gate] PI GVZ v0.3C: n={pi['n']} cov80={pi['cov80']:.3f} "
                    f"cov90={pi['cov90']:.3f} (q80={pi['q80']}, q90={pi['q90']})"
                )
        fwd = res.get("fwd", {})
        if fwd.get("n", 0) >= 3:
            print(f"[gate] FORWARD matured: n={fwd['n']} auc={fwd['auc']} mae={fwd['mae']:.4f} bias={fwd['bias']:+.4f}")
        elif fwd:
            print(f"[gate] FORWARD matured: n={fwd['n']} ({fwd.get('note', '')})")


if __name__ == "__main__":
    main()
