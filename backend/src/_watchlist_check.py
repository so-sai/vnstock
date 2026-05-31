"""Quick watchlist 1-year performance check"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, 'backend/src')
sys.stdout.reconfigure(encoding='utf-8')

from src.database.db_core import get_connection
import pandas as pd
from datetime import datetime, timedelta

symbols = ['HPG','MBB','GMD','STB','VTO','VNM','DP3','VTP','MWG','SSI','BSR','QNS','TLG','SBT','FPT','DGC','VGI','VIB','TCB','ACB']
one_year_ago = (datetime.now() - timedelta(days=365)).date()
print(f"=== WATCHLIST 1-YEAR PERFORMANCE ({one_year_ago} to {datetime.now().date()}) ===")
print()

with get_connection() as conn:
    for sym in symbols:
        df = pd.read_sql(
            "SELECT date, adj_close as close, volume FROM daily_ohlcv WHERE symbol=? AND date>=? ORDER BY date",
            conn, params=(sym, one_year_ago.isoformat())
        )
        if df.empty:
            print(f"  {sym:>4s}:  NO DATA")
            continue
        first_close = float(df.iloc[0]["close"])
        last_close = float(df.iloc[-1]["close"])
        pct = round((last_close - first_close) / first_close * 100, 2)
        vol_avg = int(df["volume"].mean())
        max_c = float(df["close"].max())
        min_c = float(df["close"].min())
        n_days = len(df)
        print(f"  {sym:>4s}:  {pct:>+7.2f}%  {first_close:>8.0f} -> {last_close:>8.0f}  cao={max_c:>8.0f}  thap={min_c:>8.0f}  klg_bq={vol_avg:>10,.0f}  sessions={n_days}")

print()
ranked = []
with get_connection() as conn:
    for sym in symbols:
        df = pd.read_sql(
            "SELECT adj_close as close FROM daily_ohlcv WHERE symbol=? AND date>=? ORDER BY date",
            conn, params=(sym, one_year_ago.isoformat())
        )
        if not df.empty:
            pct = round((float(df.iloc[-1]["close"]) - float(df.iloc[0]["close"])) / float(df.iloc[0]["close"]) * 100, 2)
            ranked.append((pct, sym))
ranked.sort(reverse=True)
print("=== RANKING BY PERFORMANCE ===")
for i, (pct, sym) in enumerate(ranked, 1):
    print(f"  {i:>2d}. {sym:>4s}  {pct:>+7.2f}%")
