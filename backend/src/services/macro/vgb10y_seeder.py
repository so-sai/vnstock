import logging
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
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import pandas as pd
import requests

from src.database.db_core import get_connection, save_data_upsert

logger = logging.getLogger(__name__)

WORLD_BANK_URL = "https://api.worldbank.org/v2/country/VN/indicator/FR.INR.LNDP?format=json&per_page=5&sort=desc"
FALLBACK_MULTIPLIER = 0.65


def _parse_world_bank_vgb10y(data: list) -> float | None:
    """Parse lending interest rate từ World Bank API response."""
    try:
        records = data[1]
        if not records:
            return None
        valid = [r for r in records if r.get("value") is not None]
        if not valid:
            return None
        return float(valid[0]["value"])
    except IndexError, TypeError, ValueError:
        return None


def _get_us10y_from_db() -> float | None:
    """Lấy US10Y mới nhất từ macro_history để tính fallback."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM macro_history WHERE variable = 'US10Y' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            return float(row[0]) if row else None
    except Exception:
        return None


def seed_vgb10y() -> bool:
    """Cào lợi suất trái phiếu chính phủ VN 10 năm thực tế.

    Nguồn 1 (REAL): World Bank API — FR.INR.LNDP (lending interest rate).
    Nguồn 2 (ESTIMATED): US10Y * 0.65 (fallback, giữ trong macro_service.py).

    Hàm này chỉ seed khi có REAL, không ghi đè fallback.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    real_yield = None

    try:
        resp = requests.get(WORLD_BANK_URL, timeout=15)
        if resp.status_code == 200:
            real_yield = _parse_world_bank_vgb10y(resp.json())
            if real_yield is not None:
                logger.info(f"VGB10Y từ World Bank: {real_yield}%")
    except Exception as e:
        logger.warning(f"World Bank API failed: {e}")

    if real_yield is None:
        logger.info("Không lấy được VGB10Y thực tế, giữ fallback ESTIMATED.")
        return False

    df = pd.DataFrame(
        [
            {"variable": "VGB10Y", "date": today, "value": real_yield},
            {"variable": "VGB10Y_STATUS", "date": today, "value": "REAL"},
        ]
    )

    try:
        with get_connection() as conn:
            save_data_upsert("macro_history", df, conn)
        logger.info(f"Đã seed VGB10Y REAL: {real_yield}%")
        return True
    except Exception as e:
        logger.error(f"Seed VGB10Y thất bại: {e}")
        return False
