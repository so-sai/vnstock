"""test_health_v2_no_data.py â€” Regression: Cash=0.00 pháº£i lÃ  NO_DATA, khÃ´ng pháº£i DISTRESSED.

WHY (bug tá»«ng xáº£y ra):
  - CafÃ©F Bank API khÃ´ng tráº£ CF statement cho doanh nghiá»‡p thÆ°á»ng (17 rows tÃ³m táº¯t)
    â†’ BCM khÃ´ng bao giá» cÃ³ CFO_TO_NET_INCOME/FCF_TO_NET_INCOME/CAPEX_TO_CFO.
  - _score_cash tráº£ 0.0 khi khÃ´ng cÃ³ ratio â†’ health-v2 in "Cash: 0.000 ðŸ”´"
    â†’ _classify_archetype coi cash=0.00 â‰¤ 0.30 â†’ BCM bá»‹ nháº§m DISTRESSED (P=79%).
  - Fix: _detect_no_data Ä‘Ã¡nh dáº¥u "cash" â†’ hiá»ƒn thá»‹ "NO DATA" vÃ  DISTRESSED
    pháº£i bá» qua organ thiáº¿u dá»¯ liá»‡u.

Run:  python -m pytest tests/test_health_v2_no_data.py -q   (tá»« backend/)
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.company_health_v2 import CompanyHealthV2, OrganScores


class TestDetectNoData:
    def test_cash_missing_flagged_no_data(self):
        """KhÃ´ng cÃ³ CF ratio â†’ cash Ä‘Æ°á»£c Ä‘Ã¡nh dáº¥u NO_DATA (khÃ´ng pháº£i Ä‘iá»ƒm 0 tháº­t)."""
        ratios = {"NET_MARGIN": [("2025Q4", 0.25)], "DEBT_TO_EQUITY": [("2025Q4", 1.7)]}
        missing = CompanyHealthV2._detect_no_data(ratios, "STANDARD")
        assert "cash" in missing
        assert "balance_sheet" not in missing

    def test_balance_missing_flagged_no_data(self):
        ratios = {"NET_MARGIN": [("2025Q4", 0.25)], "CFO_TO_NET_INCOME": [("2025Q4", 1.2)]}
        missing = CompanyHealthV2._detect_no_data(ratios, "STANDARD")
        assert "balance_sheet" in missing
        assert "cash" not in missing

    def test_full_data_not_flagged(self):
        ratios = {
            "CFO_TO_NET_INCOME": [("2025Q4", 1.2)],
            "DEBT_TO_EQUITY": [("2025Q4", 1.7)],
            "NET_MARGIN": [("2025Q4", 0.25)],
        }
        missing = CompanyHealthV2._detect_no_data(ratios, "STANDARD")
        assert missing == []

    def test_bank_cash_uses_cfo_to_net_profit(self):
        """Bank khÃ´ng cÃ³ CFO_TO_NET_INCOME nhÆ°ng cÃ³ CFO_TO_NET_PROFIT â†’ khÃ´ng NO_DATA."""
        ratios = {"CFO_TO_NET_PROFIT": [("2025Q4", 1.1)], "NPL_RATIO": [("2025Q4", 0.02)]}
        missing = CompanyHealthV2._detect_no_data(ratios, "BANK")
        assert "cash" not in missing


class TestClassifyArchetype:
    def _organs(self, p=0.63, c=0.0, b=0.64, e=0.64, m=0.66):
        return OrganScores(profitability=p, cash=c, balance_sheet=b, efficiency=e, moat=m,
                           vector=[p, c, b, e, m])

    def test_cash_no_data_not_distressed(self):
        """Cash=0.0 nhÆ°ng NO_DATA â†’ khÃ´ng Ä‘Æ°á»£c káº¿t luáº­n DISTRESSED dá»±a trÃªn sá»‘ 0 giáº£."""
        organs = self._organs()
        archetype, _ = CompanyHealthV2._classify_archetype(organs, no_data=["cash"])
        assert archetype != "DISTRESSED"

    def test_cash_really_weak_still_distressed(self):
        """Cash=0.0 tháº­t (cÃ³ dá»¯ liá»‡u CF) váº«n pháº£i bá»‹ DISTRESSED."""
        organs = self._organs(p=0.2, c=0.0, b=0.2)
        archetype, _ = CompanyHealthV2._classify_archetype(organs, no_data=[])
        assert archetype == "DISTRESSED"

    def test_default_no_data_is_none(self):
        organs = self._organs(p=0.2, c=0.0, b=0.2)
        archetype, _ = CompanyHealthV2._classify_archetype(organs)
        assert archetype == "DISTRESSED"

