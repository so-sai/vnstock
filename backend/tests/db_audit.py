"""DB schema audit for AEL design — run from project root"""
import sys, pathlib
p = pathlib.Path(__file__).resolve()
for parent in p.parents:
    if (parent / "AGENTS.md").exists() and (parent / "backend").is_dir():
        sys.path.insert(0, str(parent / "backend"))
        break

from src.database.db_core import get_connection
import pandas as pd

with get_connection() as conn:
    tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn)
    print("=== TABLES ===")
    print(tables['name'].tolist())

    print("\n=== regime_history columns ===")
    rh = pd.read_sql("PRAGMA table_info(regime_history)", conn)
    print(rh[['name','type']].to_string(index=False))

    print("\n=== regime_history sample ===")
    rh_data = pd.read_sql("SELECT * FROM regime_history ORDER BY date DESC LIMIT 5", conn)
    print(rh_data.to_string(index=False))

    print("\n=== VNINDEX date range ===")
    rng = pd.read_sql(
        "SELECT MIN(date) as first, MAX(date) as last, COUNT(*) as rows "
        "FROM daily_ohlcv WHERE symbol='VNINDEX'", conn)
    print(rng.to_string(index=False))

    print("\n=== Total symbols in daily_ohlcv ===")
    syms = pd.read_sql("SELECT COUNT(DISTINCT symbol) as n FROM daily_ohlcv", conn)
    print(syms.to_string(index=False))
