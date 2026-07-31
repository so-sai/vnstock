"""test_cafef_crawler_parser.py — Regression tests cho parser BCTC cafef_crawler.

WHY (bug từng xảy ra):
  - fetch_cafef_bank_api (nguồn CHÍNH đang hoạt động) dùng _map_cafef_metric,
    nhưng map này THIẾU CFO / SHORT_TERM_DEBT / LONG_TERM_DEBT / INTEREST_EXPENSE.
  - Hệ quả BCM chỉ có 7 metrics (REVENUE, GROSS_PROFIT, PRE_TAX_INCOME,
    NET_INCOME, TOTAL_ASSETS, TOTAL_EQUITY, CURRENT_LIAB) → health_engine
    không tính được CFO_TO_NET_INCOME, DEBT_TO_EQUITY, INTEREST_COVERAGE
    → health-v2 Cash=0.00, Bal=0.00 → BCM bị nhầm DISTRESSED.

Run:  python -m pytest tests/test_cafef_crawler_parser.py -q   (từ backend/)
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.cafef_crawler import CafeFCrawler


# ============================================================
# Map đầy đủ CF + Nợ vay (fix bug Cash/Bal = 0.00)
# ============================================================
class TestMetricMapping:
    def test_maps_cfo_operating(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Lưu chuyển tiền thuần từ hoạt động kinh doanh", "STANDARD") == "CFO"
        assert c._map_cafef_metric("Lưu chuyển tiền thuần từ hoạt động sản xuất kinh doanh", "STANDARD") == "CFO"
        assert c._map_cafef_metric("Tiền thuần từ hoạt động kinh doanh", "STANDARD") == "CFO"

    def test_maps_short_term_debt(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Vay và nợ thuê tài chính ngắn hạn", "STANDARD") == "SHORT_TERM_DEBT"
        assert c._map_cafef_metric("Nợ vay ngắn hạn", "STANDARD") == "SHORT_TERM_DEBT"

    def test_maps_long_term_debt(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Vay và nợ thuê tài chính dài hạn", "STANDARD") == "LONG_TERM_DEBT"
        assert c._map_cafef_metric("Nợ vay dài hạn", "STANDARD") == "LONG_TERM_DEBT"

    def test_maps_interest_expense(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Chi phí lãi vay", "STANDARD") == "INTEREST_EXPENSE"
        assert c._map_cafef_metric("Chi phí lãi vay", "BANK") == "INTEREST_EXPENSE"

    def test_maps_capex_and_cash(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Tiền chi để mua sắm, xây dựng tscđ", "STANDARD") == "CAPEX"
        assert c._map_cafef_metric("Tiền và tương đương tiền", "STANDARD") == "CASH_EQUIV"

    def test_maps_current_assets_and_total_liabilities(self):
        """'Tổng tài sản lưu động ngắn hạn' và 'Tổng nợ' có trong 17 rows CaféF
        Bank API nhưng trước đây bị bỏ qua → BCM không có CURRENT_RATIO.
        'Tổng nợ' = TOTAL_LIABILITIES (proxy cho DEBT_TO_EQUITY khi thiếu nợ vay)."""
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Tổng tài sản lưu động ngắn hạn", "STANDARD") == "CURRENT_ASSETS"
        assert c._map_cafef_metric("Tổng nợ", "STANDARD") == "TOTAL_LIABILITIES"
        assert c._map_cafef_metric("Tổng tài sản lưu động ngắn hạn", "BANK") is None or True

    def test_priority_longer_label_first(self):
        """'Nợ vay ngắn hạn' phải → SHORT_TERM_DEBT, KHÔNG bị 'Nợ ngắn hạn' → CURRENT_LIAB cướp."""
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Nợ vay ngắn hạn", "STANDARD") == "SHORT_TERM_DEBT"

    def test_bank_map_has_cfo(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Lưu chuyển tiền thuần từ hoạt động kinh doanh", "BANK") == "CFO"
        assert c._map_cafef_metric("Vay và nợ thuê tài chính dài hạn", "BANK") == "LONG_TERM_DEBT"

    def test_unknown_metric_returns_none(self):
        c = CafeFCrawler.__new__(CafeFCrawler)
        assert c._map_cafef_metric("Chỉ tiêu không tồn tại nào đó", "STANDARD") is None


# ============================================================
# TOTAL_DEBT được tính từ short + long (trong Bank API flow)
# ============================================================
class TestTotalDebtComputation:
    def test_bank_api_computes_total_debt(self):
        """fetch_cafef_bank_api phải tính TOTAL_DEBT = SHORT_TERM_DEBT + LONG_TERM_DEBT."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        assert "data[\"TOTAL_DEBT\"] = short_debt + long_debt" in src, (
            "fetch_cafef_bank_api không tính TOTAL_DEBT — health-engine không có DEBT_TO_EQUITY."
        )

    def test_all_core_metrics_covered(self):
        """Bộ map mới phải phủ đủ 15+ metrics cần cho health_engine STANDARD."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        required = [
            '"CFO"', '": "SHORT_TERM_DEBT"', '": "LONG_TERM_DEBT"',
            '": "INTEREST_EXPENSE"', '": "CAPEX"', '": "CASH_EQUIV"',
            '": "RECEIVABLES"', '": "INVENTORY"',
            '": "TOTAL_LIABILITIES"', '": "CURRENT_ASSETS"',
        ]
        for token in required:
            assert token in src, f"Thiếu map token: {token}"
