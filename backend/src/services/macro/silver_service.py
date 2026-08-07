"""
Silver Service — Domestic Silver from BTMC API + silver_service integration.

The BTMC scraping logic lives here (no dependency on vnstock). The
`btmc_silver_price` function was moved out of vnstock's
explorer/misc/gold_price.py so the commodity service stays independent
of the stock-scraping library.
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import requests


def _hydrate_path():
    if getattr(sys, "frozen", False):
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

logger = logging.getLogger(__name__)

BTMC_SILVER_URL = "http://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"


def btmc_silver_price(url: str = BTMC_SILVER_URL) -> pd.DataFrame:
    """Parse giá bạc từ API JSON Bảo Tín Minh Châu (VND/lượng).

    [LOCAL] Moved out of vnstock fork 3.4.0 into this service so the
    silver commodity domain no longer depends on the stock library.
    """
    response = requests.get(url)
    json_data = response.json()
    data_list = json_data["DataList"]["Data"]

    data = []
    for item in data_list:
        row_number = item["@row"]
        n_key = f"@n_{row_number}"
        k_key = f"@k_{row_number}"
        h_key = f"@h_{row_number}"
        pb_key = f"@pb_{row_number}"
        ps_key = f"@ps_{row_number}"
        pt_key = f"@pt_{row_number}"
        d_key = f"@d_{row_number}"
        name = item.get(n_key, "")
        if "BẠC" not in name.upper():
            continue
        buy_raw = item.get(pb_key, "0")
        sell_raw = item.get(ps_key, "0")
        data.append(
            {
                "name": name,
                "karat": item.get(k_key, ""),
                "gold_content": item.get(h_key, ""),
                "buy_price": float(buy_raw) * 10,
                "sell_price": float(sell_raw) * 10,
                "world_price": item.get(pt_key, ""),
                "time": item.get(d_key, ""),
            }
        )
    df = pd.DataFrame(data)
    df = df.sort_values(by=["sell_price"], ascending=False)
    return df


def get_silver_dashboard() -> dict:
    """Lấy dashboard giá bạc nội địa (BTMC)."""
    try:
        df = btmc_silver_price()
        if df is None or df.empty:
            return {"btmc_buy": 0, "btmc_sell": 0, "btmc_spread": 0, "brand": ""}
        main = df.iloc[0]
        buy = float(main["buy_price"])
        sell = float(main["sell_price"])
        return {
            "btmc_buy": buy,
            "btmc_sell": sell,
            "btmc_spread": round(sell - buy, 2),
            "brand": str(main["name"]),
        }
    except Exception as e:
        logger.error(f"Silver dashboard failed: {e}")
        return {"btmc_buy": 0, "btmc_sell": 0, "btmc_spread": 0, "brand": ""}
