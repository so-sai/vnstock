"""test_health_entity_ratios.py — SECURITIES/INSURANCE ratio framework (additive).

WHY (kiến trúc 2 tầng entity, 2026-08-07):
  - Registry đã phân loại 4 nhóm: BANK/SECURITIES/INSURANCE/STANDARD.
  - SECURITIES (CTCK): KẾ THỪA standard ratios (doanh thu môi giới/lãi tự doanh
    vẫn là doanh thu chuẩn) + BỔ SUNG ratio đặc thù TT334 (margin/FVTPL/HTM/AFS).
  - INSURANCE (DNBH): KHÔNG có REVENUE chuẩn → bộ ratio riêng TT135
    (Loss/Expense/Combined Ratio, dự phòng nghiệp vụ) + ROE/ROA/CFO dùng chung.
  - Không bịa dữ liệu: ratio đặc thù chỉ xuất hiện khi nguồn cung cấp metric
    tương ứng (filter None cuối hàm).

Run:  python -m pytest backend/tests/test_health_entity_ratios.py -q
"""

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.financial.company_health_engine import RATIO_META, HealthEngine


def make_engine():
    """HealthEngine với facts_db stub — các hàm compute không cần DB thật."""
    return HealthEngine(facts_db=SimpleNamespace(db_path=":memory:"))


STANDARD_METRICS = {
    "REVENUE": 100_000,
    "GROSS_PROFIT": 40_000,
    "NET_INCOME": 12_000,
    "CFO": 15_000,
    "TOTAL_ASSETS": 200_000,
    "TOTAL_EQUITY": 80_000,
    "TOTAL_DEBT": 40_000,
    "CURRENT_ASSETS": 90_000,
    "CURRENT_LIAB": 50_000,
    "INVENTORY": 20_000,
    "CASH_EQUIV": 25_000,
    "RECEIVABLES": 30_000,
}

SECURITIES_METRICS = {
    "MARGIN_LOANS": 40_000,
    "FVTPL": 30_000,
    "HTM": 10_000,
    "AFS": 20_000,
}

INSURANCE_METRICS = {
    "NET_PREMIUM": 50_000,
    "NET_CLAIMS": 25_000,
    "OPERATING_EXPENSE": 8_000,
    "TECHNICAL_RESERVES": 60_000,
    "TOTAL_ASSETS": 300_000,
    "TOTAL_EQUITY": 100_000,
    "NET_INCOME": 10_000,
    "CFO": 12_000,
}


class TestMetaRegistry:
    def test_securities_ratios_registered(self):
        for name in ("MARGIN_TO_EQUITY", "MARGIN_TO_ASSETS", "FVTPL_TO_ASSETS", "HTM_TO_ASSETS", "AFS_TO_ASSETS"):
            assert name in RATIO_META
            assert "SECURITIES" in RATIO_META[name]["entity_types"]

    def test_insurance_ratios_registered(self):
        for name in ("LOSS_RATIO", "EXPENSE_RATIO", "COMBINED_RATIO", "TECH_RESERVE_TO_ASSETS"):
            assert name in RATIO_META
            assert "INSURANCE" in RATIO_META[name]["entity_types"]

    def test_margin_to_equity_inverted(self):
        """Đòn bẩy margin cao = rủi ro → inverted (thấp hơn = tốt hơn)."""
        assert RATIO_META["MARGIN_TO_EQUITY"].get("inverted") is True
        assert RATIO_META["MARGIN_TO_ASSETS"].get("inverted") is True

    def test_common_ratios_shared_across_entities(self):
        for name in ("ROE", "ROA", "CFO_TO_NET_INCOME"):
            ets = set(RATIO_META[name]["entity_types"])
            assert {"STANDARD", "BANK", "SECURITIES", "INSURANCE"} <= ets


class TestSecuritiesRatios:
    def test_additive_keeps_standard_ratios(self):
        """Additive: SECURITIES phải GIỮ standard ratios khi có dữ liệu doanh thu."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, **SECURITIES_METRICS)
        r = engine.compute_securities_ratios(metrics)
        assert r["ROE"] == 12_000 / 80_000
        assert r["GROSS_MARGIN"] == 40_000 / 100_000
        assert r["DEBT_TO_EQUITY"] == 40_000 / 80_000

    def test_securities_specific_ratios_computed(self):
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, **SECURITIES_METRICS)
        r = engine.compute_securities_ratios(metrics)
        assert r["MARGIN_TO_EQUITY"] == 40_000 / 80_000
        assert r["MARGIN_TO_ASSETS"] == 40_000 / 200_000
        assert r["FVTPL_TO_ASSETS"] == 30_000 / 200_000
        assert r["HTM_TO_ASSETS"] == 10_000 / 200_000
        assert r["AFS_TO_ASSETS"] == 20_000 / 200_000

    def test_no_fabrication_without_securities_metrics(self):
        """Thiếu metric đặc thù → KHÔNG được bịa ratio; standard vẫn còn."""
        engine = make_engine()
        r = engine.compute_securities_ratios(dict(STANDARD_METRICS))
        for name in ("MARGIN_TO_EQUITY", "FVTPL_TO_ASSETS", "HTM_TO_ASSETS", "AFS_TO_ASSETS"):
            assert name not in r
        assert r["ROE"] == 12_000 / 80_000

    def test_divide_by_zero_returns_none(self):
        engine = make_engine()
        r = engine.compute_securities_ratios({"MARGIN_LOANS": 0, "TOTAL_EQUITY": 0})
        assert "MARGIN_TO_EQUITY" not in r

    def test_zero_assets_drops_asset_ratios_keeps_roe(self):
        """TOTAL_ASSETS=0 → ROA/ASSET_TURNOVER/margin-to-assets bị loại; ROE vẫn còn."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, TOTAL_ASSETS=0, **SECURITIES_METRICS)
        r = engine.compute_securities_ratios(metrics)
        assert "ROA" not in r
        assert "ASSET_TURNOVER" not in r
        assert "MARGIN_TO_ASSETS" not in r
        assert "FVTPL_TO_ASSETS" not in r
        assert "AFS_TO_ASSETS" not in r
        assert r["ROE"] == 12_000 / 80_000
        assert r["MARGIN_TO_EQUITY"] == 40_000 / 80_000

    def test_zero_revenue_drops_margin_ratios(self):
        """REVENUE=0 → GROSS_MARGIN/NET_MARGIN bị loại (chia 0), không bịa."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, REVENUE=0, **SECURITIES_METRICS)
        r = engine.compute_securities_ratios(metrics)
        assert "GROSS_MARGIN" not in r
        assert "NET_MARGIN" not in r
        assert "RECEIVABLES_TO_REVENUE" not in r

    def test_zero_margin_loans_yields_zero_ratio(self):
        """MARGIN_LOANS=0 (không dư nợ margin) là dữ liệu hợp lệ → ratio = 0, không phải chia 0."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, MARGIN_LOANS=0)
        r = engine.compute_securities_ratios(metrics)
        assert r["MARGIN_TO_EQUITY"] == 0
        assert r["MARGIN_TO_ASSETS"] == 0

    def test_zero_gross_profit_yields_zero_margin(self):
        """GROSS_PROFIT=0 hợp lệ → GROSS_MARGIN=0 (không phải INF/None)."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, GROSS_PROFIT=0)
        r = engine.compute_securities_ratios(metrics)
        assert r["GROSS_MARGIN"] == 0

    def test_negative_equity_guard(self):
        """TOTAL_EQUITY < 0 → ROE/DEBT_TO_EQUITY/MARGIN_TO_EQUITY KHÔNG được sinh (số âm gây hiểu lầm)."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, TOTAL_EQUITY=-80_000)
        r = engine.compute_securities_ratios(metrics)
        assert "ROE" not in r
        assert "DEBT_TO_EQUITY" not in r
        assert "MARGIN_TO_EQUITY" not in r
        assert r["ROA"] == 12_000 / 200_000

    def test_negative_equity_with_margin_loans_guarded(self):
        """SECURITIES: có MARGIN_LOANS nhưng equity âm → MARGIN_TO_EQUITY vẫn không sinh."""
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, TOTAL_EQUITY=-80_000, MARGIN_LOANS=40_000)
        r = engine.compute_securities_ratios(metrics)
        assert "MARGIN_TO_EQUITY" not in r
        assert "ROE" not in r
        assert "DEBT_TO_EQUITY" not in r


class TestInsuranceRatios:
    def test_technical_ratios_computed(self):
        engine = make_engine()
        r = engine.compute_insurance_ratios(dict(INSURANCE_METRICS))
        assert r["LOSS_RATIO"] == 25_000 / 50_000
        assert r["EXPENSE_RATIO"] == 8_000 / 50_000
        assert r["COMBINED_RATIO"] == round((25_000 + 8_000) / 50_000, 4)
        assert r["TECH_RESERVE_TO_ASSETS"] == 60_000 / 300_000

    def test_shared_ratios_for_cross_sector_comparison(self):
        engine = make_engine()
        r = engine.compute_insurance_ratios(dict(INSURANCE_METRICS))
        assert r["ROE"] == 10_000 / 100_000
        assert r["ROA"] == 10_000 / 300_000
        assert r["CFO_TO_NET_INCOME"] == 12_000 / 10_000

    def test_no_premium_no_technical_ratios(self):
        """Không có phí bảo hiểm → không bịa Loss/Combined Ratio."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS)
        del metrics["NET_PREMIUM"]
        r = engine.compute_insurance_ratios(metrics)
        assert "LOSS_RATIO" not in r
        assert "COMBINED_RATIO" not in r
        assert r["ROE"] == 10_000 / 100_000

    def test_zero_premium_guarded(self):
        """premium == 0 → tránh chia cho 0, không tạo ratio vô nghĩa."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, NET_PREMIUM=0)
        r = engine.compute_insurance_ratios(metrics)
        assert "LOSS_RATIO" not in r
        assert "COMBINED_RATIO" not in r

    def test_zero_claims_yields_zero_loss_ratio(self):
        """NET_CLAIMS=0 (không chi trả) hợp lệ → LOSS_RATIO=0, không phải chia 0."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, NET_CLAIMS=0)
        r = engine.compute_insurance_ratios(metrics)
        assert r["LOSS_RATIO"] == 0
        assert r["COMBINED_RATIO"] == round(8_000 / 50_000, 4)

    def test_zero_assets_drops_roa_keeps_ratios(self):
        """TOTAL_ASSETS=0 → ROA bị loại; LOSS_RATIO vẫn tính (mẫu số = premium)."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, TOTAL_ASSETS=0)
        r = engine.compute_insurance_ratios(metrics)
        assert "ROA" not in r
        assert r["LOSS_RATIO"] == 25_000 / 50_000
        assert "TECH_RESERVE_TO_ASSETS" not in r

    def test_zero_equity_drops_roe_keeps_roa(self):
        """TOTAL_EQUITY=0 → ROE bị loại; ROA vẫn còn (mẫu số = assets)."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, TOTAL_EQUITY=0)
        r = engine.compute_insurance_ratios(metrics)
        assert "ROE" not in r
        assert r["ROA"] == 10_000 / 300_000

    def test_negative_equity_guard(self):
        """TOTAL_EQUITY < 0 → ROE KHÔNG được sinh (số âm gây hiểu lầm)."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, TOTAL_EQUITY=-100_000)
        r = engine.compute_insurance_ratios(metrics)
        assert "ROE" not in r
        assert r["ROA"] == 10_000 / 300_000

    def test_zero_net_income_drops_cfo_ratio(self):
        """NET_INCOME=0 → CFO_TO_NET_INCOME bị loại (chia 0); ROA/ROE vẫn còn."""
        engine = make_engine()
        metrics = dict(INSURANCE_METRICS, NET_INCOME=0)
        r = engine.compute_insurance_ratios(metrics)
        assert "CFO_TO_NET_INCOME" not in r
        assert r["ROA"] == 0
        assert r["ROE"] == 0


class TestPeriodRouter:
    def test_router_dispatches_securities(self):
        engine = make_engine()
        metrics = dict(STANDARD_METRICS, **SECURITIES_METRICS)
        result = engine.compute_period_ratios("SSI", "2026Q2", metrics, "SECURITIES")
        assert "MARGIN_TO_EQUITY" in result
        assert result["MARGIN_TO_EQUITY"]["category"] == "leverage"
        assert result["MARGIN_TO_EQUITY"]["interpretation"] in ("GOOD", "WARNING", "BAD", "NEUTRAL")
        assert "ROE" in result

    def test_router_dispatches_insurance(self):
        engine = make_engine()
        result = engine.compute_period_ratios("BVH", "2026Q2", dict(INSURANCE_METRICS), "INSURANCE")
        assert "LOSS_RATIO" in result
        assert "COMBINED_RATIO" in result

    def test_router_defaults_to_standard(self):
        engine = make_engine()
        result = engine.compute_period_ratios("HPG", "2026Q2", dict(STANDARD_METRICS), None)
        assert "GROSS_MARGIN" in result
        assert "MARGIN_TO_EQUITY" not in result

    def test_router_bank_unchanged(self):
        engine = make_engine()
        metrics = {
            "NII": 500,
            "NET_INCOME": 100,
            "CUSTOMER_LOANS": 2000,
            "CUSTOMER_DEPOSITS": 1500,
            "TOTAL_ASSETS": 5000,
            "TOTAL_EQUITY": 800,
            "CFO": 120,
            "PROVISION_EXPENSE": 10,
        }
        result = engine.compute_period_ratios("VCB", "2026Q2", metrics, "BANK")
        assert "NIM" in result
        assert "LDR" in result
        assert "GROSS_MARGIN" not in result

    def test_bank_negative_equity_guards_roe_and_capital(self):
        """BANK: equity âm → ROE/CAPITAL_RATIO không sinh; NIM/LDR vẫn còn."""
        engine = make_engine()
        metrics = {
            "NII": 500,
            "NET_INCOME": 100,
            "CUSTOMER_LOANS": 2000,
            "CUSTOMER_DEPOSITS": 1500,
            "TOTAL_ASSETS": 5000,
            "TOTAL_EQUITY": -800,
            "CFO": 120,
            "PROVISION_EXPENSE": 10,
        }
        result = engine.compute_period_ratios("VCB", "2026Q2", metrics, "BANK")
        assert "ROE" not in result
        assert "CAPITAL_RATIO" not in result
        assert "NIM" in result
        assert "LDR" in result
