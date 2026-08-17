"""gold_cb_feature_audit.py — Gate M2: CB/Reserve-demand incremental-power audit.

Trả lời: sau khi kiểm soát M1 monetary, WGC_CB / IFS_GOLD_RESERVE_CHANGE có
incremental predictive power cho Gold (H20/H60/H120) không?

Quyết định thiết kế (chốt với user):
  - IFS-primary: CB feature = IFS_GOLD_RESERVE_CHANGE PIT-step (full coverage
    2001→2026 trong panel). WGC_CB chỉ phủ 2024-02+ (517/1142 ngày) → KHÔNG làm
    feature riêng trong M2; dùng làm confirmation trên overlap (đã có Gate O1).
  - WGC ≈ IFS là 2 measurement của CÙNG latent reserve-demand phenomenon (O1:
    Pearson 0.91) → KHÔNG cộng 2 series như 2 evidence độc lập.

PIT STRICT (đây là điểm khác biệt so với ETF E1):
  - IFS có vintage ladder thật (nhiều publication_date per obs).
  - Feature value tại t = value của obs có publication_date <= t (latest vintage
    per obs thắng). KHÔNG dùng observation_date <= t (look-ahead).
  - Walk-forward expanding chuẩn (reuse v0.1 protocol) → đây là kiểm định CHÍNH THỨC,
    không phải lag-simulation.

Contamination boundary:
  - IS: 2022-2024. OOS/descriptive: 2025. KHÔNG tuning theo 2025.
  - Không gọi M2 là "de-dollarization alpha" trước khi có residual power sau M1.

Verdict:
  M2 > M1 stable (>=2 horizons, per-year) -> PASS (CB có incremental info)
  M2 ≈ M1                            -> NO-INCREMENTAL-POWER
  M2 > M1 unstable                    -> PROMISING, NOT DEPLOYABLE

Usage (từ backend/):
  python -X utf8 src/research/gold_cb_feature_audit.py
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
IFS_SERIES = "IFS_GOLD_RESERVE_CHANGE"
IFS_ENTITY = "GLOBAL"
OUT_CSV = DATA_DIR / "reports" / "gold_cb_m2_audit.csv"


def _connect_h2() -> sqlite3.Connection:
    return sqlite3.connect(str(GOLD_H2_DB))


def _load_usable_panel() -> pd.DataFrame:
    conn = sqlite3.connect(str(SCREENER_DB))
    series_map = load_macro_series(conn)
    conn.close()
    panel = build_panel(series_map).dropna(subset=["GOLD"])
    return panel.dropna(subset=[*M1_FEATURES, "R120"])


def _pit_latest_step(rows: list[tuple[str, float, str]], dates: pd.DatetimeIndex) -> np.ndarray:
    """PIT step: value tại t = latest vintage per obs có pub <= t.

    rows: [(observation_date, value, publication_date)] — KHÔNG cần sort.
    Efficient incremental: duyệt dates tăng dần, thêm row có pub<=t vào dict
    {obs: (pub, value)}, lấy value của obs mới nhất.
    """
    recs = [(pd.Timestamp(obs), float(val), pd.Timestamp(pub)) for obs, val, pub in rows]
    recs.sort(key=lambda r: (r[2], r[0]))
    out = np.full(len(dates), np.nan)
    known: dict[pd.Timestamp, tuple[pd.Timestamp, float]] = {}
    i = 0
    n_recs = len(recs)
    for j, t in enumerate(dates):
        # thêm mọi rec có pub <= t (recs sorted theo pub)
        while i < n_recs and recs[i][2] <= t:
            obs, val, pub = recs[i]
            cur = known.get(obs)
            if cur is None or pub >= cur[0]:
                known[obs] = (pub, val)
            i += 1
        if known:
            best_obs = max(known)
            out[j] = known[best_obs][1]
    return out


def build_cb_features(df: pd.DataFrame, conn_h2) -> pd.DataFrame:
    """CB features từ IFS PIT-step: level + zscore (expanding, PIT-safe)."""
    rows = conn_h2.execute(
        "SELECT observation_date, value, publication_date FROM gold_h2_series "
        "WHERE series=? AND entity=? AND publication_date IS NOT NULL AND value IS NOT NULL",
        (IFS_SERIES, IFS_ENTITY),
    ).fetchall()
    if not rows:
        raise ValueError("Không có dữ liệu IFS GLOBAL")
    level = _pit_latest_step(rows, df.index)
    s = pd.Series(level, index=df.index)
    # zscore expanding (PIT-safe, không dùng toàn sample)
    mu = s.expanding(min_periods=20).mean()
    sd = s.expanding(min_periods=20).std()
    z = (s - mu) / sd.replace(0, np.nan)
    feats = pd.DataFrame(
        {"CB_IFS_level": level, "CB_IFS_z": z.values},
        index=df.index,
    )
    # 3m rolling sum (PIT-safe, trailing window)
    feats["CB_IFS_sum3"] = s.rolling(3, min_periods=1).sum().values
    return feats


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3:
        return np.nan
    from scipy.stats import spearmanr

    rho, _ = spearmanr(a[m], b[m])
    return float(rho)


def _redundancy_cb_m1(panel: pd.DataFrame, feats: pd.DataFrame) -> dict:
    """CB feature có information độc lập với M1 không? (corr với từng M1 feat)."""
    out = {}
    m1_cols = [c for c in ("DXY_z", "DXY_mom5", "US10Y_d5", "TIP_mom5") if c in panel]
    for cb in ("CB_IFS_level", "CB_IFS_z", "CB_IFS_sum3"):
        if cb in feats:
            for mc in m1_cols:
                out[f"spearman_{cb}_vs_{mc}"] = _spearman(feats[cb].values, panel[mc].values)
    return out


def _per_year_auc(panel: pd.DataFrame, feats: pd.DataFrame, h: int, model: str) -> dict:
    """ΔAUC M2-M1 theo từng năm (walk-forward OOS blocks)."""
    feats_ok = [c for c in ("CB_IFS_level", "CB_IFS_z") if c in feats]
    m2_feats = M1_FEATURES + feats_ok
    o1 = walk_forward(panel, M1_FEATURES, h)
    o2 = walk_forward(panel, m2_feats, h)
    out = {}
    for yr, m1, m2 in _split_by_year(o1, o2):
        a1 = m1["auc"]
        a2 = m2["auc"]
        if a1 is not None and a2 is not None and not np.isnan(a1) and not np.isnan(a2):
            out[str(yr)] = a2 - a1
    return out


def _split_by_year(o1: pd.DataFrame, o2: pd.DataFrame):
    """Chia OOS predictions theo năm (chỉ các ngày cả 2 đều có prob)."""
    for year in sorted(set(o1.index.year) | set(o2.index.year)):
        m1 = o1[(o1.index.year == year) & o1["prob"].notna()]
        m2 = o2[(o2.index.year == year) & o2["prob"].notna()]
        if len(m1) and len(m2):
            yield year, _accuracy(m1, 20), _accuracy(m2, 20)


def main() -> None:
    print("=" * 110)
    print("  GATE M2 — CB/Reserve-demand (IFS-primary) incremental power over M1")
    print("=" * 110)

    usable = _load_usable_panel()
    print(f"  Panel usable: {usable.index.min().date()} -> {usable.index.max().date()} ({len(usable)} ngày)")

    conn = _connect_h2()
    feats = build_cb_features(usable, conn)
    conn.close()
    panel = pd.concat([usable, feats], axis=1)

    # ── Coverage ──
    cov = {c: int(feats[c].notna().sum()) for c in feats.columns}
    print(f"  CB feature coverage: {cov}")

    # ── M1 vs M2 per horizon ──
    rows = []
    for h in HORIZONS:
        print(f"\n  ── HORIZON H{h} ──")
        o1 = walk_forward(panel, M1_FEATURES, h)
        m1 = _accuracy(o1, h)
        print(
            f"    M1_monetary    n={m1['n']:>4d} acc={m1['acc'] * 100:5.1f}% base={m1['base'] * 100:5.1f}% auc={m1['auc']:.3f}"
        )
        rows.append(
            {
                "horizon": h,
                "model": "M1_monetary",
                "n_oos": m1["n"],
                "acc": m1["acc"],
                "base_rate": m1["base"],
                "auc": m1["auc"],
            }
        )

        for cb in ("CB_IFS_level", "CB_IFS_z", "CB_IFS_sum3"):
            m2_feats = M1_FEATURES + [cb]
            o2 = walk_forward(panel, m2_feats, h)
            m2 = _accuracy(o2, h)
            d_auc = np.nan
            if m1["n"] and m2["n"] and not np.isnan(m1["auc"]) and not np.isnan(m2["auc"]):
                d_auc = m2["auc"] - m1["auc"]
            d_acc = (m2["acc"] - m1["acc"]) if m1["n"] and m2["n"] else np.nan
            if m2["n"]:
                print(f"    M2 +{cb:<13s} n={m2['n']:>4d} auc={m2['auc']:.3f}  dAUC={d_auc:+.3f}  dAcc={d_acc * 100:+.2f}pp")
            else:
                print(f"    M2 +{cb} NO OOS")
            rows.append(
                {
                    "horizon": h,
                    "model": f"M2_{cb}",
                    "n_oos": m2["n"],
                    "acc": m2["acc"],
                    "base_rate": m2["base"],
                    "auc": m2["auc"],
                    "delta_auc": d_auc,
                    "delta_acc": d_acc,
                }
            )

        # standalone CB feature (không cần M1) — dùng CB_IFS_z làm đại diện
        o_stand = walk_forward(panel, ["CB_IFS_z"], h)
        ms = _accuracy(o_stand, h)
        print(f"    standalone CB_IFS_z  n={ms['n']:>4d} auc={ms['auc']:.3f}")
        rows.append(
            {
                "horizon": h,
                "model": "CB_standalone_z",
                "n_oos": ms["n"],
                "acc": ms["acc"],
                "base_rate": ms["base"],
                "auc": ms["auc"],
            }
        )

    # ── Redundancy CB vs M1 ──
    print("\n  REDUNDANCY — CB feature vs M1 (spearman):")
    red = _redundancy_cb_m1(usable, feats)
    for k, v in red.items():
        print(f"    {k}: {v:.3f}")
        rows.append(
            {
                "horizon": 0,
                "model": k,
                "n_oos": 0,
                "acc": np.nan,
                "base_rate": np.nan,
                "auc": np.nan,
                "delta_auc": v,
                "delta_acc": np.nan,
            }
        )

    # ── Per-year ΔAUC ──
    print("\n  PER-YEAR ΔAUC (M2_z − M1) per horizon:")
    for h in HORIZONS:
        py = _per_year_auc(panel, feats, h, "M2")
        line = "  ".join(f"{yr}:{v:+.3f}" for yr, v in py.items())
        print(f"    H{h}: {line}")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n  Saved: {OUT_CSV}")
    print("\n  LƯU Ý: 2025 chỉ descriptive (đã nhiễm từ thí nghiệm khác). Không tuning theo 2025.")


if __name__ == "__main__":
    main()
