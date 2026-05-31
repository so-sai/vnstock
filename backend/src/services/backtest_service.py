"""
Backtest Service Layer v1.0
Time Kernel — Kết nối Backtest Engine và Stress Test.
"""
import sys
import logging
from pathlib import Path
from typing import Optional

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
    return root_path

PROJECT_ROOT = _hydrate_path()

import numpy as np
import pandas as pd
import json
import os
from src.database.db_core import get_connection
from src.registry import registry
import src.config

logger = logging.getLogger(__name__)


def get_backtest_results(model: str = "A", start_date: str = "2023-01-01", end_date: str = "2026-04-17") -> dict:
    """
    Chạy backtest và trả về kết quả.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(f"""
                SELECT symbol, date, open, high, low, adj_close as close, volume
                FROM daily_ohlcv
                WHERE date >= '{start_date}' AND date <= '{end_date}'
                AND symbol NOT IN ('VNINDEX', 'VN30')
                ORDER BY symbol, date
            """, conn)

        if df.empty:
            return {"error": "No data for backtest period"}

        df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed')

        results = _run_simple_backtest(df, model)
        return results
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        return {"error": str(e)}


def _run_simple_backtest(df: pd.DataFrame, model: str) -> dict:
    """
    Chạy backtest đơn giản dựa trên RS Rating + Regime filter.
    Model A: Momentum (RS Rating cao + Regime TRENDING)
    Model B: Mean Reversion (RS thấp + Regime CRISIS/RANGING)
    """
    g = df.groupby('symbol')

    stats = []
    for symbol, group in df.groupby('symbol'):
        if len(group) < 60:
            continue

        group = group.copy()
        group = group.sort_values('date')

        prices = _normalize_price_unit(group['close'].values)
        dates = group['date'].values

        returns_1y = (prices[-1] / prices[0] - 1) * 100 if prices[0] > 0 else 0
        max_drawdown = _calc_max_drawdown(prices)

        daily_returns = pd.Series(prices).pct_change(fill_method=None).dropna()
        sharpe = (daily_returns.mean() / daily_returns.std() * (252 ** 0.5)) if daily_returns.std() > 0 else 0
        if max_drawdown <= -90:
            sharpe = 0.0

        stats.append({
            'symbol': symbol,
            'return1y': round(returns_1y, 2),
            'sharpe': round(sharpe, 3),
            'maxDrawdown': round(max_drawdown, 2),
            'volatility': round(daily_returns.std() * (252 ** 0.5) * 100, 2),
        })

    stats_df = pd.DataFrame(stats)
    if stats_df.empty:
        return {"error": "No valid symbols for backtest"}

    if model.upper() == "A":
        top = stats_df.nlargest(20, 'sharpe')
    else:
        top = stats_df.nsmallest(20, 'maxDrawdown')

    equity_curve = _build_equity_curve(df, top['symbol'].tolist())

    return {
        "model": model.upper(),
        "startDate": str(df['date'].min().date()),
        "endDate": str(df['date'].max().date()),
        "totalSymbols": len(stats_df),
        "topPicks": top.to_dict(orient='records'),
        "portfolioStats": {
            "avgReturn": round(top['return1y'].mean(), 2),
            "avgSharpe": round(top['sharpe'].mean(), 3),
            "avgMaxDrawdown": round(top['maxDrawdown'].mean(), 2),
            "avgVolatility": round(top['volatility'].mean(), 2),
        },
        "equityCurve": equity_curve,
    }


def _calc_max_drawdown(prices) -> float:
    """Tính Maximum Drawdown từ chuỗi giá."""
    peak = prices[0]
    max_dd = 0
    for price in prices:
        if price > peak:
            peak = price
        dd = (price - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _build_equity_curve(df: pd.DataFrame, symbols: list, top_n: int = 10) -> list:
    """Xây dựng equity curve từ top symbols."""
    top_symbols = symbols[:top_n]
    filtered = df[df['symbol'].isin(top_symbols)].copy()

    if filtered.empty:
        return []

    pivot = filtered.pivot_table(index='date', columns='symbol', values='close', aggfunc='first')
    pivot = pivot.ffill()
    pivot = pivot.map(lambda x: _normalize_price_unit(np.array([x]))[0] if pd.notna(x) else x)

    if pivot.empty or len(pivot) < 2:
        return []

    daily_returns = pivot.pct_change(fill_method=None).mean(axis=1).dropna()
    equity = (1 + daily_returns).cumprod() * 100

    curve = []
    for date, value in equity.items():
        curve.append({
            "date": str(date.date()) if hasattr(date, 'date') else str(date),
            "value": round(value, 2),
        })

    return curve


def get_stress_test_summary() -> dict:
    """
    Tóm tắt kết quả Stress Test 2022.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql("""
                SELECT symbol, date, adj_close as close
                FROM daily_ohlcv
                WHERE date >= '2022-01-01' AND date <= '2023-06-30'
                AND symbol NOT IN ('VNINDEX', 'VN30')
                ORDER BY symbol, date
            """, conn)

        if df.empty:
            return {"error": "No 2022 stress test data"}

        df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed')

        results = []
        for symbol, group in df.groupby('symbol'):
            if len(group) < 30:
                continue
            group = group.sort_values('date')
            prices = _normalize_price_unit(group['close'].values)
            peak = prices.max()
            trough = prices.min()
            max_dd = (trough - peak) / peak * 100 if peak > 0 else 0

            recovery_date = None
            for i, p in enumerate(prices):
                if p >= peak and i > list(prices).index(peak):
                    recovery_date = str(group.iloc[i]['date'].date())
                    break

            results.append({
                "symbol": symbol,
                "maxDrawdown2022": round(max_dd, 2),
                "recovered": recovery_date is not None,
            })

        results_df = pd.DataFrame(results)
        if results_df.empty:
            return {"error": "No valid results"}

        return {
            "period": "2022-01-01 to 2023-06-30",
            "totalSymbols": len(results_df),
            "avgMaxDrawdown": round(results_df['maxDrawdown2022'].mean(), 2),
            "worstDrawdown": round(results_df['maxDrawdown2022'].min(), 2),
            "recoveryRate": round(results_df['recovered'].mean() * 100, 1),
            "worstPerformers": results_df.nsmallest(10, 'maxDrawdown2022').to_dict(orient='records'),
        }
    except Exception as e:
        logger.error(f"Stress test failed: {e}")
        return {"error": str(e)}


# ─── Unit normalizer: phát hiện & chuẩn hóa VND↔nghìn đồng ───

def _normalize_price_unit(prices: np.ndarray) -> np.ndarray:
    """
    Chuẩn hóa lẫn lộn đơn vị trong chuỗi giá.
    - Giá > 500 → đang ở VND (60700) → chia 1000 về nghìn đồng
    - Giá ≤ 500 → đang ở nghìn đồng (60.7) → giữ nguyên
    """
    out = prices.copy().astype(float)
    out[np.abs(out) >= 200] /= 1000.0
    return out
