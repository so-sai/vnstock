import sqlite3
import pandas as pd
import os

db = os.path.join('data', 'screener_cache.db')
conn = sqlite3.connect(db)

print("--- Symbol Start/End Ranges ---")
res = pd.read_sql("SELECT symbol, MIN(date) as start, MAX(date) as end, COUNT(*) as cnt FROM daily_ohlcv WHERE symbol IN ('VNINDEX', 'VN30', 'FPT', 'HPG') GROUP BY symbol", conn)
print(res)

print("\n--- Rows per Year ---")
counts = pd.read_sql("SELECT substr(date, 1, 4) as year, count(*) as cnt FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30') GROUP BY year", conn)
print(counts)

conn.close()
