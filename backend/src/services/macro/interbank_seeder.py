import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

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
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
from src.database.db_core import get_connection, save_data_upsert

logger = logging.getLogger(__name__)

INTERBANK_SOURCES = [
    {
        "name": "SBV",
        "url": "https://www.sbv.gov.vn/webcenter/portal/vi/menu/trangchu/dlieu/ls",
    },
]


def _get_latest_interbank_from_db() -> float | None:
    """Lấy INTERBANK_ON mới nhất từ DB để kiểm tra độ trễ."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value, date FROM macro_history WHERE variable = 'INTERBANK_ON' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return float(row[0])
    except Exception:
        return None


def refresh_interbank_rate() -> bool:
    """Cập nhật lãi suất liên ngân hàng VN (INTERBANK_ON).

    Nguồn: SBV công bố hàng ngày.
    Nếu không fetch được, giữ dữ liệu cũ — không xoá.

    Note: SBV chưa có REST API công khai.
    Dữ liệu hiện tại được cập nhật thủ công từ nguồn SBV.
    Hàm này là connector framework — sẵn sàng khi SBV mở API.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    new_rate = None

    existing = _get_latest_interbank_from_db()
    if existing is not None:
        logger.info(f"INTERBANK_ON hiện tại: {existing} (từ DB)")
        existing_date_row = None
        try:
            with get_connection() as conn:
                existing_date_row = conn.execute(
                    "SELECT date FROM macro_history WHERE variable = 'INTERBANK_ON' ORDER BY date DESC LIMIT 1"
                ).fetchone()
        except Exception:
            pass
        if existing_date_row:
            last_date = existing_date_row[0]
            days_old = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days
            logger.info(f"Dữ liệu INTERBANK_ON cũ {days_old} ngày (từ {last_date})")

    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get("https://portal.sbv.gov.vn/api/public/lai-suat-lien-ngan-hang", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                new_rate = float(data[0].get("rate", 0))
            elif isinstance(data, dict):
                new_rate = float(data.get("rate", 0))
    except Exception as e:
        logger.warning(f"SBV API không khả dụng: {e}")

    if new_rate is None:
        logger.info("Giữ INTERBANK_ON hiện tại (không có nguồn REAL mới).")
        return False

    df = pd.DataFrame([
        {"variable": "INTERBANK_ON", "date": today, "value": new_rate},
    ])

    try:
        with get_connection() as conn:
            save_data_upsert("macro_history", df, conn)
        logger.info(f"Đã cập nhật INTERBANK_ON: {new_rate}%")
        return True
    except Exception as e:
        logger.error(f"Seed INTERBANK_ON thất bại: {e}")
        return False
