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
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from src.engine.money_flow_engine import MoneyFlowEngine
from src.engine.rs_ranker import load_rs_data
from src.engine.unit_normalizer import UnitNormalizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TacticalSignal:
    symbol: str
    rs_score: float
    rvol: float
    foreign_10d_acc: float
    market_phase: str
    breadth_pct: str
    action: str

class CommanderDataError(Exception): pass

class StrategyCommander:
    """Bộ chỉ huy tối cao Alpha Brain (v2.0) - Đạt chuẩn Python 3.14."""

    def __init__(self, show_log: bool = False) -> None:
        self.show_log: bool = show_log
        self.money_flow: MoneyFlowEngine = MoneyFlowEngine(show_log=self.show_log)
        self.macro: UnitNormalizer = UnitNormalizer(show_log=self.show_log)
        rs_raw_data = load_rs_data()
        self.rs_data: Dict[str, Any] = rs_raw_data or {}

    def analyze_battle_map(self, symbols_list: List[str]) -> pd.DataFrame:
        if not symbols_list: raise CommanderDataError("❌ Symbols list empty.")
        market_condition: str = self.macro.get_market_condition()
        strong_stocks: List[str] = [s for s, d in self.rs_data.items() if isinstance(d, dict) and float(d.get('rs_rating', 0.0)) > 80.0]
        breadth_ratio: float = len(strong_stocks) / len(self.rs_data) if self.rs_data else 0.0
        breadth_str: str = f"{breadth_ratio * 100:.1f}%"
        results: List[TacticalSignal] = []
        for symbol in symbols_list:
            stock_rs: Dict[str, Any] = self.rs_data.get(symbol, {})
            rs_val: float = float(stock_rs.get('rs_rating', 0.0))
            rvol_val: float = float(stock_rs.get('rvol', 0.0))
            foreign_acc: float = float(self.money_flow.get_accumulation(symbol, days=10))
            status: str = self._decide_tactical_status(rs_val, rvol_val, foreign_acc, market_condition)
            results.append(TacticalSignal(symbol, rs_val, rvol_val, round(foreign_acc, 2), market_condition, breadth_str, status))
        return pd.DataFrame([vars(s) for s in results])

    def _decide_tactical_status(self, rs_score, rvol, foreign_acc, market_cond) -> str:
        if market_cond == "CAUTION":
            if rs_score > 90.0 and rvol > 1.5 and foreign_acc > 0.0: return "HIGH CONVICTION BUY"
            return "WATCHLIST / WAIT"
        if market_cond == "EXCELLENT":
            if rs_score > 80.0 and (rvol > 1.2 or foreign_acc > 0.0): return "BUY / ACCUMULATE"
            return "HOLD"
        if rs_score > 85.0 and rvol > 1.2: return "BUY"
        return "NEUTRAL"

if __name__ == "__main__":
    commander = StrategyCommander(show_log=True)
    report = commander.analyze_battle_map(['HPG', 'SSI', 'VNM'])
    print(report.to_string(index=False))

