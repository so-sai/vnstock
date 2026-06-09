import sqlite3, os, json
from pathlib import Path

# 1. Check vnstock.db
conn = sqlite3.connect('backend/data/vnstock.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print('vnstock.db tables:', tables)

# Check for stock/OHLCV data
for t in tables:
    c.execute(f"SELECT COUNT(*) FROM \"{t}\"")
    cnt = c.fetchone()[0]
    print(f'  {t}: {cnt} rows')
    if cnt > 0:
        try:
            c.execute(f"SELECT * FROM \"{t}\" LIMIT 2")
            cols = [desc[0] for desc in c.description]
            print(f'  {t} columns: {cols}')
            for r in c.fetchall():
                print(f'    {r}')
        except Exception as e:
            print(f'  {t} error: {e}')

# 2. Check telemetry.db outcome records full detail
c2 = conn.cursor()
conn2 = sqlite3.connect('backend/data/telemetry.db')
c2 = conn2.cursor()

# Check pending outcomes
c2.execute("""
    SELECT s.decision_id, s.timestamp, s.posture, s.market_regime, o.id as outcome_id
    FROM decision_snapshots s
    LEFT JOIN outcome_records o ON s.decision_id = o.decision_id
    ORDER BY s.timestamp
""")
for r in c2.fetchall():
    print(f'Snapshot: {r[0][:8]} | {r[1][:10]} | {r[2]:15s} | regime={r[3]:10s} | outcome={r[4]}')

# 3. Check decision_board.json
board_path = Path('backend/data/output/decision_board.json')
if board_path.exists():
    board = json.loads(board_path.read_text(encoding='utf-8'))
    print(f'\nBoard decision: timestamp={board.get("timestamp")}, status={board.get("market_status")}, consensus={board.get("consensus")}, conf={board.get("confidence")}')
else:
    print('No decision_board.json found')

conn.close()
conn2.close()
