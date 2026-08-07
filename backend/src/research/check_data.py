import sqlite3
import sys
from pathlib import Path


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


def check_coverage():
    conn = sqlite3.connect("data/screener_cache.db")
    cursor = conn.cursor()

    # 2020 COVID
    cursor.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN '2020-01-01' AND '2020-06-30'")
    r2020 = cursor.fetchone()[0]

    # 2022 Bond
    cursor.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN '2022-10-01' AND '2022-12-31'")
    r2022 = cursor.fetchone()[0]

    # Sideway 2023
    cursor.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN '2023-01-01' AND '2023-12-31'")
    r2023 = cursor.fetchone()[0]

    print(f"VNINDEX 2020 (6M): {r2020} points")
    print(f"VNINDEX 2022 (3M): {r2022} points")
    print(f"VNINDEX 2023 (1Y): {r2023} points")

    if r2020 > 0 and r2022 > 0:
        print("--- READY FOR STRESS TEST ---")
    else:
        print("--- DATA MISSING ---")


if __name__ == "__main__":
    check_coverage()
