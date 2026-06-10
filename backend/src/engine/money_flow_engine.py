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
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
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
import sqlite3

# Ensure libs/vnstock is in sys.path
if str(PROJECT_ROOT / "libs" / "vnstock") not in sys.path:
    sys.path.append(str(PROJECT_ROOT / "libs" / "vnstock"))

from vnstock import Company
from src.utils.defense import CircuitBreaker

class MoneyFlowEngine:
    """
    Engine trích xuất và tính toán dòng tiền Khối ngoại (Money Flow).
    Sử dụng chiến lược Snapshot Accumulation và Elite Defense Caching.
    """
    
    _session_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self, source: str = "VCI", show_log: bool = False) -> None:
        self.source: str = source
        self.show_log: bool = show_log

    def get_foreign_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        if symbol in self._session_cache:
            cache_entry = self._session_cache[symbol]
            if cache_entry.get('date') == datetime.now().strftime('%Y-%m-%d'):
                if self.show_log:
                    print(f"⚡ [Cache Hit] Reusing snapshot for {symbol}")
                return cache_entry

        if not CircuitBreaker.is_available(self.source):
            if self.show_log:
                print(f"🛑 [Circuit Breaker] Skipping {symbol} due to cooldown on {self.source}")
            return None

        try:
            cp = Company(source=self.source, symbol=symbol, show_log=self.show_log)
            stats = cp.trading_stats()
            if stats is None or stats.empty: return None
            f_vol_val = stats.iloc[0].get('foreign_volume', 0)
            f_vol: int = int(f_vol_val)
            snapshot = {'symbol': symbol, 'date': datetime.now().strftime('%Y-%m-%d'), 'foreign_vol': f_vol}
            self._session_cache[symbol] = snapshot
            return snapshot
        except Exception as e:
            if self.show_log:
                print(f"❌ Error fetching foreign snapshot for {symbol}: {e}")
            return None

    def update_foreign_history(self, symbols_list: List[str]) -> pd.DataFrame:
        records: List[Dict[str, Any]] = []
        for sym in symbols_list:
            snapshot = self.get_foreign_snapshot(sym)
            if snapshot: records.append(snapshot)
        if not records: return pd.DataFrame()
        df_new: pd.DataFrame = pd.DataFrame(records)
        with get_connection() as conn:
            save_data_upsert("market_foreign_history", df_new[['symbol', 'date', 'foreign_vol']], conn)
            for _, row in df_new.iterrows():
                self._calculate_net_flow(symbol=str(row['symbol']), date=str(row['date']), current_vol=int(row['foreign_vol']), conn=conn)
        return df_new

    def _calculate_net_flow(self, symbol: str, date: str, current_vol: int, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        cursor.execute("SELECT foreign_vol FROM market_foreign_history WHERE symbol = ? AND date < ? ORDER BY date DESC LIMIT 1", (symbol, date))
        result = cursor.fetchone()
        if result:
            prev_vol_val: int = int(result[0])
            net_vol: int = current_vol - prev_vol_val
            cursor.execute("SELECT close FROM daily_ohlcv WHERE symbol = ? AND date = ?", (symbol, date))
            price_res = cursor.fetchone()
            price: float = float(price_res[0]) if price_res else 0.0
            net_value: float = (net_vol * price * 1000.0) / 1e9
            cursor.execute("UPDATE market_foreign_history SET net_vol = ?, net_value = ? WHERE symbol = ? AND date = ?", (net_vol, net_value, symbol, date))
            conn.commit()

    def get_accumulation(self, symbol: str, days: int = 10) -> float:
        with get_connection() as conn:
            query = f"SELECT SUM(net_value) FROM (SELECT net_value FROM market_foreign_history WHERE symbol = '{symbol}' ORDER BY date DESC LIMIT {days})"
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            return float(result[0]) if result[0] is not None else 0.0

if __name__ == "__main__":
    engine = MoneyFlowEngine(show_log=True)
    engine.update_foreign_history(['HPG', 'SSI', 'VNM'])
    acc = engine.get_accumulation('HPG', 10)
    print(f"✅ HPG 10D Foreign Accumulation: {acc:.2f} tỷ VNĐ")

