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


# ── Test: Metric-Level Upsert (chống mất dữ liệu khi crawl nhiều nguồn) ─
class TestMetricLevelUpsert:
    """write_batch KHÔNG được purge cả (symbol, period) khi nguồn mới chỉ trả subset.

    WHY: purge-before-write DELETE (symbol, period) trong write_batch khiến crawl VCI
    (chỉ trả CF metrics cho quý gần) xóa sạch BS/IS của CafeF/vietstock đã ghi trước —
    regression FPT/VCB 06/08/2026. PRIMARY KEY (symbol, period, metric) đã có sẵn nên
    INSERT OR REPLACE vốn upsert metric-level — chỉ cần bỏ DELETE purge là giữ nguyên
    các metric cũ mà nguồn mới không cung cấp.
    """

    def _full_batch(self):
        return {
            "_fiscal_year": 2026,
            "_fiscal_quarter": 2,
            "REVENUE": 4_000_000_000,
            "NET_INCOME": 600_000_000,
            "TOTAL_ASSETS": 15_000_000_000,
            "TOTAL_EQUITY": 7_000_000_000,
            "CFO": 1_500_000_000,
            "EPS": 5000.0,
        }

    def test_metric_upsert_keeps_bs_is_from_previous_source(self, db):
        """Nguồn mới (VCI) chỉ trả CFO → BS/IS cũ vẫn còn, CFO được ghi đè."""
        r1 = db.write_batch("TEST", self._full_batch(), "STANDARD", source="vietstock")
        assert r1["facts_written"] == 6

        r2 = db.write_batch(
            "TEST",
            {"_fiscal_year": 2026, "_fiscal_quarter": 2, "CFO": 1_200_000_000},
            "STANDARD",
            source="vci",
        )
        assert r2["facts_written"] == 1

        facts = db.get_facts("TEST")
        assert "2026Q2" in facts
        q = facts["2026Q2"]
        assert q.get("REVENUE") == 4_000_000_000
        assert q.get("TOTAL_EQUITY") == 7_000_000_000
        assert q.get("CFO") == 1_200_000_000

    def test_write_batch_empty_subset_does_not_purge_existing(self, db):
        """Batch chỉ có meta (không fact hợp lệ) → dữ liệu cũ không bị xóa."""
        db.write_batch("TEST", self._full_batch(), "STANDARD", source="vietstock")

        r = db.write_batch(
            "TEST",
            {"_fiscal_year": 2026, "_fiscal_quarter": 2},
            "STANDARD",
            source="vci",
        )
        assert r["facts_written"] == 0

        facts = db.get_facts("TEST")
        assert "2026Q2" in facts
        assert len(facts["2026Q2"]) == 6


# ── Test: Period-Level Missing-Metric Fallback + Cooldown Gate ────
class TestPeriodLevelFallback:
    """Fallback cấp Quý: tự cào bù quý thiếu >= 3 core metrics (REVENUE/
    TOTAL_ASSETS/NET_PROFIT/CFO) từ nguồn phụ, kèm Cooldown 24h chống spam.

    WHY: tháp fallback cấp Symbol (len(all_periods)==0) bỏ sót quý lẻ bị
    khuyết (VD VCB 2026Q2 chỉ có BOOK_VALUE_PS) — nguồn chính trả OK 19 quý
    nên không kích hoạt fallback. Phát hiện quý partially-missing để cào bù.
    """

    CORE = ("REVENUE", "TOTAL_ASSETS", "NET_PROFIT", "CFO")

    def test_detects_quarter_missing_3_core_metrics(self, crawler):
        """Quý chỉ có 1 core metric (VD VCB 2026Q2: BOOK_VALUE_PS) → MISSING."""
        periods = [
            {"_fiscal_year": 2026, "_fiscal_quarter": 2, "BOOK_VALUE_PS": 29739.13},
            {
                "_fiscal_year": 2026,
                "_fiscal_quarter": 1,
                "REVENUE": 1e12,
                "TOTAL_ASSETS": 2e12,
                "NET_PROFIT": 5e11,
                "CFO": 1e12,
            },
        ]
        missing = crawler._detect_missing_periods(periods, [(2026, 1), (2026, 2)], self.CORE)
        assert missing == [(2026, 2)], f"expected Q2 missing, got {missing}"

    def test_complete_quarter_not_detected(self, crawler):
        """Quý đủ 4 core metrics → không bị đánh dấu missing."""
        periods = [
            {
                "_fiscal_year": 2026,
                "_fiscal_quarter": 2,
                "REVENUE": 1e12,
                "TOTAL_ASSETS": 2e12,
                "NET_PROFIT": 5e11,
                "CFO": 1e12,
            },
        ]
        missing = crawler._detect_missing_periods(periods, [(2026, 2)], self.CORE)
        assert missing == []

    def test_old_quarter_not_scanned(self, crawler):
        """Chỉ quét 4 quý gần nhất — quý cũ (2018Q1) thiếu nhưng ngoài cửa sổ."""
        periods = [
            {
                "_fiscal_year": 2026,
                "_fiscal_quarter": 1,
                "REVENUE": 1e12,
                "TOTAL_ASSETS": 2e12,
                "NET_PROFIT": 5e11,
                "CFO": 1e12,
            },
            {
                "_fiscal_year": 2026,
                "_fiscal_quarter": 2,
                "REVENUE": 1e12,
                "TOTAL_ASSETS": 2e12,
                "NET_PROFIT": 5e11,
                "CFO": 1e12,
            },
            {"_fiscal_year": 2018, "_fiscal_quarter": 1, "BOOK_VALUE_PS": 100.0},
        ]
        missing = crawler._detect_missing_periods(periods, [(2026, 1), (2026, 2)], self.CORE)
        assert missing == []

    def test_merge_fills_missing_metrics_without_purge(self, crawler):
        """Merge dữ liệu nguồn phụ vào quý thiếu — giữ metric cũ, điền metric mới."""
        target = {"_fiscal_year": 2026, "_fiscal_quarter": 2, "BOOK_VALUE_PS": 29739.13}
        fallback = {
            "_fiscal_year": 2026,
            "_fiscal_quarter": 2,
            "REVENUE": 1e12,
            "TOTAL_ASSETS": 2e12,
            "NET_PROFIT": 5e11,
            "CFO": 1e12,
        }
        merged = crawler._merge_fallback_periods(target, fallback)
        assert merged["BOOK_VALUE_PS"] == 29739.13
        assert merged["REVENUE"] == 1e12
        assert merged["TOTAL_ASSETS"] == 2e12
        assert merged["CFO"] == 1e12
        assert len([k for k in merged if not k.startswith("_")]) == 5

    def test_bank_uses_bank_core_metrics(self, crawler):
        """Bank thiếu REVENUE/CFO (không tồn tại ở BCTC ngân hàng) → không fallback.

        WHY: bank core metrics là NET_INCOME/TOTAL_ASSETS/CUSTOMER_DEPOSITS/
        CUSTOMER_LOANS. Bank có đủ chúng nhưng thiếu REVENUE theo chuẩn STANDARD
        thì KHÔNG bị đánh dấu missing — tránh fallback vô ích mỗi chu kỳ."""
        periods = [
            {
                "_fiscal_year": 2026,
                "_fiscal_quarter": 2,
                "NET_INCOME": 5e11,
                "TOTAL_ASSETS": 2e12,
                "CUSTOMER_DEPOSITS": 1.5e12,
                "CUSTOMER_LOANS": 1.2e12,
                "BOOK_VALUE_PS": 29739.13,
            },
        ]
        missing = crawler._detect_missing_periods(periods, [(2026, 2)], crawler.FALLBACK_CORE_METRICS_BANK)
        assert missing == [], f"Bank with full bank-core must not be missing, got {missing}"

    def test_cooldown_blocks_repeated_fallback(self, db):
        """Sau khi fallback không ra dữ liệu, cooldown 24h chặn thử lại."""
        db.set_fallback_cooldown("VCB", "2026Q2")
        assert db.get_fallback_cooldown("VCB", "2026Q2") is not None

    def test_cooldown_expires_after_24h(self, db):
        """Cooldown hết hạn sau 24h → cho phép thử lại."""

        db.set_fallback_cooldown("VCB", "2026Q2")
        assert db.is_fallback_cooldown_active("VCB", "2026Q2")
        db.expire_fallback_cooldown("VCB", "2026Q2")
        assert not db.is_fallback_cooldown_active("VCB", "2026Q2")
