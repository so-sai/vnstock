"""
Portfolio Memory Layer v1.0
Khởi tạo và quản lý portfolio_state.db — Sổ cái kế toán danh mục độc lập.
"""
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path


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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config

PORTFOLIO_DB_PATH = str(src.config.DATA_DIR / "portfolio_state.db")

# New columns added in Phase 9.2
SCHEMA_COLUMNS_V92 = [
    ("initial_risk_pct", "REAL NOT NULL DEFAULT 0.0"),
    ("realized_pnl_pct", "REAL"),
    ("conviction_score", "REAL NOT NULL DEFAULT 0.0"),
    ("exit_reason", "TEXT"),
    ("context_source", "TEXT NOT NULL DEFAULT 'SECONDARY_STABLE'"),
]

# Schema — Bảng vết vị thế
SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS position_lifecycle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('WATCHLIST','ARMED','ENTERED','SCALE_IN','REDUCED','EXIT_PENDING','CLOSED')),
    regime_at_entry TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL CHECK(entry_price > 0),
    current_size INTEGER NOT NULL DEFAULT 0,
    avg_cost REAL NOT NULL DEFAULT 0,
    highest_price_held REAL DEFAULT 0,
    stop_loss_price REAL NOT NULL,
    take_profit_price REAL,
    thesis_source TEXT NOT NULL,
    thesis_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    initial_risk_pct REAL NOT NULL DEFAULT 0.0,
    realized_pnl_pct REAL,
    conviction_score REAL NOT NULL DEFAULT 0.0,
    exit_reason TEXT,
    context_source TEXT NOT NULL DEFAULT 'SECONDARY_STABLE'
);
"""

SCHEMA_COLUMNS_V93 = [
    ("rolling_hit_rate_10d", "REAL DEFAULT 1.0"),
    ("current_dd_pct", "REAL DEFAULT 0.0"),
    ("risk_dampener_factor", "REAL DEFAULT 1.0"),
]

SCHEMA_TELEMETRY = """
CREATE TABLE IF NOT EXISTS portfolio_telemetry (
    snapshot_date TEXT PRIMARY KEY,
    total_nav REAL NOT NULL,
    cash_balance REAL NOT NULL,
    allocated_margin REAL DEFAULT 0,
    net_exposure_pct REAL NOT NULL,
    portfolio_heat_pct REAL NOT NULL,
    rolling_hit_rate_10d REAL DEFAULT 1.0,
    current_dd_pct REAL DEFAULT 0.0,
    risk_dampener_factor REAL DEFAULT 1.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

INDEX_ACTIVE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_positions
ON position_lifecycle (symbol)
WHERE status NOT IN ('CLOSED', 'WATCHLIST');
"""

@contextmanager
def get_portfolio_connection():
    conn = sqlite3.connect(PORTFOLIO_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def initialize_portfolio_database():
    os.makedirs(str(src.config.DATA_DIR), exist_ok=True)
    with get_portfolio_connection() as conn:
        conn.execute(SCHEMA_POSITIONS)
        conn.execute(SCHEMA_TELEMETRY)
        conn.execute(INDEX_ACTIVE)

def migrate_portfolio_database():
    """Add Phase 9.2 columns to position_lifecycle and Phase 9.3 to portfolio_telemetry."""
    with get_portfolio_connection() as conn:
        existing = {d[1] for d in conn.execute("PRAGMA table_info(position_lifecycle)").fetchall()}
        for col_name, col_type in SCHEMA_COLUMNS_V92:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE position_lifecycle ADD COLUMN {col_name} {col_type}")
        existing_tele = {d[1] for d in conn.execute("PRAGMA table_info(portfolio_telemetry)").fetchall()}
        for col_name, col_type in SCHEMA_COLUMNS_V93:
            if col_name not in existing_tele:
                conn.execute(f"ALTER TABLE portfolio_telemetry ADD COLUMN {col_name} {col_type}")

if __name__ == "__main__":
    initialize_portfolio_database()
    migrate_portfolio_database()
