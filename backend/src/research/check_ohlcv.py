import sys
from pathlib import Path

import pandas as pd


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
df = pd.read_sql("SELECT COUNT(*) as count FROM daily_ohlcv WHERE date = '2022-11-21'", conn)
print(df)
