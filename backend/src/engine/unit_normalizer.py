import sys
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
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
from datetime import datetime
from typing import List, Optional

import pandas as pd

from src.database.db_core import get_connection, save_data_upsert


class UnitNormalizer:
    """
    [DEPRECATED] Chuẩn hóa biến vĩ mô — không còn dùng OMO/INTERBANK_ON giả.
    Chuyển sang macro_service.py pipeline với REAL/NO_DATA/ESTIMATED.
    Giữ lại để tương thích ngược, sẽ xóa trong Phase 2.
    """

    def __init__(self, show_log: bool = False) -> None:
        self.show_log: bool = show_log

    def update_macro_data(self, variable_name: str, value: float, date: Optional[str] = None) -> None:
        if date is None: date = datetime.now().strftime('%Y-%m-%d')
        df: pd.DataFrame = pd.DataFrame([{'variable': variable_name, 'date': date, 'value': float(value)}])
        with get_connection() as conn:
            save_data_upsert("macro_history", df, conn)
        if self.show_log: print(f"✅ Macro updated: {variable_name} = {value} ({date})")

    def get_macro_trend(self, variable_name: str, window: int = 5) -> str:
        with get_connection() as conn:
            query: str = f"SELECT value FROM macro_history WHERE variable = '{variable_name}' ORDER BY date DESC LIMIT {window}"
            cursor = conn.cursor()
            cursor.execute(query)
            values: List[float] = [float(r[0]) for r in cursor.fetchall()]
            if len(values) < 2: return "NEUTRAL"
            current: float = values[0]
            avg: float = sum(values) / len(values)
            if variable_name == "OMO": return "BULLISH" if current > 0.0 else "BEARISH"
            elif variable_name in ["INTERBANK_ON", "USD_VND"]: return "BEARISH" if current > avg else "BULLISH"
            return "NEUTRAL"

    def get_market_condition(self) -> str:
        return "NO_DATA"

