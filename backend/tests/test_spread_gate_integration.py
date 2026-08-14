"""Integration tests cho GATE 2 (Fundamental Spread Gate) trong unified_system_replay.

Covers the approved design (2026-08-14):
  - sector scoping: only STEEL is gated, others pass-through
  - PIT bottom confirmation: no look-ahead, >= 3 mốc mở rộng liên tiếp
  - fail-closed: missing data => False (INSUFFICIENT_DATA), never crashes replay
  - `_steel_spread_gate` returns True for non-steel symbols unconditionally
"""

from __future__ import annotations

import sqlite3

import pytest
from backtest.unified_system_replay import (
    SPREAD_BOTTOM_MIN_POINTS,
    STEEL_SECTOR,
    _spread_bottom_confirmed,
    _steel_spread_gate,
    _steel_spread_series_pit,
)


@pytest.fixture
def mock_db() -> sqlite3.Connection:
    """In-memory SQLite with macro_history + symbol_industry schema."""
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
        CREATE TABLE symbol_industry (
            symbol TEXT NOT NULL,
            icb_name2 TEXT,
            PRIMARY KEY (symbol)
        );
        """
    )
    yield conn
    conn.close()


def _seed_commodity(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO macro_history (variable, date, value, is_stale) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def _weekly_series(lows_at: int, total: int = 7, base: float = 280.0) -> list[tuple[str, float]]:
    """Build a weekly V-shape series; `lows_at` = index of the trough (0-based).

    Values decline toward the trough, then expand after it.
    """
    series = []
    for i in range(total):
        if i < lows_at:
            spread = base + (lows_at - i) * 8.0  # declining toward trough
        elif i == lows_at:
            spread = base  # trough
        else:
            spread = base + (i - lows_at) * 10.0  # expanding after trough
        series.append((f"2026-0{i + 1:02d}", round(spread, 2)))
    return series


# ── Pure bottom-detection tests ──
def test_bottom_confirmed_recent_expansion() -> None:
    # bottom at index 2, then 4 consecutive expansions (>= 3 required)
    series = _weekly_series(lows_at=2)
    assert _spread_bottom_confirmed(series) is True


def test_bottom_confirmed_minimum_points_boundary() -> None:
    # exactly min_points mốc expansion (low at index 2, latest index 6 => 3 points after)
    series = _weekly_series(lows_at=2)
    assert _spread_bottom_confirmed(series, min_points=SPREAD_BOTTOM_MIN_POINTS) is True
    # low at index 4 => only 2 points after => fail
    series_2 = _weekly_series(lows_at=4)
    assert _spread_bottom_confirmed(series_2) is False


def test_bottom_not_confirmed_when_making_new_low() -> None:
    # still falling: latest is the minimum => fail
    series = [(f"2026-0{i + 1:02d}", 300.0 - i * 10.0) for i in range(7)]
    assert _spread_bottom_confirmed(series) is False


def test_bottom_not_confirmed_insufficient_points() -> None:
    assert _spread_bottom_confirmed([]) is False
    assert _spread_bottom_confirmed([("2026-01-01", 300.0), ("2026-01-08", 290.0)]) is False


def test_bottom_confirmed_requires_sustained_expansion() -> None:
    # low at 2, but then a dip again at index 4 (new low after rebound) => fail
    series = [
        ("d1", 300.0),
        ("d2", 290.0),
        ("d3", 280.0),  # low here
        ("d4", 285.0),
        ("d5", 275.0),  # new low -> no sustained expansion
        ("d6", 282.0),
        ("d7", 290.0),
    ]
    assert _spread_bottom_confirmed(series) is False


# ── PIT series construction tests ──
def test_series_pit_no_lookahead(mock_db: sqlite3.Connection) -> None:
    _seed_commodity(
        mock_db,
        [
            # 4+ distinct historical dates so the guard passes, all <= as_of 08-05
            ("HRC_CFR", "2026-08-01", 500.0, 0),
            ("IRON_ORE_62", "2026-08-01", 100.0, 0),
            ("COKING_COAL_HCC", "2026-08-01", 200.0, 0),
            ("HRC_CFR", "2026-08-02", 500.0, 0),
            ("HRC_CFR", "2026-08-03", 500.0, 0),
            ("HRC_CFR", "2026-08-04", 500.0, 0),
            ("HRC_CFR", "2026-08-15", 700.0, 0),  # future vs as_of 08-05
            ("IRON_ORE_62", "2026-08-15", 80.0, 0),
            ("COKING_COAL_HCC", "2026-08-15", 150.0, 0),
        ],
    )
    series = _steel_spread_series_pit(mock_db, as_of_date="2026-08-05")
    # only 08-01..08-04 rows (identical values), never 08-15
    assert len(series) == 4
    assert all(d <= "2026-08-05" for d, _ in series)
    assert all(pytest.approx(s, 0.01) == 240.0 for _, s in series)


def test_series_pit_multiple_dates(mock_db: sqlite3.Connection) -> None:
    _seed_commodity(
        mock_db,
        [
            ("HRC_CFR", "2026-07-01", 500.0, 0),
            ("IRON_ORE_62", "2026-07-01", 100.0, 0),
            ("COKING_COAL_HCC", "2026-07-01", 200.0, 0),
            ("HRC_CFR", "2026-07-08", 520.0, 0),
            ("IRON_ORE_62", "2026-07-08", 100.0, 0),
            ("COKING_COAL_HCC", "2026-07-08", 200.0, 0),
            ("HRC_CFR", "2026-07-15", 540.0, 0),
            ("IRON_ORE_62", "2026-07-15", 100.0, 0),
            ("COKING_COAL_HCC", "2026-07-15", 200.0, 0),
        ],
    )
    series = _steel_spread_series_pit(mock_db, as_of_date="2026-07-15")
    assert len(series) == 3
    assert [s[1] for s in series] == pytest.approx([240.0, 260.0, 280.0], 0.01)


# ── Gate scoping tests ──
def test_gate_pass_through_non_steel(mock_db: sqlite3.Connection) -> None:
    assert _steel_spread_gate(mock_db, "BANK", "2026-08-14") is True
    assert _steel_spread_gate(mock_db, "RE", "2026-08-14") is True
    assert _steel_spread_gate(mock_db, None, "2026-08-14") is True


def test_gate_steel_fail_closed_missing_data(mock_db: sqlite3.Connection) -> None:
    # no commodity data at all => INSUFFICIENT_DATA => False (fail-closed)
    assert _steel_spread_gate(mock_db, STEEL_SECTOR, "2026-08-14") is False


def test_gate_steel_blocks_when_no_bottom(mock_db: sqlite3.Connection) -> None:
    # steady decline, latest = new low => no bottom => False
    rows = []
    for i in range(7):
        d = f"2026-07-{i + 1:02d}"
        spread = 300.0 - i * 10.0
        rows.append(("HRC_CFR", d, spread, 0))
    _seed_commodity(mock_db, rows)
    # but spread needs all 3 components; seed constant ore/coal so series forms
    _seed_commodity(
        mock_db,
        [
            ("IRON_ORE_62", "2026-07-01", 100.0, 0),
            ("COKING_COAL_HCC", "2026-07-01", 200.0, 0),
        ],
    )
    assert _steel_spread_gate(mock_db, STEEL_SECTOR, "2026-08-14") is False


def test_gate_steel_passes_when_bottom_confirmed(mock_db: sqlite3.Connection) -> None:
    # V-shape: bottom at week 2, expansion after
    rows = []
    spreads = [280.0, 270.0, 260.0, 264.0, 268.0, 272.0, 276.0]
    for i, spread in enumerate(spreads):
        d = f"2026-07-{i + 1:02d}"
        rows.append(("HRC_CFR", d, spread, 0))
    _seed_commodity(mock_db, rows)
    _seed_commodity(
        mock_db,
        [
            ("IRON_ORE_62", "2026-07-01", 100.0, 0),
            ("COKING_COAL_HCC", "2026-07-01", 200.0, 0),
        ],
    )
    assert _steel_spread_gate(mock_db, STEEL_SECTOR, "2026-08-14") is True
