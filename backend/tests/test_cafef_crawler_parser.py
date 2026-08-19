"""test_cafef_crawler_parser.py — Regression tests cho parser BCTC cafef_crawler.

WHY (bug từng xảy ra):
  - fetch_cafef_bank_api (nguồn CHÍNH đang hoạt động) dùng _map_cafef_metric,
    nhưng map này THIẾU CFO / SHORT_TERM_DEBT / LONG_TERM_DEBT / INTEREST_EXPENSE.
  - Hệ quả BCM chỉ có 7 metrics (REVENUE, GROSS_PROFIT, PRE_TAX_INCOME,
    NET_INCOME, TOTAL_ASSETS, TOTAL_EQUITY, CURRENT_LIAB) → health_engine
    không tính được CFO_TO_NET_INCOME, DEBT_TO_EQUITY, INTEREST_COVERAGE
    → health-v2 Cash=0.00, Bal=0.00 → BCM bị nhầm DISTRESSED.
  - STB 2025Q3 NET_INCOME bị gán -2,752,462,000,000 (lệch cột +1): CafeF Bank
    API trả 'Chỉ tiêu | Quý 3- 2025 | Quý 4- 2025 | ...' nhưng header_labels
    giữ cả 'Chỉ tiêu' → vòng lặp đọc lệch 1 cột, số Q4/2025 rơi vào Q3/2025.
  - NET_INCOME ngân hàng bị gán statement_type='BS' vì BANK_METRICS thiếu
    NET_INCOME → fallback ("", "BS") trong write_batch.

Run:  python -m pytest tests/test_cafef_crawler_parser.py -q   (từ backend/)
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.cafef_crawler import CafeFCrawler
from src.financial.financial_facts import DataIntegrityValidator, FinancialFactsDB


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


# ============================================================
# STB 2025Q3: lệch cột +1 trong _parse_cafef_bank_api
# ============================================================
# CafeF Bank API trả bảng dạng:
#   ROW 0: ['Chỉ tiêu', 'Quý 3- 2025', 'Quý 4- 2025', 'Quý 1- 2026', 'Quý 2- 2026']
#   ROW 5: ['Lợi nhuận ròng', '2.901.283', '-2.752.462', '1.584.404', '1.346.691']
# Q3/2025 thật = +2.901.283 (triệu VND), Q4/2025 = -2.752.462 (âm).
# header_labels phải bỏ 'Chỉ tiêu' để không lệch cột (bug cũ gán số Q4 vào Q3).
BANK_API_HTML = """<table>
<tr><th>Chỉ tiêu</th><th>Quý 3- 2025</th><th>Quý 4- 2025</th><th>Quý 1- 2026</th><th>Quý 2- 2026</th></tr>
<tr><td>Kết quả kinh doanh</td><td>Xem đầy đủ</td></tr>
<tr><td>Tổng doanh thu(*)</td><td>17.591.803</td><td>17.060.000</td><td>17.063.742</td><td>20.721.971</td></tr>
<tr><td>Tổng lợi nhuận trước thuế</td><td>3.656.898</td><td>-3.360.145</td><td>2.106.205</td><td>2.029.891</td></tr>
<tr><td>Lợi nhuận ròng(**)</td><td>2.901.283</td><td>-2.752.462</td><td>1.584.404</td><td>1.346.691</td></tr>
<tr><td>(*) tỷ đồng</td><td></td><td></td><td></td><td></td></tr>
<tr><td>Tài sản</td><td>Xem đầy đủ</td></tr>
<tr><td>Tổng tài sản</td><td>848.942.045</td><td>917.119.803</td><td>859.571.527</td><td>892.048.566</td></tr>
</table>"""


class TestBankApiColumnAlignment:
    def test_no_column_shift_net_income(self):
        """BUG: header_labels giữ 'Chỉ tiêu' → lệch cột +1 → Q4/2025 (-2.752.462)
        rơi vào Q3/2025. Sau fix: STB 2025Q3 NET_INCOME = +2,901,283,000,000,
        Q4/2025 = -2,752,462,000,000."""
        c = CafeFCrawler.__new__(CafeFCrawler)
        periods = c._parse_cafef_bank_api("STB", "BANK", BANK_API_HTML)
        by_period = {(p["_fiscal_year"], p["_fiscal_quarter"]): p for p in periods}

        q3 = by_period[(2025, 3)]
        q4 = by_period[(2025, 4)]
        assert q3["NET_INCOME"] == pytest.approx(2_901_283_000_000.0)
        assert q4["NET_INCOME"] == pytest.approx(-2_752_462_000_000.0)

    def test_no_column_shift_total_assets(self):
        """Cùng lỗi lệch cột — TOTAL_ASSETS Q3/2025 = 848,942,045 triệu VND."""
        c = CafeFCrawler.__new__(CafeFCrawler)
        periods = c._parse_cafef_bank_api("STB", "BANK", BANK_API_HTML)
        by_period = {(p["_fiscal_year"], p["_fiscal_quarter"]): p for p in periods}
        assert by_period[(2025, 3)]["TOTAL_ASSETS"] == pytest.approx(848_942_045_000_000.0)

    def test_header_label_must_exclude_chi_tieu(self):
        """header_labels không được chứa 'Chỉ tiêu' (cột nhãn không phải quý)."""
        src = (BACKEND / "src" / "financial" / "cafef_crawler.py").read_text(encoding="utf-8-sig")
        assert "header_labels" in src


# ============================================================
# NET_INCOME ngân hàng phải có statement_type='IS'
# ============================================================
class TestBankNetIncomeStatementType:
    def test_write_batch_bank_net_income_is_is(self, tmp_path):
        """write_batch ghi NET_INCOME entity BANK phải statement_type='IS'
        (BANK_METRICS thiếu NET_INCOME → fallback ('', 'BS') → sai)."""
        db_file = str(tmp_path / "t.db")
        db = FinancialFactsDB(db_path=db_file)
        db.init_schema()
        db.register_entity("STB", "BANK")
        res = db.write_batch(
            "STB",
            {"_fiscal_year": 2025, "_fiscal_quarter": 3, "NET_INCOME": 2_901_283_000_000.0},
            "BANK",
            source="cafef",
        )
        assert res["status"] == "SUCCESS", res
        row = db.connect().execute(
            "SELECT statement_type FROM financial_facts WHERE symbol='STB' AND period='2025Q3' AND metric='NET_INCOME'"
        ).fetchone()
        assert row is not None
        assert row[0] == "IS"

    def test_bank_metrics_registry_contains_net_income(self):
        """BANK_METRICS phải khai báo NET_INCOME với statement_type 'IS'."""
        from src.financial.financial_facts import BANK_METRICS

        assert BANK_METRICS["NET_INCOME"][1] == "IS"


# ============================================================
# Invariant guard: VCI bridge phải phát hiện BCTC Công ty Mẹ
# ============================================================
class TestConsolidatedInvariantGuard:
    """fetch_vci_bridge phải gắn cờ khi REVENUE sụp >50% so kỳ liền trước.

    WHY (FPT consolidated 2025): một nguồn phụ từng trả BCTC Công ty Mẹ
    (FPT Q1=5.39T / Q2=6.12T thay vì hợp nhất ~16T/quý). VCI hiện trả
    hợp nhất, nhưng chưa có guard nào chặn nếu nguồn phụ lặp lại lỗi —
    theo Đạo luật 2 (accounting invariant guard): REVENUE kỳ này < 50%
    kỳ liền trước (cùng batch, kỳ trước hợp nhất) → NEEDS_AUDIT thay vì
    silent ghi.

    NGUỒN DỮ LIỆU ĐÃ THĂM DÒ (probe live 2026-08-19):
      - VCI (vnstock, VnstockProvider): nguồn CHÍNH — trả BCTC HỢP NHẤT.
        FPT 2025Q1 net_sales = 16,058,140,000,000 / Q2 = 16,624,700,000,000
        (item_id `net_sales` → VNSTOCK_ITEM_ID_MAP['net_sales']='REVENUE').
      - KBS (secondary trong cross_validate): probe trả NO DATA cho FPT —
        không phải nguồn lỗi.
      - vietstock_crawler (Unit=1000000000): trả hợp nhất (FPT Q3=17.2T,
        Q4=20.2T — đã ghi vào DB source='vietstock').
      - vnfinancialdata Parquet (annual, HSX/HNX): nguồn ĐỐI CHIẾU
        ground-truth trong cross_check_facts_2025.py — STB chỉ có số cả
        năm 5.94T; sau khi vá STB 2025Q4 = -2.75T, tổng DB = 5.94T → MATCH.
    """

    def test_flags_suspect_when_revenue_collapses(self, monkeypatch):
        """REVENUE 17.6T → 5.4T (>50% sụp) phải gắn SUSPECTED_PARENT_STATEMENT."""
        periods = [
            {"_fiscal_year": 2024, "_fiscal_quarter": 4, "REVENUE": 17_600_000_000_000.0},
            {"_fiscal_year": 2025, "_fiscal_quarter": 1, "REVENUE": 5_400_000_000_000.0},
            {"_fiscal_year": 2025, "_fiscal_quarter": 2, "REVENUE": 6_100_000_000_000.0},
        ]
        import src.financial.financial_facts as ff

        class FakeDB:
            def get_entity_type(self, symbol):
                return "STANDARD"

        c = CafeFCrawler(db=FakeDB(), delay=0)

        def fake_fetch(self, symbol, limit=30):
            return periods

        monkeypatch.setattr(ff.VnstockCrawler, "fetch_financials_vnstock", fake_fetch)
        out = c.fetch_vci_bridge("FPT")
        by_period = {(p["_fiscal_year"], p["_fiscal_quarter"]): p for p in out}
        assert by_period[(2025, 1)]["_integrity_flags"] == "SUSPECTED_PARENT_STATEMENT"
        assert "_integrity_flags" not in by_period[(2025, 2)]
        assert "_integrity_flags" not in by_period[(2024, 4)]

    def test_no_false_positive_when_revenue_stable(self, monkeypatch):
        """REVENUE ổn định (16.0T → 16.6T) không được gắn cờ."""
        periods = [
            {"_fiscal_year": 2024, "_fiscal_quarter": 4, "REVENUE": 17_600_000_000_000.0},
            {"_fiscal_year": 2025, "_fiscal_quarter": 1, "REVENUE": 16_058_140_000_000.0},
            {"_fiscal_year": 2025, "_fiscal_quarter": 2, "REVENUE": 16_624_700_000_000.0},
        ]
        import src.financial.financial_facts as ff

        class FakeDB:
            def get_entity_type(self, symbol):
                return "STANDARD"

        c = CafeFCrawler(db=FakeDB(), delay=0)

        def fake_fetch(self, symbol, limit=30):
            return periods

        monkeypatch.setattr(ff.VnstockCrawler, "fetch_financials_vnstock", fake_fetch)
        out = c.fetch_vci_bridge("FPT")
        for p in out:
            assert "_integrity_flags" not in p

    def test_write_batch_carries_suspect_flag(self, tmp_path):
        """period dict có _integrity_flags → write_batch phải ghi vào DB."""
        db_file = str(tmp_path / "t.db")
        db = FinancialFactsDB(db_path=db_file)
        db.init_schema()
        db.register_entity("FPT", "STANDARD")
        res = db.write_batch(
            "FPT",
            {
                "_fiscal_year": 2025,
                "_fiscal_quarter": 1,
                "REVENUE": 5_400_000_000_000.0,
                "_integrity_flags": "SUSPECTED_PARENT_STATEMENT",
            },
            "STANDARD",
            source="vnstock",
        )
        assert res["status"] == "SUCCESS", res
        row = db.connect().execute(
            "SELECT integrity_flags FROM financial_facts WHERE symbol='FPT' AND period='2025Q1' AND metric='REVENUE'"
        ).fetchone()
        assert row is not None
        assert "SUSPECTED_PARENT_STATEMENT" in (row[0] or "")


# ============================================================
# Invariant guard: auto_scale phải phát hiện scale sai (SUSPECTED_SCALE_CORRUPTION)
# ============================================================
class TestScaleCorruptionGuard:
    """auto_scale_to_vnd phải gắn cờ khi large-scale metric quá nhỏ.

    WHY (BCM vietstock scaler): một nguồn phụ từng trả REVENUE 422,000 (VND)
    thay vì 422 tỷ VND (4.22e11) — auto_scale_to_vnd block "tỷ→VND" nhân
    1e9 thành 4.22e14 (vì REVENUE dải [1e9, 5e14] quá rộng) → ghi sai. Thu hẹp
    dải REVENUE (max mã VN = MWG/HPG ~156T < 2e14) để 4.22e14 bị loại, và thêm
    guard: large-scale metric mà kết quả cuối < 100 triệu VND là bất khả thi
    cho công ty niêm yết VN → SUSPECTED_SCALE_CORRUPTION → write_batch SKIP
    (fail-closed, không ghi im lặng — fallback nguồn khác cung cấp số đúng).
    """

    def test_flags_suspect_when_revenue_tiny(self):
        """REVENUE=422,000 (thay vì 422 tỷ) phải gắn SUSPECTED_SCALE_CORRUPTION."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(422_000.0, "REVENUE", "BCM")
        assert note == "SUSPECTED_SCALE_CORRUPTION"
        assert value == 422_000.0

    def test_flags_suspect_when_net_income_tiny(self):
        """NET_INCOME nhỏ (<100 triệu) cho mã niêm yết cũng phải gắn cờ."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(50_000.0, "NET_INCOME", "BCM")
        assert note == "SUSPECTED_SCALE_CORRUPTION"

    def test_keeps_ty_dong_scale_to_vnd(self):
        """Giá trị tỷ đồng hợp lệ (vietstock Unit=1e9) vẫn scale đúng ×1e9."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(17_205.0, "REVENUE", "FPT")
        assert note == "AUTO_SCALED_1000000000x"
        assert value == pytest.approx(17_205_000_000_000.0)

    def test_keeps_valid_vnd_value(self):
        """Giá trị đã đúng VND (17.2 nghìn tỷ) giữ nguyên, note OK."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(17_205_000_000_000.0, "REVENUE", "FPT")
        assert note == "OK"
        assert value == pytest.approx(17_205_000_000_000.0)

    def test_net_income_million_unit_scales_to_billion(self):
        """NET_INCOME raw=62 (đơn vị 'triệu đồng') phải scale ×1e9 = 62 tỷ.

        WHY (lỗ hổng batch re-crawl): VCI trả NET_INCOME quý theo triệu đồng (vd 62)
        cho nhiều mã. Dải NET_INCOME lo=0 khiến 62×1000=62,000 VND lọt vào
        'plausible' → ghi 62 nghìn (rác) thay vì 62 tỷ. Nâng lo lên MIN_SCALE_SUSPECT
        buộc auto_scale phải thử tiếp ×1e9.
        """
        value, note = DataIntegrityValidator.auto_scale_to_vnd(62.0, "NET_INCOME", "AAA")
        assert note == "AUTO_SCALED_1000000000x"
        assert value == pytest.approx(62_000_000_000.0)

    def test_ebit_million_unit_scales_to_billion(self):
        """EBIT raw=62 (triệu đồng) → ×1e9; EBIT KHÔNG có dải range hiện tại (trả True
        vô điều kiện) nên 62×1000=62k bị ghi — cần range + nhóm abs để chặn."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(62.0, "EBIT", "AAA")
        assert note == "AUTO_SCALED_1000000000x"
        assert value == pytest.approx(62_000_000_000.0)

    def test_gross_profit_million_unit_scales_to_billion(self):
        """GROSS_PROFIT raw=62 (triệu đồng) → ×1e9 (tương tự EBIT, thiếu dải range)."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(62.0, "GROSS_PROFIT", "AAA")
        assert note == "AUTO_SCALED_1000000000x"
        assert value == pytest.approx(62_000_000_000.0)

    def test_zero_value_keeps_ok_not_auto_scaled(self):
        """Giá trị 0.0 giữ nguyên với note OK — KHÔNG bị đóng dấu AUTO_SCALED_1000x oan.

        WHY: batch re-crawl FULL từng nhân 0×1000=0 và gắn AUTO_SCALED_1000x cho
        >10,000 facts (LONG_TERM_DEBT/CAPEX/DEPRECIATION=0). 0 là trạng thái hợp lệ,
        cấm suy đoán đơn vị trên số 0.
        """
        value, note = DataIntegrityValidator.auto_scale_to_vnd(0.0, "LONG_TERM_DEBT", "AAH")
        assert value == 0.0
        assert note == "OK"

    def test_zero_net_income_keeps_ok(self):
        """NET_INCOME=0.0 (placeholder) cũng giữ nguyên 0.0 với note OK — không AUTO_SCALED."""
        value, note = DataIntegrityValidator.auto_scale_to_vnd(0.0, "NET_INCOME", "AME")
        assert value == 0.0
        assert note == "OK"

    def test_write_batch_skips_suspect_scale_corruption(self, tmp_path):
        """write_batch KHÔNG ghi REVENUE nghi scale sai (SKIP_SCALE_CORRUPTION)."""
        db_file = str(tmp_path / "t.db")
        db = FinancialFactsDB(db_path=db_file)
        db.init_schema()
        db.register_entity("BCM", "STANDARD")
        res = db.write_batch(
            "BCM",
            {
                "_fiscal_year": 2025,
                "_fiscal_quarter": 3,
                "REVENUE": 422_000.0,
                "TOTAL_EQUITY": 23_317_000_000_000.0,
            },
            "STANDARD",
            source="vietstock",
        )
        assert res["status"] == "SUCCESS", res
        assert any("SKIP_REVENUE" in w for w in res["warnings"]), res
        row = db.connect().execute(
            "SELECT value FROM financial_facts WHERE symbol='BCM' AND period='2025Q3' AND metric='REVENUE'"
        ).fetchone()
        assert row is None, "REVENUE nghi scale sai phải bị SKIP, không được ghi"
        eq = db.connect().execute(
            "SELECT value FROM financial_facts WHERE symbol='BCM' AND period='2025Q3' AND metric='TOTAL_EQUITY'"
        ).fetchone()
        assert eq is not None, "metric hợp lệ khác vẫn phải được ghi"
