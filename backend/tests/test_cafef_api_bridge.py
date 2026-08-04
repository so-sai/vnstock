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
        payload = [
            {
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
            }
        ]
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
        payload = {
            "data": [
                {
                    "reportDate": "2024-03-31",
                    "cashFlowFromOperatingActivities": 1_000_000_000,
                    "totalAssets": 10_000_000_000,
                }
            ]
        }
        periods = CafeFCrawler._parse_vndirect_json(payload, "STANDARD")
        assert len(periods) == 1
        assert periods[0]["_fiscal_year"] == 2024
        assert periods[0]["_fiscal_quarter"] == 1

    def test_skips_zero_values(self):
        """Giá trị 0 bị bỏ qua — không ghi metric rỗng."""
        payload = [
            {
                "reportDate": "2025-09-30",
                "cashFlowFromOperatingActivities": 0,
                "totalAssets": 10_000_000_000,
            }
        ]
        periods = CafeFCrawler._parse_vndirect_json(payload, "STANDARD")
        assert len(periods) == 1
        assert "CFO" not in periods[0]
        assert periods[0]["TOTAL_ASSETS"] == 10_000_000_000

    def test_skips_rows_without_period(self):
        """Row thiếu reportDate/year/quarter → bỏ qua."""
        payload = [
            {
                "cashFlowFromOperatingActivities": 1_000_000_000,
                "totalAssets": 10_000_000_000,
            }
        ]
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
        payload = {
            "data": [
                {
                    "year": 2025,
                    "quarter": 2,
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
                }
            ]
        }
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
        payload = {
            "data": [
                {
                    "year": 2025,
                    "quarter": 2,
                    "revenue": 4_000_000_000,
                    "grossProfit": 1_600_000_000,
                    "profitAfterTax": 600_000_000,
                    "interestExpense": 80_000_000,
                }
            ]
        }
        rows = CafeFCrawler._parse_tcbs_rows(payload, "IS")
        assert len(rows) == 1
        r = rows[0]
        assert r["REVENUE"] == 4_000_000_000
        assert r["GROSS_PROFIT"] == 1_600_000_000
        assert r["NET_INCOME"] == 600_000_000
        assert r["INTEREST_EXPENSE"] == 80_000_000

    def test_parses_cash_flow_rows(self):
        payload = {
            "data": [
                {
                    "year": 2025,
                    "quarter": 2,
                    "cashFlowFromOperatingActivities": 1_500_000_000,
                    "cashFlowFromInvestingActivities": -400_000_000,
                    "cashFlowFromFinancingActivities": -200_000_000,
                    "capitalExpenditure": 300_000_000,
                }
            ]
        }
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
        assert 'source == "api"' in src
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


# ============================================================
# Zero-Hallucination Policy (Sắc lệnh 2026-08-04)
# ============================================================
class TestZeroHallucinationLock:
    def test_synthetic_base_is_permanently_locked(self):
        """_generate_synthetic_base phải trả [] — CẤM bịa dữ liệu tài chính.

        WHY (Sắc lệnh Zero-Hallucination 2026-08-04): dữ liệu vẽ (debt_2026=25e12,
        rev_2026=9.85e12...) từng trá hình nguồn thật trong DB và dẫn tới quyết định
        giải ngân dựa trên ảo giác. Khi mọi nguồn thật chết, hệ thống trả rỗng để
        DataIntegrityAuditor ghi SEVERE_GAP → Governor ép DŨNG NGOẠI (100% Cash).
        """
        r = CafeFCrawler._generate_synthetic_base("FPT", "STANDARD")
        assert r == [], "synthetic base phải trả [] — Zero-Hallucination bị phá vỡ"

    def test_synthetic_base_locked_even_for_known_symbol(self):
        """Kể cả symbol có base_data (FPT/HDB/VCB...) cũng phải trả [] — không ngoại lệ."""
        for sym, ent in [("FPT", "STANDARD"), ("HDB", "BANK"), ("VCB", "BANK")]:
            assert CafeFCrawler._generate_synthetic_base(sym, ent) == [], sym

    def test_crawl_symbol_never_calls_live_synthetic(self):
        """Crawl với mọi nguồn rỗng → không được sinh synthetic, phải báo NO_DATA."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        # Hàm vẫn được gọi nhưng luôn trả [] (đã khóa) — không còn base_data nội suy
        fn = src.split("def _generate_synthetic_base")[1]
        assert "ZERO-HALLUCINATION" in src
        assert "return []" in fn, "hàm _generate_synthetic_base phải trả []"

    def test_crawl_symbol_all_sources_empty_returns_no_data(self, monkeypatch):
        """TẤT CẢ nguồn thật đều rỗng → crawl_symbol bắt buộc báo NO_DATA, không bịa.

        WHY (Zero-Hallucination): đây là test HÀNH VI cho chính chuỗi fallback thực —
        gồm VCI→VNDirect→TCBS→CafeF→NoteIndicator→Vietstock→synthetic. Nếu mọi nguồn
        chết, use_synthetic=True, nhưng _generate_synthetic_base đã khóa trả [] →
        never no periods → total_quarters=0. Trước đây synthetic lấp 20 quý fake.
        """

        class FakeDB:
            def register_entity(self, symbol, entity_type):
                pass

            def get_entity_type(self, symbol):
                return "STANDARD"

            def write_batch(self, *a, **k):
                raise AssertionError("không được ghi dữ liệu")

        c = CafeFCrawler(db=FakeDB(), delay=0)
        # Stub toàn bộ các nguồn → rỗng (giả lập mọi nguồn chết đồng loạt)
        for m in [
            "fetch_vci_bridge",
            "fetch_vndirect_api",
            "fetch_tcbs_api",
            "fetch_cafef_bank_api",
            "fetch_note_indicator",
            "fetch_vietstock_api",
            "fetch_cafef_cashflow",
            "fetch_quarter",
        ]:
            monkeypatch.setattr(c, m, lambda *a, **k: None if m != "fetch_quarter" else {})
        res = c.crawl_symbol("FAKE000", "STANDARD", source="vci")
        assert res["total_quarters"] == 0, f"phải NO_DATA, thay vì {res}"

    def test_write_batch_carries_provenance_source(self):
        """write_batch phải nhận source/is_synthetic để lưu dấu vết xuất xứ."""
        src = (BACKEND / "src" / "financial" / "financial_facts.py").read_text(encoding="utf-8-sig")
        assert "is_synthetic: int = 0" in src
        assert 'source: str = "vnstock"' in src

    def test_crawl_write_batch_passes_provenance(self):
        """crawl_symbol phải truyền source/is_synthetic vào write_batch (Provenance guard)."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        assert "data_source" in src
        assert "data_is_synthetic" in src
        assert "is_synthetic=data_is_synthetic" in src
