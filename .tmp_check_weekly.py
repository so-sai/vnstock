import sqlite3

conn = sqlite3.connect("backend/data/screener_cache.db")
conn2 = sqlite3.connect("backend/data/vnstock.db")

print("=== vnstock.db tables ===")
tables = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    cnt = conn2.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    maxd = conn2.execute(f"SELECT MAX(date) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {cnt} rows, latest={maxd}")

print()
print("=== THIS WEEK DATA (since Jun 8) in screener_cache.db ===")
for tbl in ["daily_ohlcv", "regime_history", "macro_history"]:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE date >= '2026-06-08'").fetchone()[0]
    maxd = conn.execute(f"SELECT MAX(date) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {cnt} rows since Jun 8, latest={maxd}")

conn.close()
conn2.close()
print()
print("Data check complete.")
