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


def analyze_recovery_cycles():
    conn = sqlite3.connect("data/screener_cache.db")

    # 1. Load liquid symbols history (approximate liquid as symbols with enough data)
    print("Loading data...")
    df = pd.read_sql(
        """
        SELECT symbol, date, close
        FROM daily_ohlcv
        WHERE date BETWEEN '2022-01-01' AND '2024-12-31'
    """,
        conn,
    )

    df["date"] = pd.to_datetime(df["date"], format="mixed").dt.date
    df = df.sort_values(["symbol", "date"])

    # 2. Daily Breadth Calculation
    print("Calculating daily breadth...")
    g = df.groupby("symbol")
    df["ma20"] = g["close"].transform(lambda x: x.rolling(20).mean())
    df["above_ma20"] = df["close"] > df["ma20"]

    daily_breadth = df.groupby("date")["above_ma20"].mean() * 100
    daily_breadth = daily_breadth.to_frame(name="breadth")

    # 3. Analyze Crisis to Recovery
    # Filter for Crisis (<10%)
    crisis_periods = []
    in_crisis = False
    start_date = None

    for date, row in daily_breadth.iterrows():
        b = row["breadth"]
        if b < 10 and not in_crisis:
            in_crisis = True
            start_date = date
        elif b >= 35 and in_crisis:
            in_crisis = False
            recovery_days = (date - start_date).days
            crisis_periods.append({"start_crisis": start_date, "end_recovery": date, "duration": recovery_days})

    print("\n--- BREADTH RECOVERY CYCLES (2022-2024) ---")
    if not crisis_periods:
        print("No matches detected for <10% to >35% transitions.")
    else:
        for p in crisis_periods:
            print(f"Crisis: {p['start_crisis']} -> Recovery: {p['end_recovery']} ({p['duration']} days)")

    # Calculate Velocity Insight
    daily_breadth["velocity_5d"] = daily_breadth["breadth"].diff(5)
    print("\nTop 5 Max Breadth Velocity (5-day jump):")
    print(daily_breadth.sort_values("velocity_5d", ascending=False).head(5))


analyze_recovery_cycles()
