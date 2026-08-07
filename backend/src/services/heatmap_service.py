import logging
import os
import sys
from pathlib import Path


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
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()

import json

import numpy as np
import pandas as pd

import src.config
from src.database.db_core import get_connection

logger = logging.getLogger(__name__)


def get_heatmap_data(top_n: int = 50, history_days: int = 10) -> list:
    """
    Tính ma trận nhiệt RS lịch sử cho top N mã.
    Mỗi mã trả về mảng RS proxy cho `history_days` phiên gần nhất.
    RS proxy = percent rank của 10-day ROC trong từng phiên.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT symbol, date, adj_close AS close
                FROM daily_ohlcv
                ORDER BY symbol, date
            """,
                conn,
            )
    except Exception as e:
        logger.error(f"Error fetching OHLCV for heatmap: {e}")
        return []

    if df.empty:
        return []

    df = df.copy()
    df = df.assign(date=pd.to_datetime(df["date"], format="mixed"))
    df = df.sort_values(["symbol", "date"])

    g = df.groupby("symbol")
    df = df.assign(roc_10d=g["close"].transform(lambda x: x.pct_change(10, fill_method=None)))

    trade_dates = sorted(df["date"].unique())
    recent_dates = trade_dates[-history_days:]

    rs_data = _load_rs_data()

    result = []
    for symbol in sorted(df["symbol"].unique()):
        sym_df = df[df["symbol"] == symbol]
        if len(sym_df) < 10:
            continue

        rs_info = rs_data.get(symbol, {})
        rs_rating = int(rs_info.get("rs_rating", 0))
        change_1y = float(rs_info.get("change_1y", 0))

        history = []
        for d in recent_dates:
            row = sym_df[sym_df["date"] == d]
            if not row.empty:
                val = row["roc_10d"].iloc[-1]
                history.append(round(float(val) * 100, 2) if pd.notna(val) else 0.0)
            else:
                history.append(0.0)

        result.append(
            {
                "symbol": symbol,
                "rsRating": rs_rating,
                "change1y": round(change_1y, 2),
                "rsHistory": history,
            }
        )

    result.sort(key=lambda x: x["rsRating"], reverse=True)

    if not result:
        return []

    history_by_day = {}
    for item in result:
        for i, val in enumerate(item["rsHistory"]):
            if i not in history_by_day:
                history_by_day[i] = []
            history_by_day[i].append(val)

    min_vals = {}
    max_vals = {}
    for i, vals in history_by_day.items():
        arr = np.array(vals)
        mn, mx = float(arr.min()), float(arr.max())
        min_vals[i] = mn
        max_vals[i] = mx
        if mx - mn < 0.001:
            max_vals[i] = mn + 1.0

    for item in result:
        normalized = []
        for i, val in enumerate(item["rsHistory"]):
            norm = (val - min_vals[i]) / (max_vals[i] - min_vals[i]) * 99
            normalized.append(round(norm))
        item["rsHistory"] = normalized

    return result[:top_n]


def get_breadth_stacked_history(limit: int = 60) -> list:
    """
    Tính lịch sử độ rộng 3 lớp: above MA20 %, between MA20-MA50 %, below MA50 %.
    """
    try:
        with get_connection() as conn:
            dates = pd.read_sql(
                "SELECT DISTINCT date FROM daily_ohlcv ORDER BY date DESC LIMIT ?", conn, params=(limit + 200,)
            )
        if dates.empty:
            return []
        min_date = dates["date"].min()

        with get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT symbol, date, close, volume
                FROM daily_ohlcv
                WHERE date >= ?
                ORDER BY symbol, date
            """,
                conn,
                params=(min_date,),
            )
    except Exception as e:
        logger.error(f"Error fetching OHLCV for breadth: {e}")
        return []

    if df.empty:
        return []

    df = df.copy()
    date_parsed = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    df = df.assign(date=date_parsed, date_key=date_parsed.dt.strftime("%Y-%m-%d"))
    df = df.sort_values(["symbol", "date"])

    g = df.groupby("symbol")
    df["ma20"] = g["close"].transform(lambda x: x.rolling(20).mean())
    df["ma50"] = g["close"].transform(lambda x: x.rolling(50).mean())
    df["avg_vol_20d"] = g["volume"].transform(lambda x: x.rolling(20).mean())

    liquidity = 50000
    df = df[df["avg_vol_20d"] >= liquidity].copy()
    df = df.dropna(subset=["ma20", "ma50"])

    results = []
    for date_key, group in df.groupby("date_key"):
        total = len(group)
        if total < 10:
            continue
        above_ma20_mask = group["close"] > group["ma20"]
        below_ma50_mask = group["close"] < group["ma50"]
        above_ma20 = int(above_ma20_mask.sum())
        below_ma50 = int((~above_ma20_mask & below_ma50_mask).sum())
        between = total - above_ma20 - below_ma50

        results.append(
            {
                "date": date_key,
                "aboveMa20": round(above_ma20 / total * 100, 1),
                "between": round(between / total * 100, 1),
                "belowMa50": round(below_ma50 / total * 100, 1),
            }
        )

    results.sort(key=lambda x: x["date"])
    return results[-limit:]


def _load_rs_data() -> dict:
    rs_path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    if not os.path.exists(rs_path):
        return {}
    try:
        with open(rs_path, encoding="utf-8") as f:
            data = json.load(f)
        return {item["symbol"]: item for item in data}
    except Exception as e:
        logger.error(f"Error loading RS data: {e}")
        return {}
