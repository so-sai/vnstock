"""test_ingest_staging_annual.py — Nạp staging ANNUAL vào financial_facts_annual.

WHY (BƯỚC 3): staging (vnfinancialdata) chỉ chứa dữ liệu NĂM đã kiểm toán. Bảng
financial_facts_annual lưu bản sao chuẩn hóa trong DB với source=audited_staging
(Provenance Lock — bất khả xâm phạm). Fail-closed: ambiguous (nhiều giá trị cho cùng
khái niệm) → SKIP, không bịa; metric ngoài map → không đoán.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.financial.financial_facts import FinancialFactsDB
from src.tools.ingest_staging_annual import (
    MISMATCHED,
    SOURCE,
    VERIFIED,
    apply_annual_verification,
    ingest_staging_annual,
)


def _make_staging(tmp_path: Path, rows: list[dict], stmt: str = "income_statement") -> Path:
    """Dựng cây thư mục staging giống vnfinancialdata với 1 parquet."""
    root = tmp_path / "vnfinancialdata" / "data" / stmt
    root.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(root / "HSX.parquet", index=False)
    return tmp_path / "vnfinancialdata"


@pytest.fixture()
def db(tmp_path) -> FinancialFactsDB:
    d = FinancialFactsDB(db_path=str(tmp_path / "ff.db"))
    d.init_schema()
    d.register_entity("AAA", "STANDARD")
    return d


class TestIngestStagingAnnual:
    def test_ingest_writes_audited_facts(self, tmp_path, db):
        staging = _make_staging(
            tmp_path,
            [
                {"ticker": "AAA", "year": 2025, "item_name": "Doanh số thuần", "value": 10_728_000_000_000.0},
                {"ticker": "AAA", "year": 2025, "item_name": "Lãi/(lỗ) thuần sau thuế", "value": 423_000_000_000.0},
            ],
        )
        stats = ingest_staging_annual(db, staging, year=2025)
        assert stats["facts"] == 2
        assert stats["ambiguous"] == 0
        row = db.connect().execute(
            "SELECT value, source, statement_type FROM financial_facts_annual "
            "WHERE symbol='AAA' AND fiscal_year=2025 AND metric='REVENUE'"
        ).fetchone()
        assert row[0] == pytest.approx(10_728_000_000_000.0)
        assert row[1] == SOURCE
        assert row[2] == "IS"

    def test_ingest_is_idempotent(self, tmp_path, db):
        staging = _make_staging(
            tmp_path,
            [{"ticker": "AAA", "year": 2025, "item_name": "Doanh số thuần", "value": 10_728_000_000_000.0}],
        )
        ingest_staging_annual(db, staging, year=2025)
        ingest_staging_annual(db, staging, year=2025)
        n = db.connect().execute(
            "SELECT COUNT(*) FROM financial_facts_annual WHERE symbol='AAA'"
        ).fetchone()[0]
        assert n == 1, "upsert phải idempotent — không nhân bản dòng"

    def test_ambiguous_value_skipped(self, tmp_path, db):
        staging = _make_staging(
            tmp_path,
            [
                {"ticker": "AAA", "year": 2025, "item_name": "Doanh số thuần", "value": 10_728_000_000_000.0},
                {"ticker": "AAA", "year": 2025, "item_name": "Doanh số thuần", "value": 9_000_000_000_000.0},
            ],
        )
        stats = ingest_staging_annual(db, staging, year=2025)
        assert stats["facts"] == 0
        assert stats["ambiguous"] == 1
        n = db.connect().execute(
            "SELECT COUNT(*) FROM financial_facts_annual WHERE symbol='AAA'"
        ).fetchone()[0]
        assert n == 0, "ambiguous (2 giá trị khác nhau) phải SKIP, không bịa"

    def test_unknown_metric_not_guessed(self, tmp_path, db):
        staging = _make_staging(
            tmp_path,
            [{"ticker": "AAA", "year": 2025, "item_name": "Bán hàng và CCDV", "value": 999.0}],
        )
        stats = ingest_staging_annual(db, staging, year=2025)
        assert stats["facts"] == 0
        n = db.connect().execute(
            "SELECT COUNT(*) FROM financial_facts_annual WHERE symbol='AAA'"
        ).fetchone()[0]
        assert n == 0, "metric ngoài ENTITY_METRIC_MAP không được đoán/ghi"

    def test_stock_semantics_statement_type_bs(self, tmp_path, db):
        staging = _make_staging(
            tmp_path,
            [{"ticker": "AAA", "year": 2025, "item_name": "Vốn chủ sở hữu", "value": 6_079_000_000_000.0}],
            stmt="balance_sheet",
        )
        ingest_staging_annual(db, staging, year=2025)
        row = db.connect().execute(
            "SELECT value, statement_type FROM financial_facts_annual "
            "WHERE symbol='AAA' AND fiscal_year=2025 AND metric='TOTAL_EQUITY'"
        ).fetchone()
        assert row[0] == pytest.approx(6_079_000_000_000.0)
        assert row[1] == "BS"


class TestAnnualVerification:
    """Audit Verification Gate: MATCH → VERIFIED_BY_AUDITED_ANNUAL; MISMATCH → fail-closed."""

    @pytest.fixture()
    def staging(self, tmp_path):
        return _make_staging(
            tmp_path,
            [
                {"ticker": "AAA", "year": 2025, "item_name": "Doanh số thuần", "value": 10_000_000_000_000.0},
                {"ticker": "AAA", "year": 2025, "item_name": "Lãi/(lỗ) thuần sau thuế", "value": 400_000_000_000.0},
            ],
        )

    def test_match_gets_verified_flag(self, tmp_path, db, staging):
        ingest_staging_annual(db, staging, year=2025)
        for q in (1, 2, 3, 4):
            db.write_batch(
                "AAA",
                {"_fiscal_year": 2025, "_fiscal_quarter": q, "REVENUE": 2_500_000_000_000.0,
                 "NET_INCOME": 100_000_000_000.0},
                "STANDARD",
                source="vci",
            )
        stats = apply_annual_verification(db, staging, year=2025)
        assert stats["verified"] >= 2, stats
        row = db.connect().execute(
            "SELECT verification_status FROM financial_facts_annual "
            "WHERE symbol='AAA' AND fiscal_year=2025 AND metric='REVENUE'"
        ).fetchone()
        assert row[0] == VERIFIED

    def test_mismatch_gets_fail_closed_flag(self, tmp_path, db, staging):
        ingest_staging_annual(db, staging, year=2025)
        for q in (1, 2, 3, 4):
            db.write_batch(
                "AAA",
                {"_fiscal_year": 2025, "_fiscal_quarter": q, "REVENUE": 9_000_000_000_000.0,
                 "NET_INCOME": 100_000_000_000.0},
                "STANDARD",
                source="vci",
            )
        # REVENUE Σ=36T vs annual 10T → MISMATCH
        stats = apply_annual_verification(db, staging, year=2025)
        assert stats["mismatched"] >= 1, stats
        row = db.connect().execute(
            "SELECT verification_status FROM financial_facts_annual "
            "WHERE symbol='AAA' AND fiscal_year=2025 AND metric='REVENUE'"
        ).fetchone()
        assert row[0] == MISMATCHED

    def test_incomplete_quarters_no_flag(self, tmp_path, db, staging):
        ingest_staging_annual(db, staging, year=2025)
        db.write_batch(
            "AAA",
            {"_fiscal_year": 2025, "_fiscal_quarter": 1, "REVENUE": 2_500_000_000_000.0},
            "STANDARD",
            source="vci",
        )
        apply_annual_verification(db, staging, year=2025)
        row = db.connect().execute(
            "SELECT verification_status FROM financial_facts_annual "
            "WHERE symbol='AAA' AND fiscal_year=2025 AND metric='REVENUE'"
        ).fetchone()
        assert row[0] == "", "thiếu quý → không phê duyệt (giữ '')"
