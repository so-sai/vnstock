"""
Exposure Engine v1.0 — HoldingsView Layer.
Aggregates portfolio data from JSON + SQLite into exposure metrics.
PURE AGGREGATION: no scoring, no recommendation, no interpretation.
"""

import json
import logging
import os
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import numpy as np
import pandas as pd
from core.holdings.models import HoldingPosition, SectorExposure

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = os.path.join(str(PROJECT_ROOT), "backend", "src", "portfolio", "my_portfolio.json")


def load_portfolio_positions() -> list[HoldingPosition]:
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Cannot load portfolio: {e}")
        return []

    cash = data.get("cash", 0)
    raw_positions = data.get("positions", [])
    if not raw_positions:
        return []

    sector_map = _load_sector_map()
    prices = _load_current_prices()
    volume = _load_volume_profiles()

    positions = []
    for pos in raw_positions:
        sym = pos.get("symbol", "").strip().upper()
        qty = pos.get("quantity", 0)
        entry_price_k = pos.get("entry_price", 0)
        entry_price_raw = entry_price_k * 1000
        market_price = prices.get(sym, entry_price_raw)
        market_value = qty * market_price
        pnl_pct = ((market_price - entry_price_raw) / entry_price_raw * 100) if entry_price_raw > 0 else 0
        sector = sector_map.get(sym, "Khác")
        vol_profile = volume.get(sym, {})
        adv = vol_profile.get("adv_20d", 0)
        liquidity_score = min(market_value / (adv * 1000) if adv > 0 else 999, 100)

        positions.append(
            HoldingPosition(
                symbol=sym,
                quantity=qty,
                avg_price=entry_price_k,
                market_price=round(market_price / 1000, 2),
                market_value=market_value,
                portfolio_weight=0.0,
                sector=sector,
                pnl_pct=round(pnl_pct, 2),
                liquidity_score=round(liquidity_score, 2),
            )
        )

    total_nav = cash + sum(p.market_value for p in positions)
    for p in positions:
        p.portfolio_weight = round((p.market_value / total_nav * 100), 2) if total_nav > 0 else 0.0

    return positions


def compute_sector_exposure(positions: list[HoldingPosition]) -> list[SectorExposure]:
    if not positions:
        return []
    sectors = {}
    for p in positions:
        if p.sector not in sectors:
            sectors[p.sector] = {"weight": 0.0, "count": 0, "liquidity": []}
        sectors[p.sector]["weight"] += p.portfolio_weight
        sectors[p.sector]["count"] += 1
        if p.liquidity_score is not None:
            sectors[p.sector]["liquidity"].append(p.liquidity_score)

    result = []
    for sector, data in sectors.items():
        avg_lq = np.mean(data["liquidity"]) if data["liquidity"] else 1.0
        result.append(
            SectorExposure(
                sector=sector,
                weight=round(data["weight"], 2),
                position_count=data["count"],
                liquidity_quality=round(float(avg_lq), 2),
            )
        )
    return sorted(result, key=lambda x: x.weight, reverse=True)


def compute_concentration(positions: list[HoldingPosition]) -> dict:
    if not positions:
        return {"top3_weight": 0.0, "top5_weight": 0.0, "n_positions": 0}
    sorted_pos = sorted(positions, key=lambda p: p.portfolio_weight, reverse=True)
    return {
        "top3_weight": round(sum(p.portfolio_weight for p in sorted_pos[:3]), 2),
        "top5_weight": round(sum(p.portfolio_weight for p in sorted_pos[:5]), 2),
        "n_positions": len(positions),
    }


def compute_beta_exposure(positions: list[HoldingPosition]) -> dict:
    if not positions:
        return {"high_beta_weight": 0.0, "low_beta_weight": 0.0}
    high_beta = sum(p.portfolio_weight for p in positions if _is_high_beta(p.symbol))
    low_beta = sum(p.portfolio_weight for p in positions if not _is_high_beta(p.symbol))
    return {
        "high_beta_weight": round(high_beta, 2),
        "low_beta_weight": round(low_beta, 2),
    }


def compute_liquidity_fragility(positions: list[HoldingPosition]) -> dict:
    illiquid = [p for p in positions if p.liquidity_score is not None and p.liquidity_score > 5.0]
    fragile = [p for p in positions if p.liquidity_score is not None and p.liquidity_score > 20.0]
    illiquid_weight = sum(p.portfolio_weight for p in illiquid)
    fragile_weight = sum(p.portfolio_weight for p in fragile)
    return {
        "illiquid_weight": round(illiquid_weight, 2),
        "fragile_weight": round(fragile_weight, 2),
        "illiquid_count": len(illiquid),
        "fragile_count": len(fragile),
    }


def compute_exposure_summary() -> dict:
    positions = load_portfolio_positions()
    sector_exposure = compute_sector_exposure(positions)
    concentration = compute_concentration(positions)
    beta_exp = compute_beta_exposure(positions)
    liquidity = compute_liquidity_fragility(positions)
    return {
        "positions": [p.model_dump() for p in positions],
        "sector_exposure": [s.model_dump() for s in sector_exposure],
        "concentration": concentration,
        "beta_exposure": beta_exp,
        "liquidity_fragility": liquidity,
        "cash": _load_cash(),
    }


# ───────────────────────────────
# Internal helpers
# ───────────────────────────────


def _load_cash() -> float:
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            return json.load(f).get("cash", 0)
    except Exception:
        return 0.0


def _load_sector_map() -> dict:
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
        return {row["symbol"]: row["icb_name2"] or "Khác" for _, row in df.iterrows()}
    except Exception:
        return {}


def _load_current_prices() -> dict:
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT symbol, adj_close FROM daily_ohlcv WHERE date = (SELECT MAX(date) FROM daily_ohlcv)", conn
            )
        return {row["symbol"]: float(row["adj_close"]) for _, row in df.iterrows()}
    except Exception:
        return {}


def _load_volume_profiles() -> dict:
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT symbol, date, volume FROM daily_ohlcv WHERE date >= date('now', '-30 days') ORDER BY date", conn
            )
        df = df.assign(adv_20d=df.groupby("symbol")["volume"].transform(lambda x: x.rolling(20, min_periods=5).mean()))
        latest = df[df["date"] == df.groupby("symbol")["date"].transform("max")]
        return {row["symbol"]: {"adv_20d": float(row["adv_20d"] or 0)} for _, row in latest.iterrows()}
    except Exception:
        return {}


def _is_high_beta(symbol: str) -> bool:
    """Proxy beta classification using sector."""
    high_beta_sectors = {"Dịch vụ tài chính", "Công nghệ", "Bất động sản", "Xây dựng và Vật liệu"}
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT icb_name2 FROM symbol_industry WHERE symbol = ?", conn, params=(symbol,))
            if not df.empty:
                sector = str(df.iloc[0]["icb_name2"] or "")
                return sector in high_beta_sectors
    except Exception:
        pass
    return False


if __name__ == "__main__":
    import json

    summary = compute_exposure_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
