import sqlite3
import sys
from pathlib import Path

import pandas as pd


# Sentinel v2.1 (Anchor Fix)
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
    return root_path


PROJECT_ROOT = _hydrate_path()


def check_replay_integrity():
    conn = sqlite3.connect("data/screener_cache.db")
    df = pd.read_sql(
        "SELECT date, breadth_pct FROM regime_history WHERE date BETWEEN '2022-10-01' AND '2022-12-31' ORDER BY date",
        conn,
    )
    print("--- REGIME HISTORY AUDIT ---")
    print(df)

    # Check 21/11 Velocity Calc Logic
    target_date = "2022-11-21"
    df_prev = pd.read_sql(
        f"SELECT breadth_pct, date FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 5",
        conn,
    )
    print(f"\nPast 5 days for {target_date}:")
    print(df_prev)


if __name__ == "__main__":
    check_replay_integrity()
