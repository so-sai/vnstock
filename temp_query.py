import sqlite3, json
conn = sqlite3.connect('E:\\DEV\\opensource_contrib\\PTCK_VNSTOCK\\backend\\data\\screener_cache.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
symbols = ['MBB','STB','VIB','TCB','ACB','HPG','BSR','GMD','VTO','DGC','MWG','QNS','SBT','TLG','DP3','REE','SSI','VTP','VGI','FPT','BFC','CMG','LSS','DHG','VJC','HDB','VCB','HSG','PC1','HCM','DPM','FOX','CTR','LPB','ACV','GEX','VND','NKG','IMP']
placeholders = ','.join(['?'] * len(symbols))
c.execute(f'SELECT symbol, date, close, volume FROM daily_ohlcv WHERE symbol IN ({placeholders}) ORDER BY symbol, date DESC', symbols)
results = c.fetchall()
# Get only latest per symbol
seen = set()
latest = []
for r in results:
    if r[0] not in seen:
        seen.add(r[0])
        latest.append({'symbol': r[0], 'date': str(r[1]), 'close': r[2], 'volume': r[3]})
print(json.dumps(latest, indent=2))
conn.close()
