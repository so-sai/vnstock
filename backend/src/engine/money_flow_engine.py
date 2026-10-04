import sys
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
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
from datetime import datetime
from typing import Any

import pandas as pd

from src.database.db_core import get_connection, save_data_upsert
from src.database.foreign_flow_guard import DAILY_FLOW_FILTER, guard_foreign_frame
from src.providers.vnstock_provider import VnstockProvider
from src.utils.defense import CircuitBreaker


class MoneyFlowEngine:
    """
    Engine trích xuất và tính toán dòng tiền Khối ngoại (Money Flow).
    Sử dụng chiến lược Snapshot Accumulation và Elite Defense Caching.
    """

    _session_cache: dict[str, dict[str, Any]] = {}

    def __init__(self, source: str = "VCI", show_log: bool = False) -> None:
        self.source: str = source
        self.show_log: bool = show_log

    def get_foreign_snapshot(self, symbol: str) -> dict[str, Any] | None:
        if symbol in self._session_cache:
            cache_entry = self._session_cache[symbol]
            if cache_entry.get("date") == datetime.now().strftime("%Y-%m-%d"):
                if self.show_log:
                    print(f"⚡ [Cache Hit] Reusing snapshot for {symbol}")
                return cache_entry

        if not CircuitBreaker.is_available(self.source):
            if self.show_log:
                print(f"🛑 [Circuit Breaker] Skipping {symbol} due to cooldown on {self.source}")
            return None

        try:
            cp = VnstockProvider(source=self.source)
            stats = cp.trading_stats(symbol, show_log=self.show_log)
            if stats is None or stats.empty:
                return None
            stats_row = stats.iloc[0]
            f_buy = stats_row.get("foreign_buy_volume")
            f_sell = stats_row.get("foreign_sell_volume")
            if f_buy is not None and f_sell is not None:
                # Dòng NGÀY: net = KL mua ngoại - KL bán ngoại
                f_vol: int = int(f_buy)
                net_vol: int = int(f_buy) - int(f_sell)
            else:
                # vnstock trading_stats(foreign_volume) = KL ngoại ròng của phiên
                f_vol = int(stats_row.get("foreign_volume", 0) or 0)
                net_vol = f_vol
            snapshot = {
                "symbol": symbol,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "foreign_vol": f_vol,
                "net_vol": net_vol,
            }
            self._session_cache[symbol] = snapshot
            return snapshot
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            err_str = str(e)
            if "429" in err_str or "Too Many" in err_str:
                CircuitBreaker.report_failure(self.source)
            if self.show_log:
                print(f"❌ Error fetching foreign snapshot for {symbol}: {e}")
            return None

    def update_foreign_history(self, symbols_list: list[str]) -> pd.DataFrame:
        import random
        import time

        records: list[dict[str, Any]] = []
        for idx, sym in enumerate(symbols_list):
            snapshot = self.get_foreign_snapshot(sym)
            if snapshot:
                records.append(snapshot)
            if idx > 0 and idx % 10 == 0:
                time.sleep(random.uniform(0.5, 1.5))
        if not records:
            return pd.DataFrame()
        df_new: pd.DataFrame = pd.DataFrame(records)
        if "net_vol" not in df_new.columns:
            df_new["net_vol"] = 0
        target_date = str(df_new["date"].iloc[0])
        with get_connection() as conn:
            closes: dict[str, float] = {}
            for sym in df_new["symbol"]:
                r = conn.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol = ? AND date = ?",
                    (str(sym), target_date),
                ).fetchone()
                closes[str(sym)] = float(r[0]) if r and r[0] else 0.0
            df_new["close"] = [closes.get(str(s), 0.0) for s in df_new["symbol"]]
            # net_value (tỷ đồng) = dòng NGÀY * giá / 1e9 — KHÔNG diff snapshot, KHÔNG ×1000
            df_new["net_value"] = (df_new["net_vol"].astype(float) * df_new["close"]) / 1e9
            clean, _rep = guard_foreign_frame(df_new, target_date)  # raise nếu dính chữ ký "level"
            save_data_upsert("market_foreign_history", df_new[["symbol", "date", "foreign_vol"]], conn)
            for _, row in clean.iterrows():
                conn.execute(
                    "UPDATE market_foreign_history SET net_vol = ?, net_value = ?, flow_kind = 'daily' "
                    "WHERE symbol = ? AND date = ?",
                    (int(row["net_vol"]), float(row["net_value"]), str(row["symbol"]), target_date),
                )
            conn.commit()
        return df_new

    def get_accumulation(self, symbol: str, days: int = 10) -> float:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(net_value) FROM (SELECT net_value FROM market_foreign_history "
                f"WHERE symbol = ? AND {DAILY_FLOW_FILTER} ORDER BY date DESC LIMIT ?)",
                (symbol, days),
            )
            result = cursor.fetchone()
            return float(result[0]) if result and result[0] is not None else 0.0


if __name__ == "__main__":
    engine = MoneyFlowEngine(show_log=True)
    engine.update_foreign_history(["HPG", "SSI", "VNM"])
    acc = engine.get_accumulation("HPG", 10)
    print(f"✅ HPG 10D Foreign Accumulation: {acc:.2f} tỷ VNĐ")
