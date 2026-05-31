"""
Exposure Engine v1.1
Adaptive feedback loop: conviction dampener + regime memory bias + global risk throttle.
"""
import sys, sqlite3, logging
from pathlib import Path

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
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.database.portfolio_db import PORTFOLIO_DB_PATH
from src.portfolio import memory_engine
import src.config

logger = logging.getLogger(__name__)

MAX_HEAT = 10.0

def calculate_model_dampener(thesis_source: str) -> float:
    conn = sqlite3.connect(PORTFOLIO_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT realized_pnl_pct FROM position_lifecycle
        WHERE thesis_source = ? AND status = 'CLOSED'
        ORDER BY closed_at DESC LIMIT 5
    """, (thesis_source,))
    trades = c.fetchall()
    conn.close()

    if len(trades) < 3:
        return 1.0

    loss_count = sum(1 for t in trades if t[0] and t[0] < 0)

    if loss_count >= 4:
        return 0.25
    if loss_count == 3:
        return 0.50
    return 1.0

def calculate_total_dampener(thesis_source: str, current_regime: str = "RANGING") -> dict:
    combined = memory_engine.get_effective_dampener(thesis_source, current_regime)
    combined["legacy_dampener"] = calculate_model_dampener(thesis_source)
    combined["net_dampener"] = round(
        combined["legacy_dampener"] * combined["net_dampener"], 4
    )
    return combined

def evaluate_global_risk_throttle(current_heat: float) -> dict:
    if current_heat >= MAX_HEAT:
        return {
            "throttle_activated": True,
            "risk_state": "EMERGENCY_LOCK",
            "multiplier": 0.0,
        }
    if current_heat >= MAX_HEAT * 0.7:
        return {
            "throttle_activated": False,
            "risk_state": "CAUTION",
            "multiplier": 0.5,
        }
    return {
        "throttle_activated": False,
        "risk_state": "NORMAL",
        "multiplier": 1.0,
    }

def get_portfolio_heat(nav: float = None) -> float:
    conn = sqlite3.connect(PORTFOLIO_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COALESCE(SUM((entry_price - stop_loss_price) * current_size), 0)
        FROM position_lifecycle
        WHERE status IN ('ENTERED','SCALE_IN','REDUCED')
    """)
    total_risk = c.fetchone()[0]

    if nav is None:
        c.execute("SELECT total_nav FROM portfolio_telemetry ORDER BY snapshot_date DESC LIMIT 1")
        nav_row = c.fetchone()
        conn.close()
        if not nav_row or not nav_row[0]:
            return 0.0
        nav = nav_row[0]
    else:
        conn.close()

    return (total_risk / nav) * 100 if nav > 0 else 0.0
