"""
Breakout Continuation Engine v1 (Phase 11 — Asia Adaptation Layer).
Model C for VN market: breakout continuation + liquidity confirmation.
VN does NOT mean revert cleanly — it trends with liquidity waves.
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

import pandas as pd

from src.database.db_core import get_connection
from src.engine.regime_engine import detect_regime

logger = logging.getLogger(__name__)


def _load_universe(min_value_bn: float = 5, min_price: float = 5) -> pd.DataFrame:
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT symbol, date, close, volume, high, low FROM daily_ohlcv "
            "WHERE date >= date('now', '-200 days') ORDER BY date",
            conn
        )
    if df.empty:
        return pd.DataFrame()
    df["value_bn"] = df["close"] * df["volume"] * 1000 / 1e9
    latest = df[df["date"] == df.groupby("symbol")["date"].transform("max")]
    filtered = latest[(latest["close"] >= min_price) & (latest["value_bn"] >= min_value_bn)]
    return df[df["symbol"].isin(filtered["symbol"])]


def compute_breakout_score(symbol: str, df: pd.DataFrame = None) -> dict:
    if df is None:
        with get_connection() as conn:
            df_stock = pd.read_sql(
                "SELECT date, close, volume, high, low FROM daily_ohlcv "
                "WHERE symbol = ? AND date >= date('now', '-200 days') ORDER BY date",
                conn, params=(symbol,)
            )
    else:
        df_stock = df[df["symbol"] == symbol].copy()

    if df_stock.empty or len(df_stock) < 50:
        return {"symbol": symbol, "status": "INSUFFICIENT_DATA"}

    df_stock = df_stock.sort_values("date").reset_index(drop=True)
    df_stock = df_stock.assign(
        high_20=lambda x: x["high"].rolling(20).max().shift(1),
        high_50=lambda x: x["high"].rolling(50).max().shift(1),
        high_200=lambda x: x["high"].rolling(200).max().shift(1),
        low_20=lambda x: x["low"].rolling(20).min().shift(1),
        vol_ma20=lambda x: x["volume"].rolling(20).mean(),
        vol_ma50=lambda x: x["volume"].rolling(50).mean(),
        close_ma20=lambda x: x["close"].rolling(20).mean(),
        close_ma50=lambda x: x["close"].rolling(50).mean(),
        close_ma200=lambda x: x["close"].rolling(200).mean(),
    )

    latest = df_stock.iloc[-1]
    prev = df_stock.iloc[-2] if len(df_stock) >= 2 else latest

    bo20 = latest["close"] > latest["high_20"] if pd.notna(latest["high_20"]) else False
    bo50 = latest["close"] > latest["high_50"] if pd.notna(latest["high_50"]) else False
    bo200 = latest["close"] > latest["high_200"] if pd.notna(latest["high_200"]) else False

    vol_ratio = latest["volume"] / latest["vol_ma20"] if latest["vol_ma20"] > 0 else 0
    vol_ratio_50 = latest["volume"] / latest["vol_ma50"] if latest["vol_ma50"] > 0 else 0
    volume_confirm = vol_ratio > 1.3 or vol_ratio_50 > 1.2

    price_vs_ma20 = (latest["close"] / latest["close_ma20"] - 1) * 100 if pd.notna(latest["close_ma20"]) else 0
    price_vs_ma50 = (latest["close"] / latest["close_ma50"] - 1) * 100 if pd.notna(latest["close_ma50"]) else 0

    df_stock = df_stock.assign(
        return_5d=lambda x: x["close"].pct_change(5),
        return_20d=lambda x: x["close"].pct_change(20),
    )
    mom_5d = df_stock["return_5d"].iloc[-1] * 100 if len(df_stock) >= 5 else 0
    mom_20d = df_stock["return_20d"].iloc[-1] * 100 if len(df_stock) >= 20 else 0

    pullback_check = 0
    if bo20:
        recent = df_stock.tail(min(20, len(df_stock)))
        pullbacks = (recent["close"] < recent["close_ma20"]).sum()
        pullback_check = pullbacks / len(recent)

    base_score = 0
    tf_bonus = 0
    if bo200:
        base_score = 60
        tf_bonus = 25
    elif bo50:
        base_score = 45
        tf_bonus = 15
    elif bo20:
        base_score = 30
        tf_bonus = 10

    liq_bonus = min(20, int(vol_ratio * 10))
    mom_bonus = min(15, max(0, int(mom_5d))) if not pd.isna(mom_5d) else 0
    trend_bonus = 10 if (price_vs_ma50 > 0 and price_vs_ma20 > 0) else 0
    pullback_penalty = int(pullback_check * 20)

    total_score = max(0, min(100, base_score + tf_bonus + liq_bonus + mom_bonus + trend_bonus - pullback_penalty))

    all_above_ma = (
        (pd.notna(latest["close_ma20"]) and latest["close"] > latest["close_ma20"])
        and (pd.notna(latest["close_ma50"]) and latest["close"] > latest["close_ma50"])
    )

    continuation_prob = 0.5
    if volume_confirm and bo20 and vol_ratio > 2.0:
        continuation_prob = 0.75
    if volume_confirm and bo50 and vol_ratio > 1.5:
        continuation_prob = 0.70
    if all_above_ma and bo20 and volume_confirm:
        continuation_prob = 0.65

    return {
        "symbol": symbol,
        "breakout_level": "200D" if bo200 else "50D" if bo50 else "20D" if bo20 else "NONE",
        "breakout_score": total_score,
        "continuation_prob": round(continuation_prob, 2),
        "volume_confirm": volume_confirm,
        "vol_ratio": round(vol_ratio, 2),
        "price_vs_ma20_pct": round(price_vs_ma20, 1),
        "price_vs_ma50_pct": round(price_vs_ma50, 1),
        "mom_5d_pct": round(mom_5d, 1) if not pd.isna(mom_5d) else 0,
        "mom_20d_pct": round(mom_20d, 1) if not pd.isna(mom_20d) else 0,
        "pullback_ratio": round(pullback_check, 2),
        "all_above_ma50": all_above_ma,
        "value_bn": round(latest["close"] * latest["volume"] * 1000 / 1e9, 1),
    }


def scan_breakout_opportunities(min_score: int = 40, top_n: int = 20) -> list:
    df = _load_universe()
    if df.empty:
        return []
    symbols = df["symbol"].unique().tolist()
    results = []
    for sym in symbols:
        score = compute_breakout_score(sym, df)
        if score.get("status") == "INSUFFICIENT_DATA":
            continue
        if score["breakout_score"] >= min_score:
            results.append(score)
    results.sort(key=lambda x: x["breakout_score"], reverse=True)
    return results[:top_n]


def get_breakout_market_context() -> dict:
    regime = detect_regime()
    regime_status = regime.get("status", "RANGING")

    df = _load_universe()
    if df.empty:
        return {"regime": regime_status, "status": "NO_DATA"}

    symbols = df["symbol"].unique().tolist()
    breakout_counts = {"20D": 0, "50D": 0, "200D": 0, "NONE": 0}
    total_scored = 0

    avg_score = 0
    for sym in symbols[:100]:
        score = compute_breakout_score(sym, df)
        if score.get("status") == "INSUFFICIENT_DATA":
            continue
        total_scored += 1
        avg_score += score["breakout_score"]
        lvl = score["breakout_level"]
        breakout_counts[lvl] = breakout_counts.get(lvl, 0) + 1

    avg_score = avg_score / max(1, total_scored)
    total_breakouts = breakout_counts["20D"] + breakout_counts["50D"] + breakout_counts["200D"]
    breakout_density = total_breakouts / max(1, total_scored)

    context = "LOW_BREAKOUT_ACTIVITY"
    if breakout_density > 0.3 and avg_score > 55:
        context = "HIGH_BREAKOUT_ACTIVITY"
    elif breakout_density > 0.15:
        context = "MODERATE_BREAKOUT_ACTIVITY"

    return {
        "regime": regime_status,
        "breakout_context": context,
        "avg_score": round(avg_score, 1),
        "breakout_density": round(breakout_density, 3),
        "breakout_distribution": breakout_counts,
        "symbols_scored": total_scored,
    }


def get_continuation_signal(symbol: str) -> str:
    score = compute_breakout_score(symbol)
    if score.get("status") == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"

    bsl = score["breakout_level"]
    bs = score["breakout_score"]
    vc = score["volume_confirm"]
    cp = score["continuation_prob"]
    all_ma = score["all_above_ma50"]

    if bsl == "200D" and vc and bs >= 70:
        return "STRONG_CONTINUATION_BUY"
    if bsl == "50D" and vc and bs >= 55:
        return "CONTINUATION_BUY"
    if bsl == "20D" and vc and bs >= 40 and all_ma:
        return "EARLY_BREAKOUT_BUY"
    if bsl != "NONE" and not vc:
        return "BREAKOUT_NO_VOLUME"
    if bsl == "NONE":
        return "NO_BREAKOUT"

    return "OBSERVE"


if __name__ == "__main__":
    import json
    context = get_breakout_market_context()
    print(f"Breakout Context: {json.dumps(context, ensure_ascii=False, indent=2)}")
    opps = scan_breakout_opportunities(5)
    print(f"Top Opportunities: {json.dumps(opps, ensure_ascii=False, indent=2)}")
