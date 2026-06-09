import sys, sqlite3, os, json
sys.path.insert(0, 'backend')
from pathlib import Path

from src.telemetry.recorder import record_decision

did = record_decision({
    'decision_id': 'test-001',
    'timestamp': '2026-06-08T09:35:00',
    'action': 'HOLD',
    'risk_state': 'CAUTION',
    'confidence': 45.0,
    'engine_scores': {'regime': 0.3, 'breadth_pct': 30, 'breadth_velocity': -5, 'recovery_active': 0},
})
print('Created:', did)

conn = sqlite3.connect('backend/data/telemetry.db')
c = conn.cursor()
c.execute('SELECT decision_id, posture, confidence, timestamp FROM decision_snapshots ORDER BY timestamp')
for r in c.fetchall():
    print(f'  {r[0][:8]} | {r[1]:15s} | conf={r[2]:5.0f} | {str(r[3])[:19]}')
conn.close()
