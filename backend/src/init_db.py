"""init_db.py — Unified Schema Bootstrap + Cold-Start Guard

Zero-Click First Run Architecture:
- Schema creation is LOCAL (no network)
- Data hydration is NETWORK (explicit --seed)

Usage:
    python ptck.py db init    # via CLI
    sentinel.exe --init        # via .exe
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


# ── SCHEMA DEFINITIONS ───────────────────────────────────────────
# Single source of truth for all database schemas.
# Mirrors backend/src/database/db_core.py and telemetry schema.

SCHEMA_SCREENER = """
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    adj_close REAL, volume INTEGER CHECK(volume >= 0),
    source TEXT, PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_ohlcv(symbol, date);
CREATE TABLE IF NOT EXISTS symbol_industry (
    symbol TEXT PRIMARY KEY, icb_name2 TEXT, icb_name3 TEXT, icb_name4 TEXT
);
CREATE TABLE IF NOT EXISTS market_foreign_history (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    foreign_vol INTEGER, net_vol INTEGER, net_value REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS macro_history (
    variable TEXT NOT NULL, date TEXT NOT NULL, value REAL,
    PRIMARY KEY (variable, date)
);
CREATE TABLE IF NOT EXISTS ipo_calendar (
    symbol TEXT PRIMARY KEY,
    listing_date TEXT NOT NULL,
    listing_price REAL NOT NULL DEFAULT 0,
    listing_volume INTEGER NOT NULL DEFAULT 0,
    market_cap_listing REAL NOT NULL DEFAULT 0,
    sector TEXT NOT NULL DEFAULT 'UNKNOWN',
    exchange TEXT NOT NULL DEFAULT 'HOSE',
    aftermarket_return_pct REAL DEFAULT 0,
    days_listed INTEGER DEFAULT 0,
    source TEXT DEFAULT 'STATIC_SEED',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS regime_history (
    date TEXT PRIMARY KEY, regime_score REAL, status TEXT,
    breadth_pct REAL, breadth_velocity REAL, trend_score REAL,
    vol_score REAL, atr_ratio REAL, active_model TEXT, recovery_flag INTEGER
);
CREATE TABLE IF NOT EXISTS capital_displacement_history (
    date TEXT PRIMARY KEY, classification TEXT, conviction TEXT,
    signals TEXT, top1_symbol TEXT, top1_concentration REAL,
    wl_top1_symbol TEXT, wl_top1_concentration REAL,
    top3_concentration REAL, top10_concentration REAL DEFAULT 0,
    bank_share REAL DEFAULT 0, sector_breadth REAL DEFAULT 0,
    defensive_avg_chg REAL DEFAULT 0, vnindex_close REAL DEFAULT 0,
    vnindex_chg REAL DEFAULT 0, vnindex_vol_ratio REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flow_forecast_history (
    date TEXT PRIMARY KEY, projected_regime TEXT, regime_confidence TEXT,
    regime_prob_trending REAL, regime_prob_ranging REAL,
    regime_prob_crisis REAL, flow_velocity REAL, rotation_velocity REAL,
    flow_dispersion REAL, projection_summary TEXT, sector_forecasts TEXT,
    leading_sectors TEXT, lagging_sectors TEXT
);
CREATE TABLE IF NOT EXISTS rsi_regime_history (
    date TEXT PRIMARY KEY, total_scanned INTEGER, bull_count INTEGER,
    bear_count INTEGER, aligned_count INTEGER, misaligned_count INTEGER,
    habitat_distribution TEXT, report_json TEXT
);
"""

SCHEMA_TELEMETRY = """
CREATE TABLE IF NOT EXISTS decision_snapshots (
    decision_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
    posture TEXT NOT NULL, risk_level TEXT NOT NULL,
    confidence REAL NOT NULL, dominant_signal TEXT NOT NULL,
    vnindex_level REAL NOT NULL, opportunity_symbols TEXT NOT NULL DEFAULT '[]',
    holdings_health TEXT, market_regime TEXT,
    decision_weights TEXT, engine_scores TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outcome_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    evaluated_date TEXT NOT NULL, vnindex_entry REAL NOT NULL,
    vnindex_exit REAL NOT NULL, vnindex_return REAL NOT NULL,
    benchmark_return REAL NOT NULL, success INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
);
CREATE TABLE IF NOT EXISTS attribution_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, engine TEXT NOT NULL,
    contribution REAL NOT NULL, direction TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS engine_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    engine TEXT NOT NULL, signal_at_decision REAL NOT NULL,
    contribution REAL NOT NULL,
    true_positive_contribution REAL NOT NULL DEFAULT 0.0,
    false_positive_contribution REAL NOT NULL DEFAULT 0.0,
    direction TEXT NOT NULL, correlation REAL NOT NULL,
    accuracy REAL NOT NULL, precision REAL NOT NULL DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days, engine)
);
CREATE TABLE IF NOT EXISTS engine_performance (
    engine TEXT NOT NULL, window_days INTEGER NOT NULL,
    accuracy REAL NOT NULL, precision REAL NOT NULL DEFAULT 0.5,
    avg_contribution REAL NOT NULL, stability REAL NOT NULL,
    decisions_count INTEGER NOT NULL, last_updated TEXT NOT NULL,
    PRIMARY KEY (engine, window_days)
);
CREATE TABLE IF NOT EXISTS market_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    asset_return REAL NOT NULL, benchmark_return REAL NOT NULL,
    alpha_return REAL NOT NULL, realized_volatility REAL NOT NULL DEFAULT 0.0,
    regime_shift INTEGER NOT NULL DEFAULT 0,
    sector_rotation TEXT NOT NULL DEFAULT 'NEUTRAL',
    liquidity_phase TEXT NOT NULL DEFAULT 'NEUTRAL',
    gold_change_pct REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
);
CREATE TABLE IF NOT EXISTS driver_reputation (
    driver TEXT NOT NULL, regime_tag TEXT NOT NULL DEFAULT 'all',
    window_days INTEGER NOT NULL DEFAULT 90,
    accuracy REAL NOT NULL DEFAULT 0, alpha_pct REAL NOT NULL DEFAULT 0,
    sharpe REAL NOT NULL DEFAULT 0, stability REAL NOT NULL DEFAULT 0,
    coverage REAL NOT NULL DEFAULT 0, flip_rate REAL NOT NULL DEFAULT 0,
    n_samples INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    accuracy_30d REAL, accuracy_90d REAL, accuracy_180d REAL,
    performance_drift REAL, relative_drift REAL, half_life_days REAL,
    PRIMARY KEY (driver, regime_tag, window_days)
);
"""

SCHEMA_PORTFOLIO = """
CREATE TABLE IF NOT EXISTS position_lifecycle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, status TEXT NOT NULL,
    regime_at_entry TEXT NOT NULL, entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL CHECK(entry_price > 0),
    current_size INTEGER NOT NULL DEFAULT 0,
    avg_cost REAL NOT NULL DEFAULT 0, highest_price_held REAL DEFAULT 0,
    stop_loss_price REAL NOT NULL, take_profit_price REAL,
    thesis_source TEXT NOT NULL, thesis_notes TEXT,
    initial_risk_pct REAL NOT NULL DEFAULT 0.0,
    realized_pnl_pct REAL, conviction_score REAL NOT NULL DEFAULT 0.0,
    exit_reason TEXT,
    context_source TEXT NOT NULL DEFAULT 'SECONDARY_STABLE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portfolio_telemetry (
    snapshot_date TEXT PRIMARY KEY, total_nav REAL NOT NULL,
    cash_balance REAL NOT NULL, allocated_margin REAL DEFAULT 0,
    net_exposure_pct REAL NOT NULL, portfolio_heat_pct REAL NOT NULL,
    rolling_hit_rate_10d REAL DEFAULT 1.0,
    current_dd_pct REAL DEFAULT 0.0,
    risk_dampener_factor REAL DEFAULT 1.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portfolio_risk_path (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_exposure REAL NOT NULL DEFAULT 0.0,
    portfolio_heat REAL NOT NULL DEFAULT 0.0,
    throttle_factor REAL NOT NULL DEFAULT 1.0,
    dampener_avg REAL NOT NULL DEFAULT 1.0,
    drawdown_pct REAL NOT NULL DEFAULT 0.0,
    rolling_vol REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS model_regime_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL, regime TEXT NOT NULL,
    trades INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0, total_r REAL NOT NULL DEFAULT 0.0,
    avg_r REAL NOT NULL DEFAULT 0.0, win_rate REAL NOT NULL DEFAULT 0.0,
    expectancy REAL NOT NULL DEFAULT 0.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model, regime)
);
CREATE TABLE IF NOT EXISTS portfolio_state_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
    model_source TEXT NOT NULL, regime TEXT NOT NULL,
    entry_time TEXT NOT NULL, exit_time TEXT,
    entry_score REAL NOT NULL DEFAULT 0.0, exit_score REAL,
    pnl_pct REAL, r_multiple REAL, holding_period_days INTEGER,
    max_favorable_excursion REAL, max_adverse_excursion REAL,
    exit_reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conviction_decay_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL, regime TEXT NOT NULL,
    streak_losses INTEGER NOT NULL DEFAULT 0,
    streak_wins INTEGER NOT NULL DEFAULT 0,
    decay_factor REAL NOT NULL DEFAULT 1.0,
    boost_factor REAL NOT NULL DEFAULT 1.0,
    reason TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_MACRO = """
CREATE TABLE IF NOT EXISTS macro_history_v2 (
    variable TEXT NOT NULL, date TEXT NOT NULL, value REAL,
    asset_class TEXT NOT NULL, unit TEXT NOT NULL, source TEXT NOT NULL,
    raw_value REAL, raw_unit TEXT, confidence REAL DEFAULT 1.0,
    valid_from TEXT, valid_to TEXT, freshness_score REAL DEFAULT 1.0,
    latency_ms INTEGER DEFAULT 0,
    ingested_at TEXT DEFAULT (datetime('now')),
    metadata TEXT, PRIMARY KEY (variable, date, source)
);
"""

SCHEMA_QUANT = """
CREATE TABLE IF NOT EXISTS quant_rs_scores (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    rs_raw REAL, rs_rating INTEGER,
    rvol REAL, avg_vol_20d REAL, avg_value_20d REAL,
    price REAL, change_1y REAL,
    percentile REAL, momentum_3m REAL, momentum_6m REAL,
    momentum_1y REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS factor_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL, symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL, exit_date TEXT,
    entry_price REAL NOT NULL, exit_price REAL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT')),
    return_pct REAL, r_multiple REAL,
    holding_period_days INTEGER,
    regime_at_entry TEXT, regime_at_exit TEXT,
    rs_at_entry REAL, rs_at_exit REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_quant_rs_date ON quant_rs_scores(date);
CREATE INDEX IF NOT EXISTS idx_quant_rs_symbol ON quant_rs_scores(symbol);
CREATE INDEX IF NOT EXISTS idx_factor_backtest_strategy ON factor_backtests(strategy);
"""

SCHEMA_SHADOW = """
CREATE TABLE IF NOT EXISTS shadow_decision_logs (
    decision_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
    posture TEXT NOT NULL, risk_level TEXT NOT NULL,
    confidence REAL NOT NULL, engine_scores TEXT NOT NULL,
    decision_weights TEXT NOT NULL DEFAULT '{}',
    market_regime TEXT, regime_score REAL DEFAULT 0.0,
    vnindex_level REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shadow_ablations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, engine_removed TEXT NOT NULL,
    baseline_action TEXT NOT NULL, baseline_confidence REAL NOT NULL,
    ablated_action TEXT NOT NULL, ablated_confidence REAL NOT NULL,
    action_changed INTEGER NOT NULL DEFAULT 0,
    confidence_delta REAL NOT NULL DEFAULT 0.0,
    decision_flip INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, engine_removed)
);
CREATE TABLE IF NOT EXISTS shadow_outcome_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    actual_outcome REAL NOT NULL, actual_success INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days)
);
CREATE TABLE IF NOT EXISTS shadow_attribution_perturbations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    engine TEXT NOT NULL, signal_at_decision REAL NOT NULL DEFAULT 0.0,
    baseline_contribution REAL NOT NULL DEFAULT 0.0,
    ablated_contribution REAL NOT NULL DEFAULT 0.0,
    contribution_delta REAL NOT NULL DEFAULT 0.0,
    baseline_tp REAL NOT NULL DEFAULT 0.0, ablated_tp REAL NOT NULL DEFAULT 0.0,
    baseline_fp REAL NOT NULL DEFAULT 0.0, ablated_fp REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id, horizon_days, engine)
);
CREATE TABLE IF NOT EXISTS shadow_engine_profiles (
    engine TEXT PRIMARY KEY,
    total_decisions INTEGER NOT NULL DEFAULT 0,
    flip_count INTEGER NOT NULL DEFAULT 0,
    avg_confidence_delta REAL NOT NULL DEFAULT 0.0,
    action_change_ratio REAL NOT NULL DEFAULT 0.0,
    stability_score REAL NOT NULL DEFAULT 0.0,
    last_updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_belief_state (
    key TEXT PRIMARY KEY, value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ── DATABASE REGISTRY ────────────────────────────────────────────
# Each entry: (filename, schema_sql, description)

DATABASES: list[tuple[str, str, str]] = [
    ("quant.db", SCHEMA_QUANT, "Quant RS scores + factor backtests"),
    ("telemetry.db", SCHEMA_TELEMETRY, "Decision telemetry + reputation"),
    ("portfolio_state.db", SCHEMA_PORTFOLIO, "Portfolio positions + risk"),
    ("screener_cache.db", SCHEMA_SCREENER, "Market data cache"),
    ("sentinel_macro.db", SCHEMA_MACRO, "Macro history v2"),
    ("shadow_cao.db", SCHEMA_SHADOW, "Shadow CAO testing"),
]


def init_one(db_path: str, schema: str) -> bool:
    """Initialize a single database: create tables if not exist."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(schema)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to init {path.name}: {e}")
        return False


def check_schema(db_path: str) -> bool:
    """Check if database has any tables."""
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        return len(tables) > 0
    except Exception:
        return False


def init_all(data_dir: str | Path) -> dict[str, bool]:
    """Initialize ALL database schemas in the given data directory.

    Returns dict of db_name → success status.
    """
    data_dir = Path(data_dir)
    results = {}
    for filename, schema, _desc in DATABASES:
        db_path = str(data_dir / filename)
        ok = init_one(db_path, schema)
        results[filename] = ok
    return results


def needs_init(data_dir: str | Path) -> list[str]:
    """Return list of databases that need initialization."""
    data_dir = Path(data_dir)
    missing = []
    for filename, _schema, _desc in DATABASES:
        if not check_schema(str(data_dir / filename)):
            missing.append(filename)
    return missing


def run_init(data_dir: str | Path) -> int:
    """Run full init, return count of success."""
    print(f"  Initializing database schemas in: {data_dir}")
    results = init_all(data_dir)
    ok = sum(1 for v in results.values() if v)
    total = len(results)
    for name, success in results.items():
        icon = "[OK]" if success else "[FAIL]"
        print(f"  {icon:6s} {name}")
    print(f"  Kết quả: {ok}/{total} schemas initialized")
    return ok
