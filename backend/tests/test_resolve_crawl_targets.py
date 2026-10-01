"""TDD: resolve_crawl_targets — chống ghi đè entity_type sai khi crawl.

Regression 2026-10-01:
  `ptck cafef-crawl --symbols ...` chạy qua scheduler MỖI ĐÊM (setup_scheduler)
  mà KHÔNG truyền --type → cmd_cafef_crawl cũ làm `args.type or "STANDARD"`
  → crawl_symbol(..., "STANDARD") → register_entity đè STANDARD lên BANK
  (VCB/ACB/HDB/MBB bị ghi sai, test_entity_registry_audit đỏ).

Quy tắc khóa tại đây:
  1. Không truyền --type → resolve từng symbol từ registry (fallback static
     ENTITY_TYPES): ngân hàng giữ BANK, còn lại STANDARD.
  2. Truyền --type tường minh → giữ nguyên (caller override).
  3. Idempotent: resolve không ghi DB (read-only).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.financial.financial_facts import FinancialFactsDB


class TestResolveCrawlTargets:
    def test_no_explicit_type_resolves_banks_from_registry(self):
        db = FinancialFactsDB()
        targets = db.resolve_crawl_targets(["VCB", "ACB"], None)
        assert targets == [("VCB", "BANK"), ("ACB", "BANK")]

    def test_no_explicit_type_resolves_standard_symbols(self):
        db = FinancialFactsDB()
        targets = db.resolve_crawl_targets(["FPT", "HPG"], None)
        assert targets == [("FPT", "STANDARD"), ("HPG", "STANDARD")]

    def test_explicit_type_wins(self):
        db = FinancialFactsDB()
        assert db.resolve_crawl_targets(["VCB"], "STANDARD") == [("VCB", "STANDARD")]

    def test_unknown_symbol_defaults_standard(self):
        db = FinancialFactsDB()
        assert db.resolve_crawl_targets(["ZZZZZZ"], None) == [("ZZZZZZ", "STANDARD")]

    def test_resolve_is_readonly(self):
        import sqlite3

        db = FinancialFactsDB()
        db.resolve_crawl_targets(["VCB", "ACB", "HDB", "MBB"], None)
        conn = sqlite3.connect(str(db.db_path))
        rows = dict(conn.execute("SELECT symbol, entity_type FROM entity_registry").fetchall())
        conn.close()
        assert rows.get("VCB") == "BANK"
        assert rows.get("ACB") == "BANK"

    def test_cmd_cafef_crawl_uses_resolver_not_blanket_standard(self):
        """Source guard: cmd không được quay về `args.type or "STANDARD"`."""
        src = (PROJECT_ROOT / "ptck.py").read_text(encoding="utf-8")
        assert 'args.type or "STANDARD"' not in src
        assert "resolve_crawl_targets" in src
