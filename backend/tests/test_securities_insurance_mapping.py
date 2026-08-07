"""test_securities_insurance_mapping.py — khóa mapper item_id VCI cho SECURITIES/INSURANCE.

WHY (Zero-Hallucination, 2026-08-07):
  - Trước đây VNSTOCK_ITEM_ID_MAP dùng key SUY ĐOÁN
    (financial_assets_at_fair_value_through_profit_or_loss, margin_lending,
    net_written_premium...) → không khớp item_id THỰC TẾ VCI trả → SSI/BVH
    chỉ nạp được 20 metric standard, thiếu FVTPL/AFS/MARGIN_LOANS/NET_PREMIUM.
  - Probe live VCI (wide-format item_id) đã xác định key thật:
      SSI BS: financial_assets_at_fair_value_through_profit_or_loss_fvtpl,
              available_for_sale_financial_assets_afs, loans
      SSI IS: income_from_financial_assets_recognized_through_profit_loss_fvtpl,
              income_from_loans_and_receivables, revenue_in_brokerage_services
      BVH IS: gross_written_premium, claim_and_maturity_payment_expenses
      BVH BS: unearned_premium_reserve, technical_reserve, claim_reserve
  - Tests này chốt mapper bằng DataFrame giả lập (không đụng network), để
    regress ngay nếu ai đó đổi key về dạng suy đoán cũ.

Run:  python -m pytest backend/tests/test_securities_insurance_mapping.py -q
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.financial.financial_facts import VNSTOCK_ITEM_ID_MAP, VnstockCrawler


def make_wide(rows, periods=("2026-Q1",)):
    """Build a wide-format statement DataFrame like vnstock 4.0.5 (item_id rows).

    Mỗi row: (item_id, label, value) — value được lặp cho tất cả period columns
    (giống vnstock: cùng item_id ở mọi kỳ). period labels mặc định 1 kỳ.
    """
    data = {"item_id": [r[0] for r in rows], "item_en": [r[1] for r in rows]}
    for p in periods:
        data[p] = [r[2] for r in rows]
    return pd.DataFrame(data)


class TestVerifiedItemIdKeys:
    """Key mapper phải là item_id THỰC TẾ VCI trả (đã verify live 2026-08-07)."""

    def test_securities_bs_fvtpl(self):
        assert VNSTOCK_ITEM_ID_MAP["financial_assets_at_fair_value_through_profit_or_loss_fvtpl"] == "FVTPL"

    def test_securities_bs_afs(self):
        assert VNSTOCK_ITEM_ID_MAP["available_for_sale_financial_assets_afs"] == "AFS"

    def test_securities_bs_margin_loans(self):
        assert VNSTOCK_ITEM_ID_MAP["loans"] == "MARGIN_LOANS"

    def test_securities_is_fvtpl_gain(self):
        assert VNSTOCK_ITEM_ID_MAP["income_from_financial_assets_recognized_through_profit_loss_fvtpl"] == "FVTPL_GAIN"

    def test_securities_is_margin_interest(self):
        assert VNSTOCK_ITEM_ID_MAP["income_from_loans_and_receivables"] == "MARGIN_INTEREST"

    def test_securities_is_operating_revenue(self):
        assert VNSTOCK_ITEM_ID_MAP["revenue_in_brokerage_services"] == "OPERATING_REVENUE"

    def test_insurance_is_net_premium(self):
        assert VNSTOCK_ITEM_ID_MAP["gross_written_premium"] == "NET_PREMIUM"
        assert VNSTOCK_ITEM_ID_MAP["revenue_from_insurance_premium"] == "NET_PREMIUM"

    def test_insurance_is_net_claims(self):
        assert VNSTOCK_ITEM_ID_MAP["claim_and_maturity_payment_expenses"] == "NET_CLAIMS"
        assert VNSTOCK_ITEM_ID_MAP["total_insurance_claim_settlement_expenses"] == "NET_CLAIMS"

    def test_insurance_bs_technical_reserves(self):
        assert VNSTOCK_ITEM_ID_MAP["unearned_premium_reserve"] == "TECHNICAL_RESERVES"
        assert VNSTOCK_ITEM_ID_MAP["technical_reserve"] == "TECHNICAL_RESERVES"
        assert VNSTOCK_ITEM_ID_MAP["claim_reserve"] == "TECHNICAL_RESERVES"

    def test_no_guessed_keys_from_old_draft(self):
        """Các key SUY ĐOÁN từ bản nháp cũ KHÔNG được tồn tại (gây false-positive)."""
        for guessed in (
            "net_written_premium",
            "net_insurance_claims",
            "margin_lending",
            "financial_assets_at_fair_value_through_profit_or_loss",
        ):
            assert guessed not in VNSTOCK_ITEM_ID_MAP, f"key suy đoán {guessed} vẫn tồn tại"


class TestParseWideSecurities:
    def _crawler(self):
        crawler = VnstockCrawler()
        crawler.db = Mock()
        crawler.db.get_entity_type.return_value = "SECURITIES"
        return crawler

    def test_parse_ssi_balance_sheet_captures_assets(self):
        crawler = self._crawler()
        bs = make_wide(
            [
                ("financial_assets_at_fair_value_through_profit_or_loss_fvtpl", "FVTPL", 1_000),
                ("available_for_sale_financial_assets_afs", "AFS", 2_000),
                ("loans", "Margin loans", 3_000),
                ("total_assets", "Total assets", 9_000),
            ]
        )
        out = crawler._parse_statements("SSI", {"BS": bs, "IS": None, "CF": None})
        assert out, "không parse được period nào"
        p = out[0]
        assert p["FVTPL"] == 1_000
        assert p["AFS"] == 2_000
        assert p["MARGIN_LOANS"] == 3_000
        assert p["TOTAL_ASSETS"] == 9_000

    def test_parse_ssi_income_statement_captures_gains(self):
        crawler = self._crawler()
        is_ = make_wide(
            [
                ("income_from_financial_assets_recognized_through_profit_loss_fvtpl", "FVTPL income", 100),
                ("income_from_loans_and_receivables", "Margin interest", 50),
                ("revenue_in_brokerage_services", "Brokerage revenue", 400),
                ("net_profit_loss_after_tax", "Net profit", 300),
            ]
        )
        out = crawler._parse_statements("SSI", {"BS": None, "IS": is_, "CF": None})
        p = out[0]
        assert p["FVTPL_GAIN"] == 100
        assert p["MARGIN_INTEREST"] == 50
        assert p["OPERATING_REVENUE"] == 400
        assert p["NET_INCOME"] == 300


class TestParseWideInsurance:
    def _crawler(self):
        crawler = VnstockCrawler()
        crawler.db = Mock()
        crawler.db.get_entity_type.return_value = "INSURANCE"
        return crawler

    def test_parse_bvh_income_statement_captures_premium_claims(self):
        crawler = self._crawler()
        is_ = make_wide(
            [
                ("gross_written_premium", "Gross written premium", 6_000),
                ("claim_and_maturity_payment_expenses", "Claims paid", 3_500),
                ("profit_after_tax", "Profit after tax", 800),
            ]
        )
        out = crawler._parse_statements("BVH", {"BS": None, "IS": is_, "CF": None})
        p = out[0]
        assert p["NET_PREMIUM"] == 6_000
        assert p["NET_CLAIMS"] == 3_500
        assert p["NET_INCOME"] == 800

    def test_parse_bvh_balance_sheet_captures_reserves(self):
        crawler = self._crawler()
        bs = make_wide(
            [
                ("unearned_premium_reserve", "UPR", 1_000),
                ("technical_reserve", "Technical reserve", 2_000),
                ("claim_reserve", "Claim reserve", 500),
                ("total_assets", "Total assets", 9_000),
            ]
        )
        out = crawler._parse_statements("BVH", {"BS": bs, "IS": None, "CF": None})
        p = out[0]
        # nhiều reserve key map về cùng TECHNICAL_RESERVES → dòng sau ghi đè
        assert p["TECHNICAL_RESERVES"] == 500
        assert p["TOTAL_ASSETS"] == 9_000

    def test_parse_empty_dataframe_no_crash(self):
        crawler = self._crawler()
        empty = pd.DataFrame(columns=["item_id", "item_en"])
        out = crawler._parse_statements("BVH", {"BS": empty, "IS": None, "CF": None})
        assert out == []
