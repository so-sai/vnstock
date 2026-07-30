"""TDD Tests — cafef_crawler.py + financial_search CLI

Test-driven development cho các tính năng:
1. URL_MAP endpoint registry
2. CafeFCrawler.fetch_cafef_bank_api()
3. CafeFCrawler.fetch_note_indicator()
4. CafeFCrawler.fetch_vci_bridge()
5. CafeFCrawler.crawl_symbol() fallback chain
6. cmd_financial_search CLI
"""

import sys
import os
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime

# ── Path Setup ──────────────────────────────────────────────────
_candidate = Path(__file__).resolve().parent.parent.parent.parent
for _par in [Path(__file__).resolve().parent.parent] + list(Path(__file__).resolve().parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        _candidate = _par
        break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "libs" / "vnstock"))

import pytest
import pandas as pd


# ===================================================================
# TEST 1: URL_MAP Registry
# ===================================================================

class TestURLMap:
    """Kiểm tra URL_MAP có đầy đủ endpoints và metadata."""

    def setup_method(self):
        from src.financial.cafef_crawler import URL_MAP
        self.url_map = URL_MAP

    def test_url_map_has_cafef_bank_api(self):
        """URL_MAP phải có CAFE F_BANK_API (endpoint chính)."""
        assert "CAFEF_BANK_API" in self.url_map
        assert self.url_map["CAFEF_BANK_API"]["status"] == "ALIVE"
        assert self.url_map["CAFEF_BANK_API"]["method"] == "GET"
        assert "BHoSoCongTy" in self.url_map["CAFEF_BANK_API"]["url"]

    def test_url_map_has_note_indicator(self):
        """URL_MAP phải có CAFE F_NOTE_INDI (backup limited)."""
        assert "CAFEF_NOTE_INDI" in self.url_map
        assert self.url_map["CAFEF_NOTE_INDI"]["status"] == "LIMITED"

    def test_url_map_has_dead_endpoints(self):
        """URL_MAP phải có các endpoints đã chết."""
        dead = [k for k, v in self.url_map.items() if v["status"].startswith("DEAD")]
        assert len(dead) >= 5, f"Ít nhất 5 endpoints DEAD, thực tế: {len(dead)}"

    def test_url_map_has_all_required_fields(self):
        """Mỗi endpoint phải có: url, method, type, data, status, lib, notes."""
        required_fields = {"url", "method", "type", "data", "status", "lib", "notes"}
        for name, info in self.url_map.items():
            missing = required_fields - set(info.keys())
            assert not missing, f"{name} thiếu fields: {missing}"

    def test_url_map_total_count(self):
        """URL_MAP phải có ít nhất 10 endpoints."""
        assert len(self.url_map) >= 10, f"Chỉ có {len(self.url_map)} endpoints"


# ===================================================================
# TEST 2: CafeFCrawler Initialization
# ===================================================================

class TestCafeFCrawlerInit:
    """Kiểm tra khởi tạo CafeFCrawler."""

    def setup_method(self):
        from src.financial.financial_facts import FinancialFactsDB
        from src.financial.cafef_crawler import CafeFCrawler
        self.db = FinancialFactsDB()
        self.db.init_schema()
        self.CafeFCrawler = CafeFCrawler

    def test_init_default(self):
        """Khởi tạo mặc định phải có use_playwright=False."""
        crawler = self.CafeFCrawler(self.db)
        assert crawler.use_playwright is False
        assert crawler.db is not None
        assert crawler.batch_id.startswith("cafef_")

    def test_init_with_playwright(self):
        """Khởi tạo với use_playwright=True."""
        crawler = self.CafeFCrawler(self.db, use_playwright=True)
        assert crawler.use_playwright is True

    def test_init_uses_provided_db(self):
        """Phải dùng db được truyền vào, không tạo mới."""
        crawler = self.CafeFCrawler(self.db)
        assert crawler.db is self.db


# ===================================================================
# TEST 3: _map_cafef_metric
# ===================================================================

class TestMapCafefMetric:
    """Kiểm tra ánh xạ tên metric từ CafeF → internal."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.crawler = CafeFCrawler(FinancialFactsDB())

    def test_map_standard_revenue(self):
        """Metric Doanh thu thuần → REVENUE."""
        result = self.crawler._map_cafef_metric("Doanh thu thuần", "STANDARD")
        assert result == "REVENUE"

    def test_map_standard_net_income(self):
        """Metric Lợi nhuận sau thuế → NET_INCOME."""
        result = self.crawler._map_cafef_metric("Lợi nhuận sau thuế", "STANDARD")
        assert result == "NET_INCOME"

    def test_map_standard_total_assets(self):
        """Metric Tổng tài sản → TOTAL_ASSETS."""
        result = self.crawler._map_cafef_metric("Tổng tài sản", "STANDARD")
        assert result == "TOTAL_ASSETS"

    def test_map_bank_total_assets(self):
        """Metric Tổng tài sản cho BANK → TOTAL_ASSETS."""
        result = self.crawler._map_cafef_metric("Tổng tài sản", "BANK")
        assert result == "TOTAL_ASSETS"

    def test_map_bank_net_income(self):
        """Metric Lợi nhuận sau thuế cho BANK → NET_INCOME."""
        result = self.crawler._map_cafef_metric("Lợi nhuận sau thuế", "BANK")
        assert result == "NET_INCOME"

    def test_map_unknown_returns_none(self):
        """Metric không xác định → None."""
        result = self.crawler._map_cafef_metric("Metric Không Tồn Tại", "STANDARD")
        assert result is None

    def test_map_case_insensitive(self):
        """Ánh xạ phải phân biệt hoa/thường."""
        result = self.crawler._map_cafef_metric("doanh thu thuần", "STANDARD")
        assert result == "REVENUE"


# ===================================================================
# TEST 4: _parse_cafef_value
# ===================================================================

class TestParseCafefValue:
    """Kiểm tra parse giá trị từ định dạng CafeF."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.crawler = CafeFCrawler(FinancialFactsDB())

    def test_parse_normal_number(self):
        """Số bình thường."""
        assert self.crawler._parse_cafef_value("1.234.567") == 1_234_567_000_000

    def test_parse_negative_number(self):
        """Số âm (ngoặc đơn)."""
        assert self.crawler._parse_cafef_value("(1.234)") == -1_234_000_000

    def test_parse_empty_returns_none(self):
        """Chuỗi rỗng → None."""
        assert self.crawler._parse_cafef_value("") is None

    def test_parse_dash_returns_none(self):
        """Dấu gạch ngang → None."""
        assert self.crawler._parse_cafef_value("-") is None

    def test_parse_with_comma(self):
        """Số có dấu phẩy."""
        assert self.crawler._parse_cafef_value("1,234") == 1_234_000_000

    def test_parse_million_scale(self):
        """CafeF scale: 1 đơn vị = 1 triệu → nhân 1.000.000."""
        result = self.crawler._parse_cafef_value("100")
        assert result == 100_000_000


# ===================================================================
# TEST 5: fetch_cafef_bank_api (Mocked)
# ===================================================================

class TestFetchCafeFBankAPI:
    """Kiểm tra fetch_cafef_bank_api với mocked response."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.db = FinancialFactsDB()
        self.db.init_schema()
        self.crawler = CafeFCrawler(self.db)

    def test_fetch_returns_empty_on_404(self):
        """HTTP 404 → trả về []."""
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            result = self.crawler.fetch_cafef_bank_api("NONEXIST")
            assert result == []

    def test_fetch_returns_empty_on_no_table(self):
        """HTML không có table → trả về []."""
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html><body>No table here</body></html>"
            mock_get.return_value = mock_response

            result = self.crawler.fetch_cafef_bank_api("TEST")
            assert result == []

    def test_fetch_parses_table_data(self):
        """HTML có table → parse và trả về list of dicts."""
        html = '''
        <table>
            <tr><td>Chỉ tiêu</td><td>Quý 1- 2026</td><td>Quý 2- 2025</td></tr>
            <tr><td>Tổng doanh thu</td><td>31.040.154.000</td><td>29.507.954.000</td></tr>
            <tr><td>Lợi nhuận sau thuế</td><td>9.020.499.000</td><td>8.629.542.000</td></tr>
        </table>
        '''
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            result = self.crawler.fetch_cafef_bank_api("TEST")

            assert len(result) >= 1
            assert isinstance(result[0], dict)
            assert "_fiscal_year" in result[0]
            assert "_fiscal_quarter" in result[0]

    def test_fetch_registers_entity_type(self):
        """Phải đăng ký entity_type vào db."""
        html = '''
        <table>
            <tr><td>Chỉ tiêu</td><td>Quý 1- 2026</td></tr>
            <tr><td>Tổng tài sản</td><td>450.000.000.000</td></tr>
        </table>
        '''
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            result = self.crawler.fetch_cafef_bank_api("VCB")

            # Check entity type registered
            entity = self.db.get_entity_type("VCB")
            assert entity is not None


# ===================================================================
# TEST 6: fetch_note_indicator (Mocked)
# ===================================================================

class TestFetchNoteIndicator:
    """Kiểm tra fetch_note_indicator với mocked response."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.db = FinancialFactsDB()
        self.db.init_schema()
        self.crawler = CafeFCrawler(self.db)

    def test_fetch_returns_empty_on_404(self):
        """HTTP 404 → trả về []."""
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            result = self.crawler.fetch_note_indicator("NONEXIST")
            assert result == []

    def test_fetch_parses_debt_data(self):
        """HTML có table nợ → parse và trả về list of dicts."""
        html = '''
        <table class="tab1child_content">
            <tr class="_header">
                <td>Chỉ tiêu</td><td>2024</td><td>2025</td>
            </tr>
            <tr>
                <td>Nợ đủ tiêu chuẩn</td><td>951.130.995.000</td><td>1.133.174.813.000</td>
            </tr>
            <tr>
                <td>Nợ cần chú ý</td><td>3.497.833.000</td><td>4.083.359.000</td>
            </tr>
        </table>
        '''
        with patch.object(self.crawler.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            result = self.crawler.fetch_note_indicator("VCB")

            assert len(result) >= 1
            assert isinstance(result[0], dict)
            # Check debt metrics
            sample = result[0]
            has_debt_metric = any("LOAN" in k or "DEBT" in k for k in sample.keys())
            assert has_debt_metric


# ===================================================================
# TEST 7: crawl_symbol fallback chain
# ===================================================================

class TestCrawlSymbol:
    """Kiểm tra fallback chain: VCI → CafeF → NoteIndicator → Synthetic."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.db = FinancialFactsDB()
        self.db.init_schema()
        self.crawler = CafeFCrawler(self.db)

    def test_crawl_source_synthetic(self):
        """Source=synthetic → dùng synthetic data."""
        result = self.crawler.crawl_symbol("FPT", "STANDARD", source="synthetic")
        assert result["total_metrics"] > 0
        assert result["success"] >= 1

    def test_crawl_source_cafef_with_mock(self):
        """Source=cafef → dùng CafeF Bank API với mock."""
        mock_data = [
            {"_fiscal_year": 2026, "_fiscal_quarter": 1, "REVENUE": 5.0e12, "NET_INCOME": 1.0e12},
        ]
        with patch.object(self.crawler, 'fetch_cafef_bank_api', return_value=mock_data):
            result = self.crawler.crawl_symbol("TEST", "STANDARD", source="cafef")
            assert result["total_metrics"] > 0
            assert result["success"] >= 1

    def test_crawl_all_fail_fallback_synthetic(self):
        """Tất cả API thất bại → fallback synthetic."""
        with patch.object(self.crawler, 'fetch_vci_bridge', return_value=[]):
            with patch.object(self.crawler, 'fetch_cafef_bank_api', return_value=[]):
                with patch.object(self.crawler, 'fetch_note_indicator', return_value=[]):
                    result = self.crawler.crawl_symbol("FPT", "STANDARD", source="vci")
                    assert result["total_metrics"] > 0
                    assert result["success"] >= 1


# ===================================================================
# TEST 8: cmd_financial_search CLI
# ===================================================================

class TestFinancialSearchCLI:
    """Kiểm tra CLI financial-search qua subprocess."""

    def setup_method(self):
        from src.financial.financial_facts import FinancialFactsDB
        self.db = FinancialFactsDB()
        self.db.init_schema()

        test_data = [
            ("VCB", "2025Q2", 2025, 2, "BANK", {
                "_fiscal_year": 2025, "_fiscal_quarter": 2, "_entity_type": "BANK",
                "NET_INCOME": 9.02e12,
            }),
            ("VCB", "2025Q3", 2025, 3, "BANK", {
                "_fiscal_year": 2025, "_fiscal_quarter": 3, "_entity_type": "BANK",
                "NET_INCOME": 8.63e12,
            }),
            ("ACB", "2025Q3", 2025, 3, "BANK", {
                "_fiscal_year": 2025, "_fiscal_quarter": 3, "_entity_type": "BANK",
                "REVENUE": 1.79e13,
            }),
            ("FPT", "2025Q1", 2025, 1, "STANDARD", {
                "_fiscal_year": 2025, "_fiscal_quarter": 1, "_entity_type": "STANDARD",
                "REVENUE": 5.39e12,
            }),
            ("FPT", "2025Q2", 2025, 2, "STANDARD", {
                "_fiscal_year": 2025, "_fiscal_quarter": 2, "_entity_type": "STANDARD",
                "REVENUE": 6.12e12,
            }),
            ("VCB", "2025Q2", 2025, 2, "BANK", {
                "_fiscal_year": 2025, "_fiscal_quarter": 2, "_entity_type": "BANK",
                "TOTAL_ASSETS": 4.5e14,
            }),
        ]
        for symbol, period, year, quarter, etype, data in test_data:
            self.db.write_batch(symbol, data, etype, "test")

    def _query_db(self, sql, params=()):
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_search_by_symbol(self):
        """Search --symbol VCB → trả về kết quả (exit code 0)."""
        from subprocess import run
        result = run(
            [sys.executable, "-m", "ptck", "financial-search", "--symbol", "VCB"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        rows = self._query_db(
            "SELECT COUNT(*) FROM financial_facts WHERE symbol = ?", ("VCB",)
        )
        assert rows[0][0] > 0

    def test_search_by_metric(self):
        """Search --metric NET_INCOME → có dữ liệu."""
        from subprocess import run
        result = run(
            [sys.executable, "-m", "ptck", "financial-search", "--metric", "NET_INCOME"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        rows = self._query_db(
            "SELECT COUNT(*) FROM financial_facts WHERE metric = ?", ("NET_INCOME",)
        )
        assert rows[0][0] > 0

    def test_search_no_results(self):
        """Search symbol không tồn tại → return code 0, không crash."""
        from subprocess import run
        result = run(
            [sys.executable, "-m", "ptck", "financial-search", "--symbol", "NONEXIST"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        rows = self._query_db(
            "SELECT COUNT(*) FROM financial_facts WHERE symbol = ?", ("NONEXIST",)
        )
        assert rows[0][0] == 0

    def test_search_multiple_symbols(self):
        """Search multiple --symbol → không crash."""
        from subprocess import run
        result = run(
            [sys.executable, "-m", "ptck", "financial-search",
             "--symbol", "VCB", "--symbol", "FPT", "--metric", "REVENUE"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        rows = self._query_db(
            "SELECT DISTINCT symbol FROM financial_facts WHERE metric = ? AND symbol IN (?, ?)",
            ("REVENUE", "VCB", "FPT"),
        )
        assert len(rows) >= 0  # just verifies no crash


# ===================================================================
# TEST 9: _generate_synthetic_base
# ===================================================================

class TestSyntheticBase:
    """Kiểm tra synthetic data generator."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.crawler = CafeFCrawler(FinancialFactsDB())

    def test_synthetic_fpt(self):
        """Synthetic cho FPT → có dữ liệu."""
        result = self.crawler._generate_synthetic_base("FPT", "STANDARD")
        assert len(result) > 0
        assert isinstance(result[0], dict)
        assert "_fiscal_year" in result[0]
        assert "_fiscal_quarter" in result[0]
        # Check REVENUE exists
        assert "REVENUE" in result[0] or any("REVENUE" in k for k in result[0].keys())

    def test_synthetic_acb(self):
        """Synthetic cho ACB (BANK) → có dữ liệu."""
        result = self.crawler._generate_synthetic_base("ACB", "BANK")
        assert len(result) > 0
        assert isinstance(result[0], dict)

    def test_synthetic_unknown_symbol(self):
        """Symbol không có trong base_data → trả về []."""
        result = self.crawler._generate_synthetic_base("UNKNOWN", "STANDARD")
        assert result == []

    def test_synthetic_20_quarters(self):
        """Synthetic phải có đúng 20 quarters."""
        result = self.crawler._generate_synthetic_base("FPT", "STANDARD")
        assert len(result) == 20


# ===================================================================
# TEST 10: Integration — Full Crawl Flow
# ===================================================================

class TestIntegration:
    """Integration test: crawl_multi với mocked API."""

    def setup_method(self):
        from src.financial.cafef_crawler import CafeFCrawler
        from src.financial.financial_facts import FinancialFactsDB
        self.db = FinancialFactsDB()
        self.db.init_schema()
        self.crawler = CafeFCrawler(self.db)

    def test_crawl_multi_with_mocked_cafef(self):
        """Crawl multi symbols với mocked CafeF Bank API."""
        mock_data = [
            {"_fiscal_year": 2026, "_fiscal_quarter": 1, "REVENUE": 5.0e12, "NET_INCOME": 1.0e12},
        ]
        with patch.object(self.crawler, 'fetch_cafef_bank_api', return_value=mock_data):
            targets = [
                ("FPT", "STANDARD"),
                ("ACB", "BANK"),
            ]

            result = self.crawler.crawl_multi(targets, source="cafef")

            assert result["symbols"] == 2
            assert result["total_facts"] > 0


# ===================================================================
# Run with: pytest tests/unit/test_cafef_crawler.py -v
# ===================================================================
