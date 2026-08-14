"""Unit tests cho commodity_service.get_steel_crack_spread — PIT + fail-closed + early warning.

Covers the approved design (2026-08-14):
  - exact arithmetic of Spread = HRC - 1.6*Ore - 0.5*Coal
  - Point-In-Time temporal isolation (no look-ahead)
  - fail-closed on missing component / stale / EVICTed
  - staged early-warning (warning@50% evict, critical@evict-1)
  - live vs replay mode: system_health ledger only written in live mode
"""

from __future__ import annotations

import sqlite3

import pytest
from src.services.macro.commodity_service import (
    STALE_CONFIG,
    SteelCrackSpreadResult,
    get_steel_crack_spread,
)


@pytest.fixture
def mock_db() -> sqlite3.Connection:
    """In-memory SQLite with the macro_history + system_health schema (db_core shape)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE macro_history (
            variable TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL,
            is_stale INTEGER DEFAULT 0,
            PRIMARY KEY (variable, date)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE system_health (
            component TEXT NOT NULL,
            status TEXT NOT NULL,
            last_error TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (component)
        );
        """
    )
    yield conn
    conn.close()


def _seed(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO macro_history (variable, date, value, is_stale) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


# ── Test 1: Exact arithmetic ──
def test_crack_spread_exact_calculation(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-12", 94.0, 0),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    res = get_steel_crack_spread(mock_db, as_of_date="2026-08-14")
    assert isinstance(res, SteelCrackSpreadResult)
    assert pytest.approx(res.crack_spread, 0.01) == 284.6
    assert res.hrc_price == 546.0
    assert res.iron_ore_price == 94.0
    assert res.coking_coal_price == 222.0


# ── Test 2: Temporal isolation (no look-ahead) ──
def test_crack_spread_temporal_isolation_pit(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-01", 500.0, 0),
            ("IRON_ORE_62", "2026-08-01", 100.0, 0),
            ("COKING_COAL_HCC", "2026-08-01", 200.0, 0),
            ("HRC_CFR", "2026-08-15", 700.0, 0),
            ("IRON_ORE_62", "2026-08-15", 80.0, 0),
            ("COKING_COAL_HCC", "2026-08-15", 150.0, 0),
        ],
    )
    res_t0 = get_steel_crack_spread(mock_db, as_of_date="2026-08-05")
    assert isinstance(res_t0, SteelCrackSpreadResult)
    assert pytest.approx(res_t0.crack_spread, 0.01) == 240.0
    assert res_t0.hrc_date == "2026-08-01"

    res_t1 = get_steel_crack_spread(mock_db, as_of_date="2026-08-20")
    assert isinstance(res_t1, SteelCrackSpreadResult)
    assert pytest.approx(res_t1.crack_spread, 0.01) == 497.0
    assert res_t1.hrc_date == "2026-08-15"


# ── Test 3: Missing component => None ──
def test_crack_spread_missing_component_returns_none(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-12", 94.0, 0),
        ],
    )
    assert get_steel_crack_spread(mock_db, as_of_date="2026-08-14") is None


# ── Test 4: is_stale rejection + allow_stale bypass ──
def test_crack_spread_stale_data_rejection(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-12", 94.0, 1),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    assert get_steel_crack_spread(mock_db, as_of_date="2026-08-14") is None
    res = get_steel_crack_spread(mock_db, as_of_date="2026-08-14", allow_stale=True)
    assert isinstance(res, SteelCrackSpreadResult)
    assert pytest.approx(res.crack_spread, 0.01) == 284.6


# ── Test 5: EVICTed component (delta >= evict) => None ──
def test_crack_spread_evict_rejection(mock_db: sqlite3.Connection) -> None:
    evict = STALE_CONFIG["IRON_ORE_62"]["evict"]  # 7
    stale_date = f"2026-08-{14 - evict - 1:02d}"
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", stale_date, 94.0, 0),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    assert get_steel_crack_spread(mock_db, as_of_date="2026-08-14") is None


# ── Test 6: Early warning thresholds (warning@50%, critical@evict-1) ──
def test_early_warning_writes_system_health(mock_db: sqlite3.Connection) -> None:
    cfg = STALE_CONFIG["IRON_ORE_62"]
    # ore dated exactly at critical (evict-1) days before as_of_date
    ore_date = "2026-08-08"  # 6 days before 2026-08-14 = critical
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", ore_date, 94.0, 0),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    res = get_steel_crack_spread(mock_db, as_of_date="2026-08-14", is_live=True)
    assert isinstance(res, SteelCrackSpreadResult)
    row = mock_db.execute("SELECT status FROM system_health WHERE component=?", ("commodity_sensor:IRON_ORE_62",)).fetchone()
    assert row is not None
    assert row[0] == "STALE_CRITICAL"
    assert cfg["critical"] == 6


def test_warning_tier_at_half_evict(mock_db: sqlite3.Connection) -> None:
    # ore dated 4 days before as_of_date (>= warning 4, < critical 6)
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-10", 94.0, 0),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    res = get_steel_crack_spread(mock_db, as_of_date="2026-08-14", is_live=True)
    assert isinstance(res, SteelCrackSpreadResult)
    row = mock_db.execute("SELECT status FROM system_health WHERE component=?", ("commodity_sensor:IRON_ORE_62",)).fetchone()
    assert row is not None
    assert row[0] == "STALE_WARNING"


# ── Test 7: Replay mode never writes system_health ──
def test_replay_mode_is_silent(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-08", 94.0, 0),  # 6 days stale -> critical tier
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    res = get_steel_crack_spread(mock_db, as_of_date="2026-08-14", is_live=False)
    assert isinstance(res, SteelCrackSpreadResult)
    rows = mock_db.execute("SELECT COUNT(*) FROM system_health").fetchone()[0]
    assert rows == 0


# ── Test 8: Idempotent system_health upsert (no row bloat) ──
def test_system_health_upsert_idempotent(mock_db: sqlite3.Connection) -> None:
    _seed(
        mock_db,
        [
            ("HRC_CFR", "2026-08-10", 546.0, 0),
            ("IRON_ORE_62", "2026-08-08", 94.0, 0),
            ("COKING_COAL_HCC", "2026-08-11", 222.0, 0),
        ],
    )
    for _ in range(5):
        get_steel_crack_spread(mock_db, as_of_date="2026-08-14", is_live=True)
    rows = mock_db.execute("SELECT COUNT(*) FROM system_health").fetchone()[0]
    # Only IRON_ORE_62 (6d) crosses warning/critical; HRC 4d & coal 3d stay fresh.
    assert rows == 1
