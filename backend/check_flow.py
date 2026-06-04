import sys, warnings, io, json, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings('ignore')
sys.path.insert(0, 'backend/libs/vnstock')
sys.path.insert(0, 'backend/src')

from datetime import datetime
today = '2026-06-02'
db = 'backend/data/screener_cache.db'

try:
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print('Tables:', tables)
    print()

    # money_flow / capital_flow / foreign / displace tables
    for t in tables:
        tl = t.lower()
        if any(k in tl for k in ['flow', 'capital', 'foreign', 'displace', 'breadth', 'sector']):
            cur.execute(f'SELECT * FROM "{t}" ORDER BY rowid DESC LIMIT 10')
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            print(f'--- {t} --- ({len(rows)} rows)')
            print(' | '.join(cols))
            for r in rows:
                print(r)
            print()
except Exception as e:
    print(f'Error: {e}')
finally:
    if conn:
        conn.close()
