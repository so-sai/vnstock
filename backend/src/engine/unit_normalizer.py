import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Any
from src.database.db_core import get_connection, save_data_upsert

class UnitNormalizer:
    """
    Chuẩn hóa các biến vĩ mô (Nấc 0) và cung cấp tín hiệu "Nguyên nhân" (Cause).
    Đạt chuẩn Python 3.14 Strict Typing.
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
        omo_trend: str = self.get_macro_trend("OMO")
        interbank_trend: str = self.get_macro_trend("INTERBANK_ON")
        if omo_trend == "BULLISH" and interbank_trend == "BULLISH": return "EXCELLENT"
        elif omo_trend == "BEARISH" or interbank_trend == "BEARISH": return "CAUTION"
        else: return "NORMAL"

if __name__ == "__main__":
    norm = UnitNormalizer(show_log=True)
    norm.update_macro_data("OMO", 5000.0)
    print(f"🌍 Market Condition (Phase 0): {norm.get_market_condition()}")

