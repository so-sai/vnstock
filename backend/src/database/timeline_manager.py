
import sys
import pandas as pd
import sqlite3
from pathlib import Path

# Sentinel v2.1 (Anchor Fix)
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
    return root_path

PROJECT_ROOT = _hydrate_path()
from src.database.db_core import get_connection, save_data_upsert

def log_regime_state(verdict):
    """
    Persists the daily regime decision to the regime_history table.
    """
    date = verdict.get('date')
    if not date:
        return

    data = {
        "date": [date],
        "regime_score": [verdict.get('regime_score')],
        "status": [verdict.get('market_status')],
        "breadth_pct": [verdict['details'].get('breadth_pct')],
        "breadth_velocity": [verdict.get('breadth_velocity', 0.0)],
        "trend_score": [verdict['details'].get('t_score')],
        "vol_score": [verdict['details'].get('v_score')],
        "atr_ratio": [verdict['details'].get('atr_ratio')],
        "active_model": [verdict.get('active_model', 'NONE')],
        "recovery_flag": [1 if verdict.get('recovery', {}).get('is_recovery') else 0]
    }
    
    df = pd.DataFrame(data)
    with get_connection() as conn:
        save_data_upsert("regime_history", df, conn)
    print(f"📈 [TIMELINE] Logged regime state for {date}.")

def get_regime_history(limit=30):
    """
    Retrieves the recent regime history for visibility analysis.
    """
    with get_connection() as conn:
        df = pd.read_sql(f"SELECT * FROM regime_history ORDER BY date DESC LIMIT {limit}", conn)
    return df.sort_values('date')


def get_active_ipos(days_back: int = 90):
    """Placeholder IPO loader. Chờ nguồn dữ liệu IPO thực tế."""
    return []


def get_aftermarket_returns(days_back: int = 90):
    """Placeholder aftermarket returns loader. Trả về dict rỗng khi chưa có dữ liệu."""
    return {}


def calculate_breadth_velocity(current_breadth, days=5, target_date=None):
    """
    Calculates the velocity of breadth recovery from historical logs.
    Supports Point-in-time accuracy via target_date.
    """
    try:
        with get_connection() as conn:
            if target_date:
                df = pd.read_sql(f"SELECT breadth_pct, date FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT {days}", conn)
            else:
                df = pd.read_sql(f"SELECT breadth_pct, date FROM regime_history ORDER BY date DESC LIMIT {days}", conn)
        
        if df.empty:
            return 0.0
        
        past_entry = df.iloc[-1]
        past_breadth = past_entry['breadth_pct']
        velocity = current_breadth - past_breadth
        
        if target_date and velocity != 0:
            print(f"   [VELOCITY] Today: {current_breadth}% | Past: {past_breadth}% (from {past_entry['date']}) | Result: {velocity:+.1f}%")
            
        return velocity
    except Exception:
        # Table might not exist yet on very first run
        return 0.0
