"""test_cross_validate.py - TDD for the @cross_validate decorator."""

import pandas as pd

from src.providers.base import FinancialProvider
from src.providers.cross_validate import (
    FLAG_DISCREPANCY,
    FLAG_NO_SECONDARY,
    FLAG_VERIFIED,
    compare_statements,
)
from src.providers.manager import ProviderManager


class VCIProvider(FinancialProvider):
    """Emits VCI-schema statements (net_sales, cost_of_sales, ...)."""

    name = "vci"
    source = "VCI"

    def __init__(self, shift=0.0, empty_bs=False):
        self.shift = shift
        self.empty_bs = empty_bs

    def is_available(self):
        return True

    def _is_df(self, rows):
        return pd.DataFrame(rows)

    def income_statement(self, symbol, **kwargs):
        base = 1000.0 + self.shift
        return pd.DataFrame(
            {
                "item": ["Doanh thu thuần", "Giá vốn hàng bán", "Lợi nhuận sau thuế"],
                "item_en": ["Net sales", "COGS", "Net profit"],
                "item_id": ["net_sales", "cost_of_sales", "net_profit_loss_after_tax"],
                "2025-Q1": [base, base * 0.6, base * 0.15],
                "2025-Q2": [base * 1.1, base * 0.65, base * 0.18],
            }
        )

    def balance_sheet(self, symbol, **kwargs):
        if self.empty_bs:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "item": ["Tổng tài sản", "Tổng nợ", "Vốn chủ"],
                "item_en": ["Total assets", "Total liab", "Equity"],
                "item_id": ["total_assets", "liabilities", "owners_equity"],
                "2025-Q1": [5000.0, 3000.0, 2000.0],
                "2025-Q2": [5100.0, 3050.0, 2050.0],
            }
        )

    def cashflow(self, symbol, **kwargs):
        base = 200.0 + self.shift
        return pd.DataFrame(
            {
                "item": ["CF Ops", "CF Inv", "CF Fin"],
                "item_en": ["CFO", "CFI", "CFF"],
                "item_id": [
                    "net_cash_inflows_outflows_from_operating_activities",
                    "net_cash_inflows_outflows_from_investing_activities",
                    "net_cash_inflows_outflows_from_financing_activities",
                ],
                "2025-Q1": [base, -50.0, -100.0],
                "2025-Q2": [base * 1.2, -60.0, -90.0],
            }
        )

    def history(self, symbol, start=None, end=None, **kwargs):
        return pd.DataFrame({"symbol": [symbol], "close": [10.0]})


class KBSProvider(FinancialProvider):
    """Emits KBS-schema statements (revenue, net_profit, ...)."""

    name = "kbs"
    source = "KBS"

    def __init__(self, shift=0.0, empty_bs=False):
        self.shift = shift
        self.empty_bs = empty_bs

    def is_available(self):
        return True

    def income_statement(self, symbol, **kwargs):
        base = 1000.0 + self.shift
        return pd.DataFrame(
            {
                "item": ["Doanh thu", "Giá vốn", "LNST"],
                "item_en": ["Revenue", "COGS", "Net profit"],
                "item_id": ["revenue", "cost_of_goods_sold", "net_profit"],
                "2025-Q1": [base, base * 0.6, base * 0.15],
                "2025-Q2": [base * 1.1, base * 0.65, base * 0.18],
            }
        )

    def balance_sheet(self, symbol, **kwargs):
        if self.empty_bs:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "item": ["Tổng tài sản"],
                "item_en": ["Total assets"],
                "item_id": ["total_assets"],
                "2025-Q1": [5000.0],
                "2025-Q2": [5100.0],
            }
        )

    def cashflow(self, symbol, **kwargs):
        base = 200.0 + self.shift
        return pd.DataFrame(
            {
                "item": ["CF HĐKD", "CF ĐTư", "CF TC"],
                "item_en": ["CFO", "CFI", "CFF"],
                "item_id": ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow"],
                "2025-Q1": [base, -50.0, -100.0],
                "2025-Q2": [base * 1.2, -60.0, -90.0],
            }
        )

    def history(self, symbol, start=None, end=None, **kwargs):
        return pd.DataFrame({"symbol": [symbol], "close": [10.0]})


def _manager(vci_shift=0.0, kbs_shift=0.0, kbs_empty_bs=False):
    vci = VCIProvider(shift=vci_shift)
    mgr = ProviderManager([vci])
    mgr._secondary_provider = lambda source="KBS": KBSProvider(shift=kbs_shift, empty_bs=kbs_empty_bs)
    return mgr


# ── compare_statements (pure) ───────────────────────────────────────────

def test_compare_identical_sources_verified():
    vci = VCIProvider()
    kbs = KBSProvider()
    report = compare_statements(
        vci.income_statement("PNJ"),
        kbs.income_statement("PNJ"),
        source="VCI",
        secondary_source="KBS",
        method="income_statement",
        threshold=0.05,
    )
    assert report["status"] == FLAG_VERIFIED
    assert report["max_error"] == 0.0
    assert report["metrics"] == {"NET_REVENUE": 0.0, "COGS": 0.0, "NET_PROFIT": 0.0}


def test_compare_discrepancy_flagged():
    # KBS reports revenue 20% off → crosses the 5% threshold.
    vci = VCIProvider()
    kbs = KBSProvider(shift=200.0)  # net_sales 1000 vs revenue 1200
    report = compare_statements(
        vci.income_statement("PNJ"),
        kbs.income_statement("PNJ"),
        source="VCI",
        secondary_source="KBS",
        method="income_statement",
        threshold=0.05,
    )
    assert report["status"] == FLAG_DISCREPANCY
    assert report["max_error"] > 0.05


def test_compare_secondary_empty_no_flag():
    vci = VCIProvider()
    kbs = KBSProvider(empty_bs=True)
    report = compare_statements(
        vci.balance_sheet("PNJ"),
        kbs.balance_sheet("PNJ"),
        source="VCI",
        secondary_source="KBS",
        method="balance_sheet",
        threshold=0.05,
    )
    assert report["status"] == FLAG_NO_SECONDARY


def test_compare_cashflow_cfo():
    vci = VCIProvider()
    kbs = KBSProvider(shift=0.0)
    report = compare_statements(
        vci.cashflow("PNJ"),
        kbs.cashflow("PNJ"),
        source="VCI",
        secondary_source="KBS",
        method="cashflow",
        threshold=0.05,
    )
    assert report["status"] == FLAG_VERIFIED
    assert "CFO" in report["metrics"]


# ── decorator wiring through ProviderManager ───────────────────────────

def test_manager_income_statement_records_cross_validation():
    mgr = _manager()
    df = mgr.income_statement("PNJ")
    assert df is not None and not df.empty  # primary data unchanged
    report = mgr.cross_validation_report().get("PNJ:income_statement")
    assert report is not None
    assert report["status"] == FLAG_VERIFIED


def test_manager_flagged_discrepancy_recorded():
    mgr = _manager(kbs_shift=200.0)
    mgr.income_statement("PNJ")
    dis = mgr.discrepancies()
    assert "PNJ:income_statement" in dis
    assert dis["PNJ:income_statement"]["status"] == FLAG_DISCREPANCY


def test_manager_balance_sheet_no_secondary_not_flagged():
    mgr = _manager(kbs_empty_bs=True)
    df = mgr.balance_sheet("PNJ")
    assert df is not None and not df.empty
    report = mgr.cross_validation_report().get("PNJ:balance_sheet")
    assert report is not None
    assert report["status"] == FLAG_NO_SECONDARY


def test_on_demand_cross_validate_method():
    mgr = _manager()
    report = mgr.cross_validate("PNJ", method="income_statement")
    assert report["status"] in (FLAG_VERIFIED, FLAG_DISCREPANCY, FLAG_NO_SECONDARY)
