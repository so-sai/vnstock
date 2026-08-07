"""
Portfolio Service Layer v1.0
Quản lý danh mục + Shadow Tracker reconciliation.
"""

import json
import logging
import os
import sys
from datetime import datetime
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
# Ensure backend/ is in sys.path so 'src' package is importable
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = str(backend_dir / "src" / "portfolio" / "my_portfolio.json")
STOP_LOSS_THRESHOLD = -5.0


def _load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_PATH):
        return {"cash": 0, "positions": []}
    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_portfolio(data: dict):
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _get_latest_price(symbol: str) -> float | None:
    try:
        with get_connection() as conn:
            row = pd.read_sql(
                f"""
                SELECT adj_close FROM daily_ohlcv
                WHERE symbol = '{symbol}'
                ORDER BY date DESC LIMIT 1
            """,
                conn,
            )
            if not row.empty:
                return float(row.iloc[0]["adj_close"])
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")
    return None


def get_portfolio_summary() -> dict:
    """
    Lấy tổng quan danh mục + P&L từ Shadow Tracker logic.
    """
    data = _load_portfolio()
    cash = data.get("cash", 0)
    positions = data.get("positions", [])

    total_cost = 0
    total_market_value = 0
    position_details = []
    alerts = []

    for pos in positions:
        sym = pos.get("symbol", "")
        qty = pos.get("quantity", 0)
        buy_price = pos.get("entry_price", 0)
        fee_rate = pos.get("fee_paid", 0.0015)

        buy_price_raw = buy_price * 1000
        cost_with_fee = qty * buy_price_raw * (1 + fee_rate)
        total_cost += cost_with_fee

        market_price = _get_latest_price(sym)
        if market_price is None:
            position_details.append(
                {
                    "symbol": sym,
                    "quantity": qty,
                    "entryPrice": buy_price,
                    "currentPrice": None,
                    "pnlPercent": None,
                    "pnlVnd": None,
                    "marketValue": None,
                    "alert": "No price data",
                }
            )
            continue

        market_value = qty * market_price
        total_market_value += market_value

        pnl_pct = ((market_price - buy_price_raw) / buy_price_raw) * 100 if buy_price_raw > 0 else 0
        pnl_vnd = market_value - cost_with_fee

        alert = None
        if pnl_pct <= STOP_LOSS_THRESHOLD:
            alert = f"Cắt lỗ: {pnl_pct:.1f}% (Ngưỡng: {STOP_LOSS_THRESHOLD}%)"

        position_details.append(
            {
                "symbol": sym,
                "quantity": qty,
                "entryPrice": buy_price,
                "currentPrice": market_price,
                "pnlPercent": round(pnl_pct, 2),
                "pnlVnd": round(pnl_vnd, 0),
                "marketValue": round(market_value, 0),
                "alert": alert,
            }
        )

        if alert:
            alerts.append({"symbol": sym, "message": alert})

    total_nav = total_market_value + cash
    total_pnl_pct = ((total_nav - total_cost) / total_cost * 100) if total_cost > 0 else 0
    cash_percent = (cash / total_nav * 100) if total_nav > 0 else 100

    return {
        "cash": round(cash, 0),
        "totalCost": round(total_cost, 0),
        "totalMarketValue": round(total_market_value, 0),
        "totalNav": round(total_nav, 0),
        "totalPnlPercent": round(total_pnl_pct, 2),
        "cashPercent": round(cash_percent, 1),
        "positions": position_details,
        "alerts": alerts,
        "stopLossThreshold": STOP_LOSS_THRESHOLD,
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def add_position(symbol: str, quantity: int, entry_price: float, fee_paid: float = 0.0015) -> dict:
    """Thêm vị thế mới vào danh mục."""
    data = _load_portfolio()
    data["positions"].append(
        {
            "symbol": symbol,
            "quantity": quantity,
            "entry_price": entry_price,
            "fee_paid": fee_paid,
            "added_at": datetime.now().strftime("%Y-%m-%d"),
        }
    )
    _save_portfolio(data)
    return get_portfolio_summary()


def remove_position(symbol: str) -> dict:
    """Xóa vị thế khỏi danh mục."""
    data = _load_portfolio()
    data["positions"] = [p for p in data["positions"] if p.get("symbol") != symbol]
    _save_portfolio(data)
    return get_portfolio_summary()


def update_cash(amount: float) -> dict:
    """Cập nhật số dư tiền mặt."""
    data = _load_portfolio()
    data["cash"] = amount
    _save_portfolio(data)
    return get_portfolio_summary()


def update_position(symbol: str, quantity: int | None = None, entry_price: float | None = None) -> dict:
    """Cập nhật vị thế (số lượng hoặc giá vốn)."""
    data = _load_portfolio()
    for pos in data["positions"]:
        if pos.get("symbol") == symbol:
            if quantity is not None:
                pos["quantity"] = quantity
            if entry_price is not None:
                pos["entry_price"] = entry_price
            break
    else:
        raise ValueError(f"Không tìm thấy vị thế {symbol}")
    _save_portfolio(data)
    return get_portfolio_summary()
