"""test_cafef_api_bridge.py — Regression tests cho VNDirect/TCBS REST JSON bridge.

WHY (nhu cầu): CafeF Bank API (BHoSoCongTy) chỉ trả BCTC Tóm tắt 17 rows →
BCM thiếu CFO / Nợ vay dài hạn / Chi phí lãi vay → Cash/DEBT = NO DATA.
VNDirect Fininfo + TCBS FinAPI trả JSON đủ 4 bảng. Các pure parser
(_parse_vndirect_json, _parse_tcbs_rows) được test deterministic với mock JSON
(KHÔNG cần network — DNS của VNDirect/TCBS chưa mở trong môi trường dev).

Run:  python -m pytest tests/test_cafef_api_bridge.py -q   (từ backend/)
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.cafef_crawler import CafeFCrawler


# ============================================================
# VNDirect Fininfo JSON parser
# ============================================================
class TestVndirectParser:
    def test_parses_cfo_debt_interest(self):
        """Row VNDirect với CFO + nợ vay + chi phí lãi vay → đủ metric PTCK."""
        payload = [{
            "reportDate": "2025-06-30",
            "cashFlowFromOperatingActivities": 1_500_000_000,
            "shortTermBorrowings": 2_000_000_000,
            "longTermBorrowings": 3_000_000_000,
            "interestExpense": 80_000_000,
            "cashAndCashEquivalents": 900_000_000,
            "inventory": 1_200_000_000,
            "accountReceivables": 700_000_000,
            "totalCurrentAssets": 5_000_000_000,
            "totalCurrentLiabilities": 2_500_000_000,
            "totalLiabilities": 8_000_000_000,
            "totalAssets": 15_000_000_000,
            "totalEquity": 7_000_000_000,
            "totalRevenue": 4_000_000_000,
            "grossProfit": 1_600_000_000,
            "netProfit": 600_000_000,
            "capitalExpenditure": 300_000_000,
        }]
        periods = CafeFCrawler._parse_vndirect_json(payload, "STANDARD")
        assert len(periods) == 1
        p = periods[0]
        assert p["_fiscal_year"] == 2025
        assert p["_fiscal_quarter"] == 2
        assert p["CFO"] == 1_500_000_000
        assert p["SHORT_TERM_DEBT"] == 2_000_000_000
        assert p["LONG_TERM_DEBT"] == 3_000_000_000
        assert p["INTEREST_EXPENSE"] == 80_000_000
        assert p["TOTAL_DEBT"] == 5_000_000_000
        assert p["CAPEX"] == 300_000_000

    def test_handles_dict_payload_wrapped_in_data(self):
        """VNDirect có thể wrap rows trong {"data": [...]}."""
        payload = {"data": [{
            "reportDate": "2024-03-31",
            "cashFlowFromOperatingActivities": 1_000_000_000,
            "totalAssets": 10_000_000_000,
        }]}
        periods = CafeFCrawler._parse_vndirect_json(payload, "STANDARD")
        assert len(periods) == 1
        assert periods[0]["_fiscal_year"] == 2024
        assert periods[0]["_fiscal_quarter"] == 1

    def test_skips_zero_values(self):
        """Giá trị 0 bị bỏ qua — không ghi metric rỗng."""
        payload = [{
            "reportDate": "2025-09-30",
            "cashFlowFromOperatingActivities": 0,
            "totalAssets": 10_000_000_000,
        }]
        periods = CafeFCrawler._parse_vndirect_json(payload, "STANDARD")
        assert len(periods) == 1
        assert "CFO" not in periods[0]
        assert periods[0]["TOTAL_ASSETS"] == 10_000_000_000

    def test_skips_rows_without_period(self):
        """Row thiếu reportDate/year/quarter → bỏ qua."""
        payload = [{
            "cashFlowFromOperatingActivities": 1_000_000_000,
            "totalAssets": 10_000_000_000,
        }]
        assert CafeFCrawler._parse_vndirect_json(payload, "STANDARD") == []

    def test_empty_or_malformed_payload(self):
        assert CafeFCrawler._parse_vndirect_json(None, "STANDARD") == []
        assert CafeFCrawler._parse_vndirect_json([], "STANDARD") == []
        assert CafeFCrawler._parse_vndirect_json({"items": []}, "STANDARD") == []


# ============================================================
# TCBS FinAPI parser
# ============================================================
class TestTcbsParser:
    def test_parses_balance_sheet_rows(self):
        payload = {"data": [{
            "year": 2025, "quarter": 2,
            "cashAndCashEquivalents": 900_000_000,
            "shortTermBorrowings": 2_000_000_000,
            "longTermBorrowings": 3_000_000_000,
            "inventory": 1_200_000_000,
            "receivables": 700_000_000,
            "totalCurrentAssets": 5_000_000_000,
            "currentLiabilities": 2_500_000_000,
            "totalLiabilities": 8_000_000_000,
            "totalAssets": 15_000_000_000,
            "shareHolderEquity": 7_000_000_000,
        }]}
        rows = CafeFCrawler._parse_tcbs_rows(payload, "BS")
        assert len(rows) == 1
        r = rows[0]
        assert r["_fiscal_year"] == 2025
        assert r["_fiscal_quarter"] == 2
        assert r["CASH_EQUIV"] == 900_000_000
        assert r["SHORT_TERM_DEBT"] == 2_000_000_000
        assert r["LONG_TERM_DEBT"] == 3_000_000_000
        assert r["TOTAL_EQUITY"] == 7_000_000_000

    def test_parses_income_statement_rows(self):
        payload = {"data": [{
            "year": 2025, "quarter": 2,
            "revenue": 4_000_000_000,
            "grossProfit": 1_600_000_000,
            "profitAfterTax": 600_000_000,
            "interestExpense": 80_000_000,
        }]}
        rows = CafeFCrawler._parse_tcbs_rows(payload, "IS")
        assert len(rows) == 1
        r = rows[0]
        assert r["REVENUE"] == 4_000_000_000
        assert r["GROSS_PROFIT"] == 1_600_000_000
        assert r["NET_INCOME"] == 600_000_000
        assert r["INTEREST_EXPENSE"] == 80_000_000

    def test_parses_cash_flow_rows(self):
        payload = {"data": [{
            "year": 2025, "quarter": 2,
            "cashFlowFromOperatingActivities": 1_500_000_000,
            "cashFlowFromInvestingActivities": -400_000_000,
            "cashFlowFromFinancingActivities": -200_000_000,
            "capitalExpenditure": 300_000_000,
        }]}
        rows = CafeFCrawler._parse_tcbs_rows(payload, "CF")
        assert len(rows) == 1
        r = rows[0]
        assert r["CFO"] == 1_500_000_000
        assert r["CFI"] == -400_000_000
        assert r["CFF"] == -200_000_000
        assert r["CAPEX"] == 300_000_000

    def test_skips_rows_without_period(self):
        payload = [{"cashAndCashEquivalents": 1_000_000_000}]
        assert CafeFCrawler._parse_tcbs_rows(payload, "BS") == []

    def test_empty_or_malformed_payload(self):
        assert CafeFCrawler._parse_tcbs_rows(None, "BS") == []
        assert CafeFCrawler._parse_tcbs_rows({"items": []}, "CF") == []
        assert CafeFCrawler._parse_tcbs_rows("not json", "IS") == []


# ============================================================
# URL_MAP coverage + fallback chain
# ============================================================
class TestBridgeIntegration:
    def test_url_map_has_new_bridges(self):
        from src.financial.cafef_crawler import URL_MAP
        assert "VNDIRECT_FININFO" in URL_MAP
        assert "TCBS_FINAPI" in URL_MAP
        assert URL_MAP["VNDIRECT_FININFO"]["method"] == "GET"
        assert URL_MAP["TCBS_FINAPI"]["method"] == "GET"

    def test_crawl_symbol_api_source_uses_vndirect_first(self):
        """source='api' phải gọi fetch_vndirect_api trước, rồi TCBS, rồi CafeF."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        assert "source == \"api\"" in src
        assert "self.fetch_vndirect_api(symbol)" in src
        assert "self.fetch_tcbs_api(symbol)" in src
        # Fallback chain phải giữ CafeF Bank API làm nguồn cuối
        assert "fallback CafeF Bank API" in src

    def test_vci_chain_includes_json_bridges(self):
        """source='vci' phải thử VNDirect/TCBS trước CafeF Bank API."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        vci_block = src.split('if source == "vci":')[1].split('elif source == "cafef":')[0]
        idx_vnd = vci_block.index("self.fetch_vndirect_api(symbol)")
        idx_tcbs = vci_block.index("self.fetch_tcbs_api(symbol)")
        idx_cafef = vci_block.index("self.fetch_cafef_bank_api(symbol)")
        assert idx_vnd < idx_tcbs < idx_cafef, "Thứ tự fallback sai: phải VNDirect→TCBS→CafeF"

    def test_tcbs_merges_sections_by_period(self):
        """fetch_tcbs_api phải merge BS+IS+CF cùng period thành 1 dict."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        assert "merged_by_period.setdefault(key, {}).update(row)" in src
        assert '"BALANCE_SHEET"' in src and '"INCOME_STATEMENT"' in src and '"CASH_FLOW"' in src
