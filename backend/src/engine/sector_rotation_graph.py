"""
Sector Rotation Graph (Phase 11 — Asia Adaptation Layer).
Measures inter-sector influence network, rotation phase, money flow propagation.
VN market flows through sector narratives (Bank → Securities → Midcap → Penny).
"""
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import logging

import numpy as np
import pandas as pd

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

SECTOR_ORDER = [
    "Ngân hàng", "Chứng khoán", "Bất động sản", "Xây dựng",
    "Thép", "Dầu khí", "Điện", "Công nghệ",
    "Bán lẻ", "Thực phẩm", "Dược", "Hàng không",
    "Cảng biển", "Nhựa", "Dệt may", "Thủy sản",
]


def _load_sector_mapping() -> dict:
    with get_connection() as conn:
        df = pd.read_sql("SELECT symbol, icb_name3 FROM symbol_industry", conn)
    return df.set_index("symbol")["icb_name3"].to_dict()


def _sanitize_float(val, default=0.0):
    try:
        if val is None or pd.isna(val) or np.isinf(val):
            return default
        return float(val)
    except Exception:
        return default


def compute_sector_rs(sector: str, lookback: int = 60) -> dict:
    with get_connection() as conn:
        mapping = _load_sector_mapping()
        symbols = [s for s, sec in mapping.items() if sec == sector]
        if not symbols:
            return {"sector": sector, "status": "NO_SYMBOLS"}

        placeholders = ",".join(["?"] * len(symbols))
        df = pd.read_sql(
            f"SELECT symbol, date, close FROM daily_ohlcv "
            f"WHERE symbol IN ({placeholders}) AND date >= date('now', '-{lookback + 10} days') "
            f"ORDER BY date",
            conn, params=symbols
        )
    if df.empty:
        return {"sector": sector, "status": "NO_DATA"}

    df["return"] = df.groupby("symbol")["close"].pct_change()
    daily = df.groupby("date")["return"].mean().reset_index().sort_values("date")
    if daily.empty or len(daily) < 20:
        return {"sector": sector, "status": "INSUFFICIENT_DATA"}

    daily["cum_return"] = (1 + daily["return"]).cumprod()
    daily["ma5"] = daily["cum_return"].rolling(5).mean()
    daily["ma20"] = daily["cum_return"].rolling(20).mean()
    daily["momentum"] = daily["ma5"] / daily["ma20"] - 1

    latest = daily.iloc[-1]
    daily["volatility"] = daily["return"].rolling(20).std()
    sharpe = (daily["return"].mean() / daily["return"].std() * np.sqrt(252)) if daily["return"].std() > 0 else 0

    sma20 = daily["close"].mean() if "close" in daily.columns else 0

    recent_5d = daily.tail(5)["return"].sum()
    recent_20d = daily.tail(20)["return"].sum() if len(daily) >= 20 else recent_5d

    mom_slope = 0
    if len(daily) >= 10:
        mom_vals = daily["momentum"].dropna().tail(10).values
        if len(mom_vals) >= 5:
            mom_slope = (mom_vals[-1] - mom_vals[0]) / len(mom_vals)

    return {
        "sector": sector,
        "momentum": round(_sanitize_float(latest["momentum"]) * 100, 2),
        "return_5d": round(_sanitize_float(recent_5d) * 100, 2),
        "return_20d": round(_sanitize_float(recent_20d) * 100, 2),
        "momentum_slope": round(_sanitize_float(mom_slope) * 100, 3),
        "sharpe_annual": round(_sanitize_float(sharpe), 2),
        "volatility_20d": round(_sanitize_float(daily["volatility"].iloc[-1]) * 100, 2) if len(daily) >= 20 else 0.0,
        "rotation_streak": int(_rotation_streak(daily)),
        "phase": _classify_rotation_phase(_sanitize_float(latest["momentum"]), _sanitize_float(mom_slope)),
    }


def _rotation_streak(daily: pd.DataFrame) -> int:
    streak = 0
    for i in range(min(10, len(daily) - 1)):
        if daily["momentum"].iloc[-(i + 1)] > 0:
            streak += 1
        else:
            break
    return streak


def _classify_rotation_phase(momentum: float, slope: float) -> str:
    if momentum > 0.03 and slope > 0.005:
        return "EARLY_ACCEL"
    if momentum > 0.05 and slope > 0:
        return "MID_CYCLE"
    if momentum > 0.03 and slope < -0.003:
        return "LATE_CYCLE"
    if momentum > 0.01:
        return "SUSTAINED"
    if momentum > -0.02:
        return "NEUTRAL"
    return "WEAKENING"


def get_sector_rotation_map() -> dict:
    sectors = SECTOR_ORDER
    results = []
    for sec in sectors:
        rs = compute_sector_rs(sec)
        if rs.get("status") in ("NO_SYMBOLS", "NO_DATA", "INSUFFICIENT_DATA"):
            continue
        results.append(rs)

    results.sort(key=lambda x: x["momentum"], reverse=True)

    top3 = [r["sector"] for r in results[:3]]
    bottom3 = [r["sector"] for r in results[-3:]]
    leaders_avg_mom = np.mean([r["momentum"] for r in results[:3]]) if len(results) >= 3 else 0
    laggards_avg_mom = np.mean([r["momentum"] for r in results[-3:]]) if len(results) >= 3 else 0
    rotation_spread = leaders_avg_mom - laggards_avg_mom

    phase_counts = {}
    for r in results:
        phase_counts[r["phase"]] = phase_counts.get(r["phase"], 0) + 1

    dominant_phase = max(phase_counts, key=phase_counts.get) if phase_counts else "NEUTRAL"

    return {
        "sectors": results,
        "leading_sectors": top3,
        "lagging_sectors": bottom3,
        "rotation_spread": round(rotation_spread, 2),
        "dominant_phase": dominant_phase,
        "phase_distribution": phase_counts,
        "num_sectors_active": len(results),
        "market_breadth_score": round(
            (phase_counts.get("EARLY_ACCEL", 0) + phase_counts.get("MID_CYCLE", 0) * 0.7
             + phase_counts.get("SUSTAINED", 0) * 0.4
             - phase_counts.get("WEAKENING", 0) * 0.5) / max(1, len(results)), 3
        ),
    }


def detect_money_flow_propagation(backtrack_days: int = 30) -> list:
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT date, symbol, close, volume FROM daily_ohlcv "
            f"WHERE date >= date('now', '-{backtrack_days} days') AND volume > 0 "
            "ORDER BY date",
            conn
        )
    if df.empty:
        return []

    mapping = _load_sector_mapping()
    df["sector"] = df["symbol"].map(mapping)
    df["value_bn"] = df["close"] * df["volume"] * 1000 / 1e9

    sector_daily = df.groupby(["date", "sector"])["value_bn"].sum().reset_index()
    sector_daily["value_ma3"] = sector_daily.groupby("sector")["value_bn"].transform(
        lambda x: x.rolling(3).mean()
    )
    sector_daily["value_shock"] = sector_daily["value_bn"] / sector_daily["value_ma3"]

    dates = sector_daily["date"].unique()
    if len(dates) < 3:
        return []

    latest_date = sorted(dates, reverse=True)[0]
    prev_date = sorted(dates, reverse=True)[1] if len(dates) >= 2 else latest_date

    latest_flow = sector_daily[sector_daily["date"] == latest_date].set_index("sector")
    prev_flow = sector_daily[sector_daily["date"] == prev_date].set_index("sector")

    flow_changes = []
    for sec in latest_flow.index:
        curr = latest_flow.loc[sec, "value_bn"]
        prev = prev_flow.loc[sec, "value_bn"] if sec in prev_flow.index else 0
        change_pct = ((curr - prev) / prev * 100) if prev > 0 else 0.0
        flow_changes.append({
            "sector": sec,
            "current_value_bn": round(_sanitize_float(curr), 0),
            "change_1d_pct": round(_sanitize_float(change_pct), 1),
            "shock_ratio": round(_sanitize_float(latest_flow.loc[sec, "value_shock"]), 2),
        })

    flow_changes.sort(key=lambda x: x["change_1d_pct"], reverse=True)
    return flow_changes


def get_rotation_beta() -> dict:
    rotation = get_sector_rotation_map()
    flow = detect_money_flow_propagation()

    flow_sectors = {f["sector"] for f in flow[:5]}
    leading = set(rotation.get("leading_sectors", []))
    alignment = len(flow_sectors & leading) / max(1, len(leading))

    rotation_score = rotation.get("market_breadth_score", 0)
    spread = rotation.get("rotation_spread", 0)

    regime_mapped = "HEALTHY_ROTATION"
    if rotation_score < -0.2 and spread < 0:
        regime_mapped = "NARROW_LEADERSHIP"
    elif rotation_score < 0 and spread > 3:
        regime_mapped = "DIVERGENT"
    elif rotation_score > 0.15:
        regime_mapped = "BROAD_ROTATION"

    return {
        "rotation_regime": regime_mapped,
        "flow_alignment_pct": round(_sanitize_float(alignment) * 100, 0),
        "rotation_score": round(_sanitize_float(rotation_score), 3),
        "spread": round(_sanitize_float(spread), 2),
        "dominant_phase": rotation.get("dominant_phase", "NEUTRAL"),
        "leading_sectors": rotation.get("leading_sectors", []),
        "lagging_sectors": rotation.get("lagging_sectors", []),
    }


if __name__ == "__main__":
    import json
    rot = get_sector_rotation_map()
    print(f"Sector Rotation: {json.dumps({k: v for k, v in rot.items() if k != 'sectors'}, ensure_ascii=False, indent=2)}")
    flow = detect_money_flow_propagation()
    print(f"Flow Propagation: {json.dumps(flow[:5], ensure_ascii=False, indent=2)}")
    beta = get_rotation_beta()
    print(f"Rotation Beta: {json.dumps(beta, ensure_ascii=False, indent=2)}")
