import sys
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    current = Path(__file__).resolve().parent.parent.parent
    if str(current) not in sys.path:
        sys.path.insert(0, str(current))
    return current


PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection, save_data_upsert
from src.providers.vnstock_provider import VnstockProvider


def hydrate_2020_data():
    """Fetches VNINDEX 2019-2020 for the COVID Stress Test."""
    print("📡 Hydrating VNINDEX 2019-2020 history...")
    try:
        q = VnstockProvider(source="kbs")
        df = q.history("VNINDEX", start="2019-01-01", end="2020-06-30")
        if df is not None and not df.empty:
            df = df.rename(columns={"time": "date"})
            df["symbol"] = "VNINDEX"
            df["source"] = "kbs"
            if "adj_close" not in df.columns:
                df["adj_close"] = df["close"]

            # Format date for SQLite
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")

            cols = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
            with get_connection() as conn:
                save_data_upsert("daily_ohlcv", df[cols], conn)
            print(f"✅ Hydrated {len(df)} points for VNINDEX.")
        else:
            print("⚠️ No data found.")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    hydrate_2020_data()
