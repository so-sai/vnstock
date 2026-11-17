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
        """Cash=0.0 nhưng NO_DATA → không được kết luận DISTRESSED dựa trên số 0 giả."""
        organs = self._organs()
        archetype, _ = CompanyHealthV2._classify_archetype(organs, no_data=["cash"])
        assert archetype != "DISTRESSED"

    def test_cash_really_weak_still_distressed(self):
        """Cash=0.0 thật (có dữ liệu CF) vẫn phải bị DISTRESSED."""
        organs = self._organs(p=0.2, c=0.0, b=0.2)
        archetype, _ = CompanyHealthV2._classify_archetype(organs, no_data=[])
        assert archetype == "DISTRESSED"

    def test_default_no_data_is_none(self):
        organs = self._organs(p=0.2, c=0.0, b=0.2)
        archetype, _ = CompanyHealthV2._classify_archetype(organs)
        assert archetype == "DISTRESSED"

    def test_bank_khong_bao_gio_mang_nhan_hang_hoa(self):
        """[WHY] Bản tin 17/11 gọi NVB (Ngân hàng TMCP Quốc Dân) là "Hàng hóa".

        Gốc lỗi: moat của BANK chỉ còn ROE-stability; NVB có ROE biến động mạnh nên
        moat = 0.00 ⇒ khớp COMMODITY (m ≤ 0.25, p ≥ 0.20). Nhưng COMMODITY/CYCLICAL
        là archetype NGÀNH (price-taker, biên gắn giá đầu vào/đầu ra) — khái niệm
        không tồn tại với ngân hàng. Không còn archetype nào áp dụng được cho BANK
        ⇒ phải trả INSUFFICIENT_DATA, tuyệt đối không bịa "Hàng hóa"/"Chu kỳ".
        """
        # organ của NVB đo thật trên DB: p=0.58 c=0.87 b=0.00 e=0.40 m=0.00
        organs = self._organs(p=0.58, c=0.87, b=0.00, e=0.40, m=0.00)
        archetype, confidence = CompanyHealthV2._classify_archetype(
            organs, no_data=["cash", "balance_sheet"], entity="BANK"
        )
        assert archetype == "INSUFFICIENT_DATA"
        assert confidence == 0.0

    def test_bank_khong_bi_da_ra_khi_con_archetype_khac(self):
        """[WHY] Chống vá quá tay: chỉ chặn 2 archetype ngành, KHÔNG được đá ngân hàng
        ra khỏi mọi phân loại. ABB khớp LEVERAGED_GROWTH (b ≤ 0.40, p ≥ 0.50, e ≥ 0.50)
        ⇒ vẫn phải giữ LEVERAGED_GROWTH dù COMMODITY/CYCLICAL bị chặn."""
        organs = self._organs(p=0.60, c=0.10, b=0.30, e=0.55, m=0.00)
        archetype, _ = CompanyHealthV2._classify_archetype(
            organs, no_data=["cash", "balance_sheet"], entity="BANK"
        )
        assert archetype == "LEVERAGED_GROWTH"

    def test_bank_khong_khop_gi_van_giu_nguyen_fallback(self):
        """[WHY] Chống vá quá tay lần 2: SHB/TCB/BID/CTG hiện rơi vào fallback
        STEADY_EARNER 0.35 vì cash < 0.5 (CFO/NI thấp) — nếu đổi fallback thành
        INSUFFICIENT_DATA, 20 ngân hàng lớn sẽ biến mất khỏi bản tin. Hành vi này
        giữ nguyên; nợ mô hình organ ngân hàng được ghi ở tech_debt_backlog Mục 33.
        """
        organs = self._organs(p=0.57, c=0.037, b=0.736, e=0.384, m=0.645)
        archetype, confidence = CompanyHealthV2._classify_archetype(
            organs, no_data=["cash"], entity="BANK"
        )
        assert archetype == "STEADY_EARNER"
        assert confidence == 0.35

    def test_doanh_nghiep_thuong_van_giu_commodity(self):
        """[WHY] Chống vá quá tay lần 3: doanh nghiệp phi ngân hàng có moat yếu vẫn
        phải là COMMODITY — luật chặn chỉ áp cho BANK."""
        organs = self._organs(p=0.50, c=0.45, b=0.35, e=0.40, m=0.10)
        archetype, _ = CompanyHealthV2._classify_archetype(
            organs, no_data=[], entity="STANDARD"
        )
        assert archetype == "COMMODITY"


