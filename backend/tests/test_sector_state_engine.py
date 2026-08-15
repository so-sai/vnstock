"""test_sector_state_engine.py — Regression tests for SectorStateEngine health/valuation pillars.

Locks the schema-alignment fix for BUG-001:
  - `_compute_health` used to query `health_ratios` with non-existent columns
    `(score, date)` — real schema is `(symbol, ratio_name, ratio_value, period)`.
  - `_compute_valuation` queried `valuation_scores` with `(score, date)` —
    real schema stores `z_score` by `period`.
  Both now read the correct PIT schema, return NEUTRAL 0.0 on missing data
  instead of crashing the CLI, and accept an injectable fin_db_path.

Run:  python -m pytest backend/tests/test_sector_state_engine.py -q
"""

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.core.macro.sector_state_engine import SectorStateEngine


def make_fin_db(tmp_path: Path) -> Path:
    """Create a financial_facts.db with the REAL health_ratios/valuation_scores schema."""
    path = tmp_path / "financial_facts.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    conn.execute("CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL, z_score REAL)")
    conn.commit()
    conn.close()
    return path


def seed_health(path: Path, rows: list[tuple]):
    conn = sqlite3.connect(str(path))
    conn.executemany(
        "INSERT INTO health_ratios (symbol, period, ratio_name, ratio_value) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def seed_valuation(path: Path, rows: list[tuple]):
    conn = sqlite3.connect(str(path))
    conn.executemany(
        "INSERT INTO valuation_scores (symbol, period, ratio_name, ratio_value, z_score) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def engine(fin_db: Path) -> SectorStateEngine:
    return SectorStateEngine(fin_db_path=fin_db)


# ── BUG-001: health reads real (ratio_name, ratio_value, period) schema ──


class TestComputeHealth:
    def test_strong_fundamentals_positive_score(self, tmp_path):
        """High ROE + margin, low D/E → positive health in [-1,1]."""
        db = make_fin_db(tmp_path)
        seed_health(
            db,
            [
                ("HPG", "2026Q2", "ROE", 0.08),  # quarterly → 0.32 annualized
                ("HPG", "2026Q2", "GROSS_MARGIN", 0.40),
                ("HPG", "2026Q2", "DEBT_TO_EQUITY", 0.40),
                ("NKG", "2026Q2", "ROE", 0.07),
                ("NKG", "2026Q2", "GROSS_MARGIN", 0.35),
                ("NKG", "2026Q2", "DEBT_TO_EQUITY", 0.50),
            ],
        )
        e = engine(db)
        health = e._compute_health(["HPG", "NKG"])
        assert -1.0 <= health <= 1.0
        assert health > 0.0, f"strong fundamentals should be positive, got {health}"

    def test_weak_fundamentals_negative_score(self, tmp_path):
        """Low ROE, high D/E, thin margin → negative health."""
        db = make_fin_db(tmp_path)
        seed_health(
            db,
            [
                ("X", "2026Q2", "ROE", 0.01),  # 0.04 annualized
                ("X", "2026Q2", "GROSS_MARGIN", 0.05),
                ("X", "2026Q2", "DEBT_TO_EQUITY", 5.0),
                ("Y", "2026Q2", "ROE", 0.02),
                ("Y", "2026Q2", "GROSS_MARGIN", 0.08),
                ("Y", "2026Q2", "DEBT_TO_EQUITY", 4.0),
            ],
        )
        e = engine(db)
        health = e._compute_health(["X", "Y"])
        assert -1.0 <= health <= 1.0
        assert health < 0.0, f"weak fundamentals should be negative, got {health}"

    def test_missing_data_returns_neutral_not_crash(self, tmp_path):
        """Table exists but no rows for the symbols → 0.0 (NEUTRAL), no exception."""
        db = make_fin_db(tmp_path)
        e = engine(db)
        assert e._compute_health(["AAA"]) == 0.0

    def test_empty_symbols_returns_zero(self, tmp_path):
        db = make_fin_db(tmp_path)
        assert engine(db)._compute_health([]) == 0.0

    def test_missing_fin_db_returns_zero(self, tmp_path):
        """financial_facts.db absent → 0.0, never crash (fail-safe)."""
        missing = tmp_path / "no_such_financial_facts.db"
        e = engine(missing)
        assert e._compute_health(["HPG"]) == 0.0

    def test_pit_ignores_future_period(self, tmp_path):
        """PIT: periods AFTER the anchor quarter must not leak in.

        Only 2026Q1 exists → composite derives from it; a 2027Q1 row must be ignored.
        """
        db = make_fin_db(tmp_path)
        seed_health(
            db,
            [
                ("HPG", "2026Q1", "ROE", 0.08),
                ("HPG", "2026Q1", "GROSS_MARGIN", 0.40),
                ("HPG", "2026Q1", "DEBT_TO_EQUITY", 0.40),
                ("HPG", "2027Q1", "ROE", 0.005),  # future — must be ignored
                ("HPG", "2027Q1", "GROSS_MARGIN", 0.01),
                ("HPG", "2027Q1", "DEBT_TO_EQUITY", 9.0),
            ],
        )
        e = engine(db)
        h1 = e._compute_health(["HPG"])
        # Only the 2026Q1 strong ratios feed the composite.
        assert h1 > 0.0, f"future-period leakage should not drag health down, got {h1}"


# ── BUG-001: valuation reads real z_score schema ──


class TestComputeValuation:
    def test_cheap_stocks_positive_valuation(self, tmp_path):
        """Negative z-scores (cheap) → positive valuation score."""
        db = make_fin_db(tmp_path)
        seed_valuation(
            db,
            [("HPG", "2026Q2", "P/E", 5.0, -1.5), ("NKG", "2026Q2", "P/E", 6.0, -1.2)],
        )
        e = engine(db)
        val = e._compute_valuation(["HPG", "NKG"])
        assert -1.0 <= val <= 1.0
        assert val > 0.0, f"cheap sector should be positive, got {val}"

    def test_expensive_stocks_negative_valuation(self, tmp_path):
        """Positive z-scores (expensive) → negative valuation score."""
        db = make_fin_db(tmp_path)
        seed_valuation(
            db,
            [("HPG", "2026Q2", "P/E", 40.0, 2.0), ("NKG", "2026Q2", "P/E", 35.0, 1.5)],
        )
        e = engine(db)
        val = e._compute_valuation(["HPG", "NKG"])
        assert val < 0.0, f"expensive sector should be negative, got {val}"

    def test_missing_valuation_returns_zero(self, tmp_path):
        db = make_fin_db(tmp_path)
        assert engine(db)._compute_valuation(["AAA"]) == 0.0

    def test_missing_fin_db_returns_zero(self, tmp_path):
        missing = tmp_path / "no_such_financial_facts.db"
        assert engine(missing)._compute_valuation(["HPG"]) == 0.0

    def test_pit_ignores_future_period(self, tmp_path):
        """PIT: 2027Q1 (future) z-score must not leak into the sector valuation."""
        db = make_fin_db(tmp_path)
        seed_valuation(
            db,
            [
                ("HPG", "2026Q2", "P/E", 6.0, -1.5),
                ("HPG", "2027Q1", "P/E", 60.0, 3.0),  # future — must be ignored
            ],
        )
        e = engine(db)
        val = e._compute_valuation(["HPG"])
        assert val > 0.0, f"future cheap/expensive leakage, got {val}"


# ── Engine-level regression ──


class TestSectorStateEngineEndToEnd:
    def test_health_and_valuation_do_not_raise(self, tmp_path):
        """BUG-001: analyze() previously crashed on missing `score` column.

        Even with empty financial data, the engine must NOT raise; health and
        valuation pillars fall back to NEUTRAL 0.0 and other pillars still run.
        """
        db = make_fin_db(tmp_path)  # empty financial data
        e = engine(db)
        # Full pipeline needs screener_cache; exercise pillars directly instead
        # to lock the fail-safe contract without depending on a live DB.
        assert e._compute_health(["HPG", "NKG"]) == 0.0
        assert e._compute_valuation(["HPG", "NKG"]) == 0.0
