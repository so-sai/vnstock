"""opportunity_score_research.py — READ-ONLY feature-discrimination audit (Gate 2).

Trả lời đúng câu hỏi nghiên cứu:
    "Feature này có giúp phân biệt mã tốt hơn mã xấu trong cùng một ngày,
     một cách ổn định ngoài mẫu không?"

KHÔNG hỏi "feature này có tương quan với return không" (bài học p_gain:
pooled RankIC +0.008 — correlation pooled không phải selection power).

Candidate pool: decision_ledger trong `data/replays/replay_58_ts/`
(57 mã core × ~249 phiên/năm × 2022-2025). Đây là cross-section đầy đủ.

Targets: Y_H = R_{t→t+H} cho H = 20 (primary), 60, 120.
Forward return tính bằng TRADING-DAY (shift theo phiên, không theo ngày lịch),
PIT strict: entry = close tại t, exit = close phiên thứ H sau t.

Feature matrix (mỗi feature PIT tại t, chuẩn hoá cross-sectional nội ngày):
  Cluster Valuation  : PE/PB/PS/EV_EBITDA/ROE TS z-score + valuation zone (strict period<)
  Cluster Market     : momentum ret_5/20/60/120, realized vol, drawdown, ADV, volume anomaly
  Cluster Behavior   : volume_profile ratio + position (date<=t)
  Cluster Relative   : sector-relative momentum (loại beta ngành)
  Cluster Evidence   : p_gain / eu / kelly_alloc / decision_quality / n_independent_evidence
                       → DIAGNOSTIC ONLY. Không đưa vào score.

Diagnostic mỗi feature × mỗi horizon:
  1. PIT provenance       — nguồn query, biên period/date
  2. Cross-sectional dispersion — mean within-day std (feature có biến thiên nội ngày?)
  3. Daily RankIC          — Spearman(feature, Y_H) TỪNG NGÀY, rồi pooled/per-year/per-regime
  4. Top-decile spread     — mean Y_H(top decile) − mean Y_H(bottom decile), nội ngày
  5. Monotonic bucket      — quintile nội ngày → Spearman(rank bucket, mean bucket)
                           (pooled bucket CẤM — timing artifact như p_gain)
  6. Sign stability        — % năm cùng dấu với pooled IC

READ-ONLY: không ghi replay DB / screener_cache.db / financial_facts.db.
Không tuning weights. Không sửa Governor.

Usage (từ project root):
  python -X utf8 backend/src/research/opportunity_score_research.py [--pool all|deploy]
  python -m backend.src.research.opportunity_score_research
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

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
SCREENER_DB = DATA_DIR / "screener_cache.db"
FIN_DB = DATA_DIR / "financial_facts.db"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

YEARS = ("2022", "2023", "2024", "2025")
HORIZONS = (20, 60, 120)
MIN_SYMBOLS_PER_DAY = 15  # đủ cross-section để tính RankIC/decile đáng tin
DEFAULT_REPLAY_PREFIX = "replay_58"
DEFAULT_REPLAY_DIR = "replay_58_ts"

# Ordinal cho valuation zone (TS z-score → zone). Giá trị thấp = rẻ.
ZONE_ORDINAL = {
    "ULTRA_CHEAP": 2.0,
    "CHEAP": 1.0,
    "FAIR": 0.0,
    "EXPENSIVE": -1.0,
    "ULTRA_EXPENSIVE": -2.0,
}


def _quarter_key(date_str: str) -> int:
    """date 'YYYY-MM-DD' → period_key int YYYYQn (n=1..4)."""
    try:
        y, m, _ = date_str.split("-")
        q = (int(m) - 1) // 3 + 1
        return int(y) * 10 + q
    except ValueError, TypeError:
        return 0


def _parse_period_key(period: str) -> int:
    """period '2022Q1' → int 20221."""
    try:
        y, q = period.split("Q")
        return int(y) * 10 + int(q)
    except ValueError, TypeError:
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# 1. LOADERS (đều READ-ONLY)
# ═══════════════════════════════════════════════════════════════════════════


def load_pool(replay_dir: Path, prefix: str = DEFAULT_REPLAY_PREFIX, pool: str = "all") -> pd.DataFrame:
    """Candidate pool = decision_ledger trong replay_58_ts.

    --pool all   : toàn bộ ledger (cross-section 57 mã/ngày).
    --pool deploy: chỉ OPEN/SCALE_IN (tập deployment candidate thực tế).
    """
    frames = []
    for y in YEARS:
        db = replay_dir / f"{prefix}_{y}.db"
        if not db.exists():
            continue
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT date, symbol, p_gain, eu, kelly_alloc, action, decision, "
                "decision_quality, n_independent_evidence "
                "FROM decision_ledger ORDER BY date"
            ).fetchall()
        finally:
            conn.close()
        df = pd.DataFrame(
            rows,
            columns=[
                "date",
                "symbol",
                "p_gain",
                "eu",
                "kelly_alloc",
                "action",
                "decision",
                "decision_quality",
                "n_independent_evidence",
            ],
        )
        df["year"] = y
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"Không có replay DB nào trong {replay_dir}")
    out = pd.concat(frames, ignore_index=True)
    if pool == "deploy":
        deploy = out["action"].astype(str).str.strip().str.upper().isin(("OPEN", "SCALE_IN"))
        out = out[deploy]
    out["year"] = out["year"].astype(str)
    out["date"] = out["date"].astype(str)
    return out.reset_index(drop=True)


def load_prices(symbols: list[str], start: str = "2021-01-01") -> pd.DataFrame:
    """daily_ohlcv cho các symbol trong pool (từ start để có lookback 120d).

    READ-ONLY. Trả frame sorted (symbol, date)."""
    ph = ",".join("?" * len(symbols))
    conn = sqlite3.connect(str(SCREENER_DB))
    try:
        rows = conn.execute(
            f"SELECT symbol, date, close, volume FROM daily_ohlcv "
            f"WHERE symbol IN ({ph}) AND date >= ? AND date <= ? ORDER BY symbol, date",
            (*symbols, start, "2026-12-31"),
        ).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"])
    df["date"] = df["date"].astype(str)
    return df


def add_price_features(px: pd.DataFrame) -> pd.DataFrame:
    """Momentum / vol / drawdown / liquidity / volume anomaly + forward returns.

    Features CHỈ dùng dữ liệu <= t (shift dương = quá khứ).
    Forward returns dùng shift âm (phiên H sau t) — là TARGET, không phải feature.
    """
    px = px.copy().sort_values(["symbol", "date"]).reset_index(drop=True)
    g = px.groupby("symbol", sort=False)

    px["ret_5d"] = g["close"].pct_change(5)
    px["ret_20d"] = g["close"].pct_change(20)
    px["ret_60d"] = g["close"].pct_change(60)
    px["ret_120d"] = g["close"].pct_change(120)

    daily_ret = g["close"].pct_change()
    px["rvol_20d"] = daily_ret.groupby(px["symbol"]).transform(lambda s: s.rolling(20).std())

    px["dd_120d"] = g["close"].transform(lambda s: s / s.rolling(120).max() - 1.0)
    px["adv_20d"] = g["volume"].transform(lambda s: s.rolling(20).mean())
    px["vol_ratio_20d"] = px["volume"] / px["adv_20d"]

    # Forward returns (trading-day, H phiên sau t)
    for h in HORIZONS:
        px[f"r{h}"] = g["close"].shift(-h) / px["close"] - 1.0

    return px.drop(columns=["volume"])


def load_valuation_pit(symbols: list[str]) -> pd.DataFrame:
    """Valuation TS z-score PIT: cho mỗi (symbol, date) lấy quý CHỈ CŨ HƠN
    quý hiện tại (period_key < quarter_key(t)) — chuẩn nghiêm PIT của repo
    (walk_forward_harness test yêu cầu period<?).

    Trả frame nội suy: symbol, date, pe_z_ts, pb_z_ts, ps_z_ts, ev_ebitda_z_ts,
    roe_z_ts, val_zone_ts (ordinal), pe_pct, pb_pct.
    """
    ph = ",".join("?" * len(symbols))
    conn = sqlite3.connect(str(FIN_DB))
    try:
        rows = conn.execute(
            f"SELECT symbol, period, ratio_name, z_score_ts, percentile, zone_ts "
            f"FROM valuation_scores WHERE symbol IN ({ph}) "
            f"AND z_score_ts IS NOT NULL ORDER BY symbol, period",
            symbols,
        ).fetchall()
    finally:
        conn.close()
    val = pd.DataFrame(rows, columns=["symbol", "period", "ratio_name", "z_score_ts", "percentile", "zone_ts"])
    if val.empty:
        return pd.DataFrame(columns=["symbol", "date"])
    val["period_key"] = val["period"].map(_parse_period_key)

    z = val.pivot_table(index=["symbol", "period_key"], columns="ratio_name", values="z_score_ts", aggfunc="last")
    z = z.reset_index()
    z = z.rename(columns={c: f"{c}_z_ts" for c in z.columns if c not in ("symbol", "period_key")})

    pct = val.pivot_table(index=["symbol", "period_key"], columns="ratio_name", values="percentile", aggfunc="last")
    pct = pct.reset_index()
    pct = pct.rename(columns={c: f"{c}_pct" for c in pct.columns if c not in ("symbol", "period_key")})

    zone = val.pivot_table(index=["symbol", "period_key"], columns="ratio_name", values="zone_ts", aggfunc="last")
    zone = zone.reset_index()
    # zone ordinal: trung bình ordinal theo ratio có sẵn
    zone_ord = zone[[c for c in zone.columns if c not in ("symbol", "period_key")]].applymap(
        lambda v: ZONE_ORDINAL.get(str(v).strip().upper(), 0.0)
    )
    zone_ord["symbol"] = zone["symbol"]
    zone_ord["period_key"] = zone["period_key"]
    zone_ord["val_zone_ts"] = zone_ord[[c for c in zone_ord.columns if c not in ("symbol", "period_key")]].mean(axis=1)

    merged = z.merge(pct, on=["symbol", "period_key"], how="outer").merge(
        zone_ord[["symbol", "period_key", "val_zone_ts"]], on=["symbol", "period_key"], how="outer"
    )
    merged = merged.sort_values(["symbol", "period_key"]).reset_index(drop=True)

    # Mở rộng grid (symbol × date từ pool) — gọi hàm merge_pit bên ngoài.
    return merged


def merge_valuation_pit(pool: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    """Gắn valuation PIT vào pool: với mỗi (symbol,date) chọn quý lớn nhất < quý(date).

    merge_asof backward với period_key target − 1 → period < target (chuẩn nghiêm).
    """
    pool = pool.copy()
    pool["period_target"] = pool["date"].map(_quarter_key)
    left = pool[["symbol", "date", "period_target"]].copy()
    left["lk"] = left["period_target"] - 1  # chỉ nhận quý cũ hơn
    right = val.rename(columns={"period_key": "lk"})[
        ["symbol", "lk"] + [c for c in val.columns if c not in ("symbol", "period_key")]
    ]
    right = right.sort_values(["lk"]).reset_index(drop=True)
    left = left.sort_values(["lk"]).reset_index(drop=True)
    m = pd.merge_asof(left, right, on="lk", by="symbol", direction="backward", suffixes=("", "_val"))
    pool = pool.drop(columns=["period_target"])
    m = m.drop(columns=["lk", "date_val", "symbol_val"] if "date_val" in m.columns else ["lk"])
    return pool.merge(
        m[["symbol", "date"] + [c for c in m.columns if c not in ("symbol", "date")]], on=["symbol", "date"], how="left"
    )


def load_volume_profile_pit(pool: pd.DataFrame) -> pd.DataFrame:
    """Volume profile PIT: với mỗi (symbol,date) lấy bản ghi date <= t gần nhất.

    READ-ONLY. merge_asof backward on date (ISO sort = chronological).
    """
    conn = sqlite3.connect(str(FIN_DB))
    try:
        rows = conn.execute(
            "SELECT symbol, date, volume_ratio, price_current, val, vah "
            "FROM volume_profile WHERE volume_ratio IS NOT NULL "
            "ORDER BY symbol, date"
        ).fetchall()
    finally:
        conn.close()
    vp = pd.DataFrame(rows, columns=["symbol", "date", "volume_ratio", "price_current", "val", "vah"])
    vp["date"] = vp["date"].astype(str)
    vp["date_ord"] = pd.to_datetime(vp["date"]).astype("int64") // 10**9
    vp = vp.sort_values(["date_ord"]).reset_index(drop=True)

    pool = pool[["symbol", "date"]].copy()
    pool["date_ord"] = pd.to_datetime(pool["date"]).astype("int64") // 10**9
    pool = pool.sort_values(["date_ord"]).reset_index(drop=True)
    m = pd.merge_asof(pool, vp, on="date_ord", by="symbol", direction="backward")
    m = m.drop(columns=["date_ord", "date_y"] if "date_y" in m.columns else ["date_ord"])
    # position: dưới VA = 0, trong VA = 1, trên VA = 2
    m["vp_position"] = np.select(
        [m["price_current"] < m["val"], m["price_current"] > m["vah"]],
        [0.0, 2.0],
        default=1.0,
    )
    return m[["symbol", "date_x", "volume_ratio", "vp_position"]].rename(columns={"date_x": "date"})


def load_sector_map() -> pd.DataFrame:
    conn = sqlite3.connect(str(SCREENER_DB))
    try:
        rows = conn.execute("SELECT symbol, icb_name2 AS sector FROM symbol_industry WHERE icb_name2 IS NOT NULL").fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["symbol", "sector"])


def load_regimes() -> pd.DataFrame:
    conn = sqlite3.connect(str(SCREENER_DB))
    try:
        rows = conn.execute("SELECT date, status AS regime FROM regime_history").fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["date", "regime"])
    df["date"] = df["date"].astype(str)
    return df


# ═══════════════════════════════════════════════════════════════════════════
# 2. DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════════


def _spearman(x, y) -> float | None:
    if len(x) < 3:
        return None
    try:
        rho = spearmanr(x, y).statistic
    except ValueError, TypeError:
        return None
    if rho is None or math.isnan(rho):
        return None
    return float(rho)


def daily_rank_ic(df: pd.DataFrame, feature: str, target: str) -> pd.DataFrame:
    """Daily RankIC: Spearman(feature, target) TỪNG NGÀY.

    Trả frame: date, year, regime, n, ic. Chỉ giữ ngày có >= MIN_SYMBOLS_PER_DAY.
    """
    out = []
    for date, g in df.groupby("date"):
        g = g.dropna(subset=[feature, target])
        if len(g) < MIN_SYMBOLS_PER_DAY:
            continue
        rho = _spearman(g[feature], g[target])
        if rho is None:
            continue
        out.append(
            {
                "date": date,
                "year": str(date)[:4],
                "regime": g["regime"].iloc[0] if "regime" in g.columns and g["regime"].notna().any() else "UNKNOWN",
                "n": len(g),
                "ic": rho,
            }
        )
    return pd.DataFrame(out, columns=["date", "year", "regime", "n", "ic"])


def top_decile_spread(df: pd.DataFrame, feature: str, target: str) -> dict:
    """Top-decile − bottom-decile spread (nội ngày), trung bình qua các ngày."""
    spreads = []
    for date, g in df.groupby("date"):
        g = g.dropna(subset=[feature, target])
        if len(g) < MIN_SYMBOLS_PER_DAY:
            continue
        r = g[feature].rank(method="first")
        n = len(g)
        top = g.loc[r > n * 0.9, target]
        bot = g.loc[r <= n * 0.1, target]
        if len(top) < 2 or len(bot) < 2:
            continue
        spreads.append(top.mean() - bot.mean())
    if not spreads:
        return {"n_days": 0, "mean_spread": None, "pct_positive": None}
    arr = np.array(spreads)
    return {
        "n_days": len(arr),
        "mean_spread": float(np.mean(arr)),
        "pct_positive": float(np.mean(arr > 0)),
    }


def monotonic_buckets(df: pd.DataFrame, feature: str, target: str, n_buckets: int = 5) -> dict:
    """Monotonic bucket nội ngày: chia feature thành n_buckets theo rank trong ngày,
    tính Spearman(rank bucket, mean target). Trung bình qua các ngày.

    KHÔNG pooled bucket (cấm timing artifact — bài học p_gain).
    """
    monos = []
    for date, g in df.groupby("date"):
        g = g.dropna(subset=[feature, target])
        if len(g) < MIN_SYMBOLS_PER_DAY * 2:
            continue
        r = g[feature].rank(method="first").astype(int)
        bucket = np.floor((r - 1) / len(g) * n_buckets).clip(0, n_buckets - 1)
        bucket_means = g.groupby(bucket)[target].mean()
        if len(bucket_means) < 3:
            continue
        rho = _spearman(np.arange(len(bucket_means)), bucket_means.values)
        if rho is not None:
            monos.append(rho)
    if not monos:
        return {"n_days": 0, "mean_mono": None, "pct_positive": None}
    arr = np.array(monos)
    return {
        "n_days": len(arr),
        "mean_mono": float(np.mean(arr)),
        "pct_positive": float(np.mean(arr > 0)),
    }


def diagnose_feature(
    df: pd.DataFrame,
    feature: str,
    target: str,
    expected_sign: str = "pos",
) -> dict:
    """Toàn bộ diagnostic cho 1 feature × 1 horizon.

    Một pass duy nhất qua các ngày: tính đồng thời RankIC, top-decile spread,
    monotonic bucket (không 3 pass riêng — nhanh ~3x).

    expected_sign: 'pos' → IC dương là đúng hướng; 'neg' → IC âm là đúng hướng
    (vd valuation z-score: rẻ = z thấp → return cao → IC âm kỳ vọng).
    """
    sub = df.dropna(subset=[feature, target])
    n_obs = int(len(sub))
    if n_obs < MIN_SYMBOLS_PER_DAY:
        return {"feature": feature, "target": target, "n_obs": 0, "error": "insufficient"}

    # ── single pass over days ─────────────────────────────────────────────
    rows = []
    for date, g in sub.groupby("date", sort=True):
        n = len(g)
        if n < MIN_SYMBOLS_PER_DAY:
            continue
        r = g[feature].rank(method="first")
        rho = _spearman(g[feature], g[target])
        top = g.loc[r > n * 0.9, target]
        bot = g.loc[r <= n * 0.1, target]
        if len(top) >= 2 and len(bot) >= 2:
            spread = float(top.mean() - bot.mean())
        else:
            spread = None
        if n >= MIN_SYMBOLS_PER_DAY * 2:
            bucket = np.floor((r - 1) / n * 5).clip(0, 4)
            bucket_means = g.groupby(bucket)[target].mean()
            if len(bucket_means) >= 3:
                mono = _spearman(np.arange(len(bucket_means)), bucket_means.values)
            else:
                mono = None
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
        return {"feature": feature, "target": target, "n_obs": n_obs, "error": "no_daily_ic"}
    day = pd.DataFrame(rows)

    ics = day.dropna(subset=["ic"])
    if ics.empty:
        return {"feature": feature, "target": target, "n_obs": n_obs, "error": "no_daily_ic"}
    pooled = float(ics["ic"].mean())
    mean_abs = float(ics["ic"].abs().mean())
    n_days = len(ics)
    std = float(ics["ic"].std(ddof=1))
    t_stat = pooled / (std / math.sqrt(n_days)) if std > 0 else 0.0
    pct_pos = float((ics["ic"] > 0).mean())

    per_year = {str(y): float(g["ic"].mean()) for y, g in ics.groupby("year") if len(g) >= 10}
    year_sign_match = sum(1 for v in per_year.values() if (v > 0) == (pooled > 0))
    sign_stability = year_sign_match / len(per_year) if per_year else None

    per_regime = {str(r): float(g["ic"].mean()) for r, g in ics.groupby("regime") if len(g) >= 10}

    spreads = day.dropna(subset=["spread"])["spread"]
    monos = day.dropna(subset=["mono"])["mono"]

    # cross-sectional dispersion (nội ngày): mean within-day std
    disp = float(sub.groupby("date")[feature].std().mean()) if not sub.empty else float("nan")

    correct_pooled = pooled if expected_sign == "pos" else -pooled
    correct_years = sum(1 for v in per_year.values() if (v if expected_sign == "pos" else -v) > 0)

    return {
        "feature": feature,
        "target": target,
        "n_obs": n_obs,
        "n_days": n_days,
        "dispersion_std": round(disp, 6),
        "pooled_ic": round(pooled, 4),
        "correct_sign_ic": round(correct_pooled, 4),
        "mean_abs_ic": round(mean_abs, 4),
        "ic_tstat": round(t_stat, 2),
        "pct_days_pos": round(pct_pos * 100, 1),
        "sign_stability": round(sign_stability * 100, 0) if sign_stability is not None else None,
        "correct_years": correct_years,
        "per_year": per_year,
        "per_regime": per_regime,
        "top_decile_spread_pct": round(float(spreads.mean() * 100), 2) if len(spreads) else None,
        "top_decile_pct_positive": round(float((spreads > 0).mean() * 100), 0) if len(spreads) else None,
        "mono_mean": round(float(monos.mean()), 3) if len(monos) else None,
        "mono_pct_positive": round(float((monos > 0).mean() * 100), 0) if len(monos) else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. MAIN
# ═══════════════════════════════════════════════════════════════════════════

FEATURE_SPECS = {
    # cluster → (feature_col, expected_sign, note)
    "Valuation": [
        ("PE_z_ts", "neg", "PE TS z-score (thấp = rẻ)"),
        ("PB_z_ts", "neg", "PB TS z-score (thấp = rẻ)"),
        ("PS_z_ts", "neg", "PS TS z-score (thấp = rẻ)"),
        ("EV_EBITDA_z_ts", "neg", "EV/EBITDA TS z-score (thấp = rẻ)"),
        ("val_zone_ts", "pos", "Ordinal zone (2=rẻ nhất)"),
    ],
    "Quality": [
        ("ROE_z_ts", "pos", "ROE TS z-score (cao = chất lượng)"),
        ("PE_pct", "neg", "PE percentile"),
    ],
    "Market": [
        ("ret_5d", "pos", "Momentum 5 phiên"),
        ("ret_20d", "pos", "Momentum 20 phiên"),
        ("ret_60d", "pos", "Momentum 60 phiên"),
        ("ret_120d", "pos", "Momentum 120 phiên"),
        ("rvol_20d", "neg", "Realized vol 20 phiên (thấp = an toàn)"),
        ("dd_120d", "pos", "Drawdown 120d (0 = gần đỉnh)"),
    ],
    "Behavior": [
        ("vol_ratio_20d", "pos", "Volume anomaly (cao = có dòng tiền)"),
        ("adv_20d", "pos", "Thanh khoản ADV20"),
        ("volume_ratio", "pos", "Volume profile ratio"),
        ("vp_position", "pos", "Vị trí vs VA (0=dưới, 1=trong, 2=trên)"),
    ],
    "Relative": [
        ("sector_rel_ret_20d", "pos", "Momentum 20d − sector median (loại beta ngành)"),
    ],
    "Evidence(diag)": [
        ("p_gain", "pos", "P(model nói BUY) — DIAGNOSTIC ONLY"),
        ("eu", "pos", "Expected Utility — DIAGNOSTIC ONLY"),
        ("kelly_alloc", "pos", "Kelly allocation — DIAGNOSTIC ONLY"),
        ("decision_quality", "pos", "Strong/Mixed ordinal — DIAGNOSTIC ONLY"),
        ("n_independent_evidence", "pos", "Số bằng chứng độc lập — DIAGNOSTIC ONLY"),
    ],
}

EVIDENCE_FEATURES = {"p_gain", "eu", "kelly_alloc", "decision_quality", "n_independent_evidence"}


def build_feature_matrix(pool: pd.DataFrame) -> pd.DataFrame:
    """Kết hợp pool + price features + valuation PIT + volume profile + sector.

    Trả frame wide: date, symbol, year, regime + các feature columns + r20/r60/r120.
    """
    symbols = sorted(pool["symbol"].unique())
    px = load_prices(symbols)
    px = add_price_features(px)
    px = px.drop(columns=["close"])

    val = load_valuation_pit(symbols)
    m = pool.merge(px, on=["symbol", "date"], how="left")
    m = merge_valuation_pit(m, val)
    m = m.merge(load_volume_profile_pit(pool), on=["symbol", "date"], how="left")
    m = m.merge(load_regimes(), on="date", how="left")

    # Sector-relative momentum: ret_20d − sector median (nội ngày)
    sec = load_sector_map()
    m = m.merge(sec, on="symbol", how="left")
    m["sector_rel_ret_20d"] = np.nan
    has_sec = m["sector"].notna() & m["ret_20d"].notna()
    m.loc[has_sec, "sector_rel_ret_20d"] = m.loc[has_sec, "ret_20d"] - m.loc[has_sec].groupby(["date", "sector"])[
        "ret_20d"
    ].transform("median")

    # Evidence ordinal
    m["decision_quality"] = m["decision_quality"].map({"Strong": 2.0, "Mixed": 1.0}).fillna(0.0)
    return m


def print_report(df: pd.DataFrame, pool_label: str) -> None:
    print("=" * 132)
    print(f"  FEATURE-DISCRIMINATION AUDIT — candidate pool = {pool_label} (replay_58_ts)")
    print("  Câu hỏi: feature giúp phân biệt mã tốt/xấu TRONG CÙNG NGÀY, ổn định ngoài mẫu?")
    print("  Target: R(t→t+H), H = 20/60/120. PIT strict (period< / date<=). READ-ONLY.")
    print("=" * 132)

    all_rows = []
    for horizon in HORIZONS:
        target = f"r{horizon}"
        print(f"\n### Horizon H{horizon}  (Y = R(t→t+{horizon}))")
        print(
            f"{'Cluster':<16}{'Feature':<26}{'n':>6}{'disp':>8}{'IC':>8}{'dirIC':>8}"
            f"{'tstat':>7}{'%pos':>6}{'signStab':>8}{'topDecile%':>11}{'mono':>7}"
        )
        print("-" * 132)
        for cluster, feats in FEATURE_SPECS.items():
            for fname, exp_sign, note in feats:
                if fname not in df.columns:
                    continue
                d = diagnose_feature(df, fname, target, exp_sign)
                d["cluster"] = cluster
                d["horizon"] = horizon
                d["feature_note"] = note
                all_rows.append(d)
                if d.get("error"):
                    print(f"{cluster:<16}{fname:<26}{d['n_obs']:>6}  {d['error']}")
                    continue
                stab = f"{d['sign_stability']:.0f}%" if d["sign_stability"] is not None else "  -"
                spread = f"{d['top_decile_spread_pct']:+.2f}" if d["top_decile_spread_pct"] is not None else "   -"
                mono = f"{d['mono_mean']:+.3f}" if d["mono_mean"] is not None else "  -"
                print(
                    f"{cluster:<16}{fname:<26}{d['n_obs']:>6}{d['dispersion_std']:>8.3f}"
                    f"{d['pooled_ic']:>+8.3f}{d['correct_sign_ic']:>+8.3f}{d['ic_tstat']:>7.1f}"
                    f"{d['pct_days_pos']:>6.0f}{stab:>8}{spread:>11}{mono:>7}"
                )

    result = pd.DataFrame(all_rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_feature_audit_{pool_label}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")

    # Warning cho evidence features (chỉ diagnostic)
    print("\n" + "=" * 132)
    print("  LƯU Ý: Evidence features (p_gain/eu/kelly/quality/evidence) chỉ DIAGNOSTIC.")
    print("  p_gain không được đưa vào Opportunity Score — cần experiment incremental")
    print("  information conditional on new score trước khi quay lại.")
    print("=" * 132)


def main() -> None:
    ap = argparse.ArgumentParser(description="Feature-discrimination audit (Gate 2) — READ-ONLY")
    ap.add_argument(
        "--pool",
        choices=("all", "deploy"),
        default="all",
        help="Candidate pool: all (cross-section đủ) hoặc deploy (OPEN/SCALE_IN)",
    )
    ap.add_argument("--replay-dir", default=DEFAULT_REPLAY_DIR)
    ap.add_argument("--prefix", default=DEFAULT_REPLAY_PREFIX)
    args = ap.parse_args()

    replay_dir = DATA_DIR / "replays" / args.replay_dir
    pool = load_pool(replay_dir, prefix=args.prefix, pool=args.pool)
    print(
        f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} "
        f"dates={pool['date'].nunique()} years={sorted(pool['year'].unique())}"
    )

    matrix = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(matrix)} cols={len(matrix.columns)}")
    print("[features] p_gain RankIC sanity (H20, cross-section): ")

    # Sanity check: tái lập baseline p_gain RankIC pooled ≈ +0.008
    pg_ic = daily_rank_ic(matrix.dropna(subset=["p_gain", "r20"]), "p_gain", "r20")
    if not pg_ic.empty:
        print(f"    p_gain H20: pooled RankIC={pg_ic['ic'].mean():+.4f} (n_days={len(pg_ic)}, baseline 147afdd ≈ +0.008)")

    print_report(matrix, args.pool)


if __name__ == "__main__":
    main()
