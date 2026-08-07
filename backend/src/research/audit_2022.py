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


def audit_2022_q4():
    conn = sqlite3.connect("data/screener_cache.db")
    df = pd.read_sql(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' "
        "AND date BETWEEN '2022-10-01' AND '2022-12-31' ORDER BY date",
        conn,
    )

    if df.empty:
        print("❌ NO DATA FOUND for 2022-Q4.")
        return

    df["date"] = pd.to_datetime(df["date"], format="mixed")
    # Check for gaps (weekdays only)
    all_dates = pd.date_range(start="2022-10-01", end="2022-12-31", freq="B")  # Business days
    missing = all_dates[~all_dates.isin(df["date"])]

    # Filter out potential holidays (Tết Dương Lịch 01/01 etc)
    # Vietnam holidays in Q4 2022: mostly weekends or Dec 31
    print(f"Total days: {len(df)}")
    if not missing.empty:
        print(f"Potential gaps ({len(missing)}):")
        print(missing)
    else:
        print("✅ Data continuity: 100% Business Day Coverage.")


if __name__ == "__main__":
    audit_2022_q4()
