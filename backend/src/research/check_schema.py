import sys
import os
import pandas as pd
from pathlib import Path

# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, 'frozen', False):
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

import sqlite3
conn = sqlite3.connect('data/screener_cache.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(f'Tables: {cursor.fetchall()}')

for table in ['symbols', 'sector_history', 'market_breadth']:
    try:
        df = pd.read_sql(f"SELECT * FROM {table} LIMIT 1", conn)
        print(f'\nTable {table} columns: {df.columns.tolist()}')
    except:
        print(f'\nTable {table} not found or empty.')