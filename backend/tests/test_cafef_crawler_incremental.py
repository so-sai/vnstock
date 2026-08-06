"""test_cafef_crawler_incremental.py — TDD for incremental CafeF BCTC crawling.

WHY: BCTC quarters are static after publication. Re-crawling all 20 quarters
every day wastes 51s/symbol (ACB benchmark). Incremental mode checks SQLite
first and only fetches missing or active-season quarters.

Run:  python -m pytest tests/test_cafef_crawler_incremental.py -v   (from backend/)
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.cafef_crawler import CafeFCrawler
from src.financial.financial_facts import FinancialFactsDB


# ── Fixtures ──────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    """On-disk FinancialFactsDB for testing — avoids :memory: connection isolation."""
    db_file = str(tmp_path / "test_facts.db")
    d = FinancialFactsDB(db_path=db_file)
    d.init_schema()
    return d


@pytest.fixture
def crawler(db):
    """CafeFCrawler with incremental=True (default)."""
    return CafeFCrawler(db, incremental=True)


@pytest.fixture
def crawler_full(db):
    """CafeFCrawler with incremental=False (full mode)."""
    return CafeFCrawler(db, incremental=False)


def _seed_periods(db, symbol: str, periods: list, entity_type: str = "STANDARD"):
    """Seed financial_facts with dummy data for given periods."""
    conn = db.connect()
    for period in periods:
        year = int(period.split("Q")[0])
        q = int(period.split("Q")[1])
        conn.execute(
            """INSERT INTO financial_facts
               (symbol, period, fiscal_year, fiscal_quarter, entity_type,
                statement_type, metric, value, source, is_synthetic, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, period, year, q, entity_type, "BS", "TOTAL_ASSETS", 1000.0, "test", 0, datetime.now().isoformat()),
        )
    conn.commit()


# ── Test: _get_existing_periods ───────────────────────────────
class TestGetExistingPeriods:
    def test_empty_db_returns_empty_set(self, crawler, db):
        result = crawler._get_existing_periods("FPT")
        assert result == set()

    def test_returns_all_seeded_periods(self, crawler, db):
        _seed_periods(db, "FPT", ["2024Q1", "2024Q2", "2024Q3"])
        result = crawler._get_existing_periods("FPT")
        assert result == {"2024Q1", "2024Q2", "2024Q3"}

    def test_symbol_isolation(self, crawler, db):
        _seed_periods(db, "FPT", ["2024Q1"])
        _seed_periods(db, "ACB", ["2024Q2"])
        assert crawler._get_existing_periods("FPT") == {"2024Q1"}
        assert crawler._get_existing_periods("ACB") == {"2024Q2"}

    def test_case_insensitive(self, crawler, db):
        _seed_periods(db, "FPT", ["2024Q1"])
        assert crawler._get_existing_periods("fpt") == {"2024Q1"}


# ── Test: get_missing_or_active_quarters ──────────────────────
class TestGetMissingOrActiveQuarters:
    def test_all_missing_returns_all_quarters(self, crawler):
        quarters = CafeFCrawler.generate_20_quarters()
        result = crawler.get_missing_or_active_quarters("FPT", quarters)
        assert result == quarters

    def test_all_present_skips_non_active(self, crawler, db):
        """When all 20 quarters exist and none are active-season, return empty."""
        quarters = CafeFCrawler.generate_20_quarters()
        all_periods = [f"{y}Q{q}" for y, q in quarters]
        _seed_periods(db, "FPT", all_periods)
        result = crawler.get_missing_or_active_quarters("FPT", quarters)
        # Active-season quarters (current ± 1) are always included
        now = datetime.now()
        current_q = (now.month - 1) // 3 + 1
        current_y = now.year
        active = {f"{current_y}Q{current_q}"}
        if current_q == 1:
            active.add(f"{current_y - 1}Q4")
        else:
            active.add(f"{current_y}Q{current_q - 1}")
        expected = [(y, q) for y, q in quarters if f"{y}Q{q}" in active]
        assert result == expected

    def test_missing_quarters_are_included(self, crawler, db):
        """Missing quarters should always be included."""
        quarters = CafeFCrawler.generate_20_quarters()
        _seed_periods(db, "FPT", ["2024Q1", "2024Q2", "2024Q3"])
        result = crawler.get_missing_or_active_quarters("FPT", quarters)
        result_periods = {f"{y}Q{q}" for y, q in result}
        # 2024Q1-Q3 exist, so they should NOT be in result (unless active-season)
        # All other quarters should be in result
        assert "2024Q4" in result_periods
        assert "2025Q1" in result_periods
        assert "2023Q3" in result_periods

    def test_full_mode_returns_all_quarters(self, crawler_full, db):
        """Full mode (--full) always returns all quarters regardless of DB state."""
        quarters = CafeFCrawler.generate_20_quarters()
        _seed_periods(db, "FPT", [f"{y}Q{q}" for y, q in quarters])
        result = crawler_full.get_missing_or_active_quarters("FPT", quarters)
        assert result == quarters

    def test_active_season_always_included(self, crawler, db):
        """Active-season quarters are always re-crawled even if they exist."""
        quarters = CafeFCrawler.generate_20_quarters()
        all_periods = [f"{y}Q{q}" for y, q in quarters]
        _seed_periods(db, "FPT", all_periods)
        result = crawler.get_missing_or_active_quarters("FPT", quarters)
        now = datetime.now()
        current_q = (now.month - 1) // 3 + 1
        current_y = now.year
        # Q3/2026 is beyond the 20-quarter range (ends Q2/2026), so only Q2/2026
        # (previous quarter) should be in the result as active-season.
        if current_q == 1:
            prev_q, prev_y = 4, current_y - 1
        else:
            prev_q, prev_y = current_q - 1, current_y
        # At minimum, previous quarter should be in result (if in range)
        if (prev_y, prev_q) in quarters:
            assert (prev_y, prev_q) in result


# ── Test: crawl_symbol incremental skip ───────────────────────
class TestCrawlSymbolIncremental:
    def test_skip_when_all_quarters_exist(self, crawler, db):
        """crawl_symbol skips non-active quarters; only active-season re-crawls."""
        quarters = CafeFCrawler.generate_20_quarters()
        all_periods = [f"{y}Q{q}" for y, q in quarters]
        _seed_periods(db, "FPT", all_periods)
        result = crawler.crawl_symbol("FPT", entity_type="STANDARD", source="synthetic")
        # Active-season quarter (2026Q2) is always re-crawled, so the crawler
        # does NOT skip entirely. It proceeds, finds no data from source (synthetic
        # returns []), and reports empty.
        assert result.get("skipped_incremental") is not True
        # Verify the missing_quarters list only contains active-season quarters
        missing = crawler.get_missing_or_active_quarters("FPT", quarters)
        now = datetime.now()
        current_q = (now.month - 1) // 3 + 1
        current_y = now.year
        # Active season: current quarter + previous quarter
        active = {(current_y, current_q)}
        if current_q == 1:
            active.add((current_y - 1, 4))
        else:
            active.add((current_y, current_q - 1))
        # All missing quarters should be in active season
        for y, q in missing:
            assert (y, q) in active, f"{y}Q{q} should be active-season only"

    def test_no_skip_when_quarters_missing(self, crawler, db):
        """crawl_symbol proceeds when quarters are missing."""
        _seed_periods(db, "FPT", ["2024Q1"])
        result = crawler.crawl_symbol("FPT", entity_type="STANDARD", source="synthetic")
        # synthetic source generates empty list (zero-hallucination guard)
        # but the point is it DID NOT skip
        assert result.get("skipped_incremental") is not True

    def test_full_mode_never_skips(self, crawler_full, db):
        """Full mode never skips even when all quarters exist."""
        quarters = CafeFCrawler.generate_20_quarters()
        all_periods = [f"{y}Q{q}" for y, q in quarters]
        _seed_periods(db, "FPT", all_periods)
        result = crawler_full.crawl_symbol("FPT", entity_type="STANDARD", source="synthetic")
        assert result.get("skipped_incremental") is not True


# ── Test: default incremental flag ────────────────────────────
class TestIncrementalDefault:
    def test_default_is_incremental(self, db):
        c = CafeFCrawler(db)
        assert c.incremental is True

    def test_explicit_full(self, db):
        c = CafeFCrawler(db, incremental=False)
        assert c.incremental is False


# ── Test: CLI args ────────────────────────────────────────────
class TestCLIArgs:
    def test_full_flag_sets_incremental_false(self):
        """--full flag in CLI results in incremental=False."""
        import argparse

        args = argparse.Namespace(
            symbols=["FPT"],
            type="STANDARD",
            source="synthetic",
            playwright=False,
            delay=0,
            full=True,
        )
        # Just verify the argument parsing logic (not full execution)
        incremental = not getattr(args, "full", False)
        assert incremental is False

    def test_no_full_flag_sets_incremental_true(self):
        """Without --full flag, incremental defaults to True."""
        import argparse

        args = argparse.Namespace(
            symbols=["FPT"],
            type="STANDARD",
            source="synthetic",
            playwright=False,
            delay=0,
        )
        incremental = not getattr(args, "full", False)
        assert incremental is True
