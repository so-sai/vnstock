"""
Silver Service — Domestic Silver from BTMC API + silver_service integration.
"""
import logging
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent.parent
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
    vnstock_path = str(root_path / "backend" / "libs" / "vnstock")
    if vnstock_path not in sys.path:
        sys.path.insert(0, vnstock_path)
    return root_path

PROJECT_ROOT = _hydrate_path()

from vnstock.explorer.misc.gold_price import btmc_silver_price

logger = logging.getLogger(__name__)


def get_silver_dashboard() -> dict:
    """Lấy dashboard giá bạc nội địa (BTMC)."""
    try:
        df = btmc_silver_price()
        if df is None or df.empty:
            return {"btmc_buy": 0, "btmc_sell": 0, "btmc_spread": 0, "brand": ""}
        main = df.iloc[0]
        buy = float(main['buy_price'])
        sell = float(main['sell_price'])
        return {
            "btmc_buy": buy,
            "btmc_sell": sell,
            "btmc_spread": round(sell - buy, 2),
            "brand": str(main['name']),
        }
    except Exception as e:
        logger.error(f"Silver dashboard failed: {e}")
        return {"btmc_buy": 0, "btmc_sell": 0, "btmc_spread": 0, "brand": ""}
