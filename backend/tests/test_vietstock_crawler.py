"""test_vietstock_crawler.py — Tests cho Vietstock Finance crawler (free summary).

WHY (context 2026-07-31):
  - VNDirect/TCBS bridge: DNS UNVERIFIED từ môi trường dev → cần nguồn thứ 4
    để kiểm tra chéo dữ liệu tài chính.
  - Vietstock free tier: POST /data/financeinfo trả BCTC Tóm tắt 4 quý
    (KQKD 5 + CDKT 6 + CSTC 6 dòng). BCTC chi tiết 37 dòng → PAYWALL.
  - Phát hiện data drift: DB (CafeF Bank API) BCM label lệch 1 quý so với
    Vietstock (PeriodBegin/PeriodEnd explicit) — cross_check dùng được để bắt.

Run:  python -m pytest tests/test_vietstock_crawler.py -q   (từ backend/)
"""

import sys
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.financial.vietstock_crawler import (
    VietstockCrawler,
    VIETSTOCK_METRIC_MAP,
    VIETSTOCK_BASE_URL,
)
from src.financial.cafef_crawler import CafeFCrawler


# ── Fixture: payload thật capture từ /data/financeinfo (BCM, free 4 quý) ──
def _real_payload():
    payload = [
        [  # periods
            {"ID": 4, "Row": 4, "YearPeriod": 2025, "TermCode": "Q2",
             "PeriodBegin": "202504", "PeriodEnd": "202506"},
            {"ID": 3, "Row": 3, "YearPeriod": 2025, "TermCode": "Q3",
             "PeriodBegin": "202507", "PeriodEnd": "202509"},
            {"ID": 2, "Row": 2, "YearPeriod": 2025, "TermCode": "Q4",
             "PeriodBegin": "202510", "PeriodEnd": "202512"},
            {"ID": 1, "Row": 1, "YearPeriod": 2026, "TermCode": "Q1",
             "PeriodBegin": "202601", "PeriodEnd": "202603"},
        ],
        {
            "Kết quả kinh doanh": [
                {"Name": "Doanh thu thuần", "Value1": 1104960466553.0,
                 "Value2": 1376454513805.0, "Value3": 828384301042.0,
                 "Value4": 2520696393487.0},
                {"Name": "Lợi nhuận gộp", "Value1": 682650522716.0,
                 "Value2": 1040043428725.0, "Value3": 509907089589.0,
                 "Value4": 1808983082654.0},
                {"Name": "LN thuần từ HĐKD", "Value1": 298100278801.0,
                 "Value2": 1291522970056.0, "Value3": 407907359067.0,
                 "Value4": 1703089235858.0},
                {"Name": "LNST thu nhập DN", "Value1": 288428728297.0,
                 "Value2": 1247075059092.0, "Value3": 422385962602.0,
                 "Value4": 1467709611247.0},
            ],
            "Báo cáo tình hình tài chính": [
                {"Name": "Tài sản ngắn hạn", "Value1": 31731742585463.0,
                 "Value2": 31384545366358.0, "Value3": 31593936321156.0,
                 "Value4": 31456653937054.0},
                {"Name": "Tổng tài sản", "Value1": 61310091666414.0,
                 "Value2": 60908205782622.0, "Value3": 58182019548233.0,
                 "Value4": 57290938438608.0},
                {"Name": "Nợ phải trả", "Value1": 38832215623031.0,
                 "Value2": 37590843080089.0, "Value3": 36094277481215.0,
                 "Value4": 35878346940126.0},
                {"Name": "Nợ ngắn hạn", "Value1": 19756628598894.0,
                 "Value2": 21678288206864.0, "Value3": 20051995983453.0,
                 "Value4": 22469544340020.0},
                {"Name": "Vốn chủ sở hữu", "Value1": 22477876043383.0,
                 "Value2": 23317362702533.0, "Value3": 22087742067018.0,
                 "Value4": 21412591498482.0},
                {"Name": "Lợi ích của CĐ thiểu số", "Value1": None,
                 "Value2": None, "Value3": None, "Value4": None},
            ],
            "Chỉ số tài chính": [
                {"Name": "EPS 4 quý", "Unit": "VNĐ", "Value1": 3285.0,
                 "Value2": 3361.0, "Value3": 3488.0, "Value4": 3419.0},
                {"Name": "BVPS cơ bản", "Unit": "VNĐ", "Value1": 21718.0,
                 "Value2": 22529.0, "Value3": 21341.0, "Value4": 20688.0},
            ],
        },
        None,
        None,
    ]
    return payload


class TestMetricMap:
    def test_map_has_revenue(self):
        assert VIETSTOCK_METRIC_MAP["Doanh thu thuần"] == "REVENUE"

    def test_map_has_ebit_and_net_income(self):
        assert VIETSTOCK_METRIC_MAP["LN thuần từ HĐKD"] == "EBIT"
        assert VIETSTOCK_METRIC_MAP["LNST thu nhập DN"] == "NET_INCOME"

    def test_map_has_balance_sheet(self):
        assert VIETSTOCK_METRIC_MAP["Tổng tài sản"] == "TOTAL_ASSETS"
        assert VIETSTOCK_METRIC_MAP["Tài sản ngắn hạn"] == "CURRENT_ASSETS"
        assert VIETSTOCK_METRIC_MAP["Nợ phải trả"] == "TOTAL_LIABILITIES"
        assert VIETSTOCK_METRIC_MAP["Nợ ngắn hạn"] == "CURRENT_LIAB"
        assert VIETSTOCK_METRIC_MAP["Vốn chủ sở hữu"] == "TOTAL_EQUITY"

    def test_map_has_per_share(self):
        assert VIETSTOCK_METRIC_MAP["EPS 4 quý"] == "EPS"
        assert VIETSTOCK_METRIC_MAP["BVPS cơ bản"] == "BOOK_VALUE_PS"

    def test_map_does_not_have_cfo_free_tier(self):
        """Free summary KHÔNG có CFO/CFI/CFF/CAPEX — paywall."""
        assert "CFO" not in VIETSTOCK_METRIC_MAP.values()
        assert "CAPEX" not in VIETSTOCK_METRIC_MAP.values()
        assert "TOTAL_DEBT" not in VIETSTOCK_METRIC_MAP.values()


class TestParsePayload:
    def test_parses_4_periods(self):
        periods = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        assert len(periods) == 4

    def test_period_order_newest_first(self):
        periods = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        assert periods[0]["_fiscal_year"] == 2026
        assert periods[0]["_fiscal_quarter"] == 1
        assert periods[-1]["_fiscal_year"] == 2025
        assert periods[-1]["_fiscal_quarter"] == 2

    def test_value_maps_to_correct_period(self):
        """Value1 ↔ Row=1 (Q1/2026), Value4 ↔ Row=4 (Q2/2025)."""
        periods = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        newest = periods[0]
        assert newest["REVENUE"] == 1104960466553.0
        oldest = periods[-1]
        assert oldest["REVENUE"] == 2520696393487.0

    def test_entity_type_set(self):
        periods = VietstockCrawler.parse_financeinfo_payload(
            _real_payload(), entity_type="STANDARD")
        assert all(p["_entity_type"] == "STANDARD" for p in periods)

    def test_skips_null_values(self):
        """Lợi ích của CĐ thiểu số = None → không xuất hiện."""
        periods = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        for p in periods:
            assert "Lợi ích của CĐ thiểu số" not in p
            assert not any(k == "None" for k in p)

    def test_skips_unknown_metric_names(self):
        payload = [{"Row": 1, "YearPeriod": 2026, "TermCode": "Q1"}],
        payload = [
            [{"Row": 1, "YearPeriod": 2026, "TermCode": "Q1"}],
            {"XYZ": [{"Name": "Không biết metric này", "Value1": 123.0}]},
            None, None,
        ]
        periods = VietstockCrawler.parse_financeinfo_payload(payload)
        assert len(periods) == 0  # không metric nào biết → period bị bỏ

    def test_empty_payload_returns_empty(self):
        assert VietstockCrawler.parse_financeinfo_payload([]) == []
        assert VietstockCrawler.parse_financeinfo_payload(None) == []
        assert VietstockCrawler.parse_financeinfo_payload("junk") == []

    def test_period_missing_quarter_skipped(self):
        payload = [
            [{"Row": 1, "YearPeriod": 2026, "TermCode": "H1"}],
            {"Kết quả kinh doanh": [
                {"Name": "Doanh thu thuần", "Value1": 10.0}]},
            None, None,
        ]
        assert VietstockCrawler.parse_financeinfo_payload(payload) == []

    def test_roundtrip_through_json(self):
        """Payload JSON (như response thật) parse được."""
        raw = json.dumps(_real_payload())
        payload = json.loads(raw)
        periods = VietstockCrawler.parse_financeinfo_payload(payload)
        assert len(periods) == 4
        assert periods[0]["TOTAL_ASSETS"] == 61310091666414.0


class TestToDbPeriods:
    def test_clone_keeps_metrics(self):
        src = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        out = VietstockCrawler.to_db_periods(src, entity_type="STANDARD")
        assert out[0]["REVENUE"] == src[0]["REVENUE"]
        assert out[0]["_entity_type"] == "STANDARD"

    def test_does_not_mutate_source(self):
        src = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        before = dict(src[0])
        VietstockCrawler.to_db_periods(src, entity_type="BANK")
        assert src[0] == before


class TestCrossCheck:
    def _db_style_periods(self):
        """Mô phỏng dữ liệu DB (CafeF) — giá trị lệch 1 quý (data drift)."""
        vs = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        out = []
        for p in vs:
            y, q = p["_fiscal_year"], p["_fiscal_quarter"]
            shifted = (y, q - 1) if q > 1 else (y - 1, 4)
            d = {"_fiscal_year": shifted[0], "_fiscal_quarter": shifted[1]}
            d.update({k: v for k, v in p.items() if not k.startswith("_")})
            out.append(d)
        return out

    def test_cross_check_matches_aligned_data(self):
        ref = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        cand = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        res = CafeFCrawler.cross_check(ref, cand, tolerance_pct=20.0)
        assert res["checked"] > 0
        assert res["mismatched"] == 0
        assert res["matched"] == res["checked"]

    def test_cross_check_detects_quarter_shift(self):
        ref = self._db_style_periods()
        cand = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        res = CafeFCrawler.cross_check(ref, cand, tolerance_pct=20.0)
        assert res["mismatched"] > 0
        assert res["checked"] > 0

    def test_cross_check_details_have_metric(self):
        ref = self._db_style_periods()
        cand = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        res = CafeFCrawler.cross_check(ref, cand, tolerance_pct=20.0)
        if res["details"]:
            d = res["details"][0]
            assert "metric" in d and "period" in d and "diff_pct" in d

    def test_cross_check_ignores_none(self):
        ref = [{"_fiscal_year": 2026, "_fiscal_quarter": 1, "REVENUE": 100.0}]
        cand = [{"_fiscal_year": 2026, "_fiscal_quarter": 1, "REVENUE": None}]
        res = CafeFCrawler.cross_check(ref, cand)
        assert res["checked"] == 0


class TestUrlMap:
    def test_vietstock_base_url(self):
        assert VIETSTOCK_BASE_URL == "https://finance.vietstock.vn"

    def test_url_map_has_vietstock_entry(self):
        from src.financial.cafef_crawler import URL_MAP
        assert "VIETSTOCK_FININFO" in URL_MAP
        assert URL_MAP["VIETSTOCK_FININFO"]["status"] == "ALIVE"


# ── BCTT Fixtures ──────────────────────────────────────────────────────
def _bctt_periods_desc():
    """9 periods newest first (2026Q2 → 2024Q2), matching live probe."""
    term_map = {2: 'Q1', 3: 'Q2', 4: 'Q3', 5: 'Q4'}
    return [
        {"ReportDataID": 293359, "YearPeriod": 2026, "ReportTermID": 3,
         "PeriodBegin": "202604", "PeriodEnd": "202606", "RowNumber": 38,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 289473, "YearPeriod": 2026, "ReportTermID": 2,
         "PeriodBegin": "202601", "PeriodEnd": "202603", "RowNumber": 37,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 283569, "YearPeriod": 2025, "ReportTermID": 5,
         "PeriodBegin": "202510", "PeriodEnd": "202512", "RowNumber": 36,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 279313, "YearPeriod": 2025, "ReportTermID": 4,
         "PeriodBegin": "202507", "PeriodEnd": "202509", "RowNumber": 35,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 273315, "YearPeriod": 2025, "ReportTermID": 3,
         "PeriodBegin": "202504", "PeriodEnd": "202506", "RowNumber": 34,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 268535, "YearPeriod": 2025, "ReportTermID": 2,
         "PeriodBegin": "202501", "PeriodEnd": "202503", "RowNumber": 33,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 262654, "YearPeriod": 2024, "ReportTermID": 5,
         "PeriodBegin": "202410", "PeriodEnd": "202412", "RowNumber": 32,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 258367, "YearPeriod": 2024, "ReportTermID": 4,
         "PeriodBegin": "202407", "PeriodEnd": "202409", "RowNumber": 31,
         "UnitedName": "Hợp nhất"},
        {"ReportDataID": 252306, "YearPeriod": 2024, "ReportTermID": 3,
         "PeriodBegin": "202404", "PeriodEnd": "202406", "RowNumber": 30,
         "UnitedName": "Hợp nhất"},
    ]


def _bctt_norm_rows():
    """46 norms with sampled values (newest first = Value1)."""
    return [
        # KQ
        {"ReportNormId": 2216, "ReportTypeCode": "KQ",
         "Value1": 1104.96, "Value2": 1376.45, "Value3": 828.38,
         "Value4": 2520.70, "Value5": 1104.96, "Value6": 1376.45,
         "Value7": 828.38, "Value8": 2520.70, "Value9": 1104.96},
        {"ReportNormId": 2207, "ReportTypeCode": "KQ",
         "Value1": 343.0, "Value2": 1632.0, "Value3": 613.0,
         "Value4": 712.0, "Value5": 343.0, "Value6": 1632.0,
         "Value7": 613.0, "Value8": 712.0, "Value9": 343.0},
        {"ReportNormId": 2217, "ReportTypeCode": "KQ",
         "Value1": 682.65, "Value2": 1040.04, "Value3": 509.91,
         "Value4": 1808.98, "Value5": 682.65, "Value6": 1040.04,
         "Value7": 509.91, "Value8": 1808.98, "Value9": 682.65},
        {"ReportNormId": 2208, "ReportTypeCode": "KQ",
         "Value1": 298.10, "Value2": 1291.52, "Value3": 407.91,
         "Value4": 1703.09, "Value5": 298.10, "Value6": 1291.52,
         "Value7": 407.91, "Value8": 1703.09, "Value9": 298.10},
        {"ReportNormId": 2212, "ReportTypeCode": "KQ",
         "Value1": 288.43, "Value2": 1247.08, "Value3": 422.39,
         "Value4": 1467.71, "Value5": 288.43, "Value6": 1247.08,
         "Value7": 422.39, "Value8": 1467.71, "Value9": 288.43},
        {"ReportNormId": 2215, "ReportTypeCode": "KQ",
         "Value1": 3285.0, "Value2": 3361.0, "Value3": 3488.0,
         "Value4": 3419.0, "Value5": 3285.0, "Value6": 3361.0,
         "Value7": 3488.0, "Value8": 3419.0, "Value9": 3285.0},
        # CD
        {"ReportNormId": 3000, "ReportTypeCode": "CD",
         "Value1": 31731.74, "Value2": 31384.55, "Value3": 31593.94,
         "Value4": 31456.65, "Value5": 31731.74, "Value6": 31384.55,
         "Value7": 31593.94, "Value8": 31456.65, "Value9": 31731.74},
        {"ReportNormId": 3003, "ReportTypeCode": "CD",
         "Value1": 15000.0, "Value2": 14500.0, "Value3": 13800.0,
         "Value4": 12500.0, "Value5": 15000.0, "Value6": 14500.0,
         "Value7": 13800.0, "Value8": 12500.0, "Value9": 15000.0},
        {"ReportNormId": 3005, "ReportTypeCode": "CD",
         "Value1": 8500.0, "Value2": 9200.0, "Value3": 7800.0,
         "Value4": 6500.0, "Value5": 8500.0, "Value6": 9200.0,
         "Value7": 7800.0, "Value8": 6500.0, "Value9": 8500.0},
        {"ReportNormId": 3006, "ReportTypeCode": "CD",
         "Value1": 5200.0, "Value2": 5800.0, "Value3": 4900.0,
         "Value4": 4200.0, "Value5": 5200.0, "Value6": 5800.0,
         "Value7": 4900.0, "Value8": 4200.0, "Value9": 5200.0},
        {"ReportNormId": 2996, "ReportTypeCode": "CD",
         "Value1": 61310.09, "Value2": 60908.21, "Value3": 58182.02,
         "Value4": 57290.94, "Value5": 61310.09, "Value6": 60908.21,
         "Value7": 58182.02, "Value8": 57290.94, "Value9": 61310.09},
        {"ReportNormId": 2997, "ReportTypeCode": "CD",
         "Value1": 38832.22, "Value2": 37590.84, "Value3": 36094.28,
         "Value4": 35878.35, "Value5": 38832.22, "Value6": 37590.84,
         "Value7": 36094.28, "Value8": 35878.35, "Value9": 38832.22},
        {"ReportNormId": 3014, "ReportTypeCode": "CD",
         "Value1": 19756.63, "Value2": 21678.29, "Value3": 20052.00,
         "Value4": 22469.54, "Value5": 19756.63, "Value6": 21678.29,
         "Value7": 20052.00, "Value8": 22469.54, "Value9": 19756.63},
        {"ReportNormId": 3017, "ReportTypeCode": "CD",
         "Value1": 8500.0, "Value2": 8200.0, "Value3": 7800.0,
         "Value4": 7500.0, "Value5": 8500.0, "Value6": 8200.0,
         "Value7": 7800.0, "Value8": 7500.0, "Value9": 8500.0},
        {"ReportNormId": 2998, "ReportTypeCode": "CD",
         "Value1": 22477.88, "Value2": 23317.36, "Value3": 22087.74,
         "Value4": 21412.59, "Value5": 22477.88, "Value6": 23317.36,
         "Value7": 22087.74, "Value8": 21412.59, "Value9": 22477.88},
        # CSTC
        {"ReportNormId": 54, "ReportTypeCode": "CSTC",
         "Value1": 21718.0, "Value2": 22529.0, "Value3": 21341.0,
         "Value4": 20688.0, "Value5": 21718.0, "Value6": 22529.0,
         "Value7": 21341.0, "Value8": 20688.0, "Value9": 21718.0},
    ]


class TestBCTTMetricMap:
    """Test BCTT_METRIC_MAP and BCTT_STANDARD_METRICS constants."""

    def test_bctt_has_cash_equiv(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 3003 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[3003] == "CASH_EQUIV"

    def test_bctt_has_receivables(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 3005 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[3005] == "RECEIVABLES"

    def test_bctt_has_inventory(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 3006 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[3006] == "INVENTORY"

    def test_bctt_has_long_term_debt(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 3017 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[3017] == "LONG_TERM_DEBT"

    def test_bctt_has_cogs(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 2207 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[2207] == "COGS"

    def test_bctt_has_revenue_and_ebit(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 2216 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[2216] == "REVENUE"
        assert 2208 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[2208] == "EBIT"

    def test_bctt_has_net_income(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 2212 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[2212] == "NET_INCOME"

    def test_bctt_has_eps_and_book_value(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 2215 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[2215] == "EPS"
        assert 54 in BCTT_STANDARD_METRICS
        assert BCTT_STANDARD_METRICS[54] == "BOOK_VALUE_PS"

    def test_bctt_has_balance_sheet(self):
        from src.financial.vietstock_crawler import BCTT_STANDARD_METRICS
        assert 3000 in BCTT_STANDARD_METRICS  # CURRENT_ASSETS
        assert 2996 in BCTT_STANDARD_METRICS  # TOTAL_ASSETS
        assert 2997 in BCTT_STANDARD_METRICS  # TOTAL_LIABILITIES
        assert 3014 in BCTT_STANDARD_METRICS  # CURRENT_LIAB
        assert 2998 in BCTT_STANDARD_METRICS  # TOTAL_EQUITY


class TestParseBCTTDetailedPayload:
    """Test BCTT detail payload parser — deterministic, testable."""

    def test_parses_9_periods(self):
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        assert len(result) == 9

    def test_period_order_newest_first(self):
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        assert result[0]["_fiscal_year"] == 2026
        assert result[0]["_fiscal_quarter"] == 2
        assert result[-1]["_fiscal_year"] == 2024
        assert result[-1]["_fiscal_quarter"] == 2

    def test_value1_maps_to_newest_period(self):
        """Value1 ↔ period_desc[0] (newest 2026Q2)."""
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        newest = result[0]
        assert newest["REVENUE"] == 1104.96
        assert newest["CASH_EQUIV"] == 15000.0
        assert newest["RECEIVABLES"] == 8500.0
        assert newest["INVENTORY"] == 5200.0
        assert newest["LONG_TERM_DEBT"] == 8500.0

    def test_value9_maps_to_oldest_period(self):
        """Value9 ↔ period_desc[8] (oldest 2024Q2)."""
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        oldest = result[-1]
        assert oldest["REVENUE"] == 1104.96  # same as Value9 in fixture
        assert oldest["CASH_EQUIV"] == 15000.0
        assert oldest["RECEIVABLES"] == 8500.0

    def test_reports_16_standard_metrics_per_period(self):
        """Each period should have 16 STANDARD_METRICS mapped from BCTT norms."""
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        for p in result:
            metrics = {k: v for k, v in p.items() if not k.startswith("_")}
            assert len(metrics) == 16

    def test_entity_type_set(self):
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(
            norm_rows, periods, entity_type="STANDARD")
        assert all(p["_entity_type"] == "STANDARD" for p in result)

    def test_skips_non_standard_metrics(self):
        """Non-STANDARD_METRICS (FINANCIAL_REVENUE, etc.) should not appear."""
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, periods)
        for p in result:
            assert "FINANCIAL_REVENUE" not in p
            assert "FINANCIAL_COST" not in p
            assert "SELLING_EXPENSE" not in p
            assert "ADMIN_EXPENSE" not in p
            assert "OTHER_INCOME" not in p
            assert "JOINT_VENTURE_INCOME" not in p
            assert "PRE_TAX_INCOME" not in p
            assert "NET_INCOME_PARENT" not in p
            assert "SHORT_TERM_INVEST" not in p
            assert "SHORT_TERM_ASSET_OTHER" not in p
            assert "LONG_TERM_ASSET" not in p
            assert "FIXED_ASSET" not in p
            assert "INVESTMENT_REAL_ESTATE" not in p
            assert "PAID_IN_CAPITAL" not in p
            assert "SHARE_PREMIUM" not in p
            assert "UNALLOCATED_EARNINGS" not in p
            assert "MINORITY_INTEREST" not in p
            assert "TOTAL_SOURCE" not in p
            assert "PE_RATIO" not in p
            assert "PB_RATIO" not in p
            assert "GROSS_MARGIN" not in p
            assert "NET_MARGIN" not in p
            assert "ROEA" not in p
            assert "ROAA" not in p
            assert "CURRENT_RATIO" not in p
            assert "INTEREST_COVERAGE" not in p
            assert "DEBT_TO_ASSET" not in p
            assert "DEBT_TO_EQUITY" not in p

    def test_empty_norm_rows_returns_empty(self):
        periods = _bctt_periods_desc()
        assert VietstockCrawler.parse_bctt_detail_payload([], periods) == []

    def test_empty_periods_returns_empty(self):
        norm_rows = _bctt_norm_rows()
        assert VietstockCrawler.parse_bctt_detail_payload(norm_rows, []) == []

    def test_empty_both_returns_empty(self):
        assert VietstockCrawler.parse_bctt_detail_payload([], []) == []

    def test_invalid_period_term_skipped(self):
        """Periods with invalid ReportTermID should be skipped."""
        bad_periods = [
            {"ReportDataID": 1, "YearPeriod": 2026, "ReportTermID": 99,
             "PeriodBegin": "202601", "PeriodEnd": "202603", "RowNumber": 1},
        ]
        norm_rows = _bctt_norm_rows()
        result = VietstockCrawler.parse_bctt_detail_payload(norm_rows, bad_periods)
        assert len(result) == 0

    def test_roundtrip_through_json(self):
        periods = _bctt_periods_desc()
        norm_rows = _bctt_norm_rows()
        raw = json.dumps(norm_rows)
        parsed_norms = json.loads(raw)
        result = VietstockCrawler.parse_bctt_detail_payload(parsed_norms, periods)
        assert len(result) == 9
        assert result[0]["TOTAL_ASSETS"] == 61310.09


class TestMergePeriods:
    """Test _merge_periods — BCTT overrides financeinfo for same period."""

    def test_bctt_overrides_financeinfo(self):
        finf = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        bctt_periods = _bctt_periods_desc()
        bctt_norm_rows = _bctt_norm_rows()
        bctt = VietstockCrawler.parse_bctt_detail_payload(bctt_norm_rows, bctt_periods)
        # Overlap: 2025Q1, 2025Q2 exist in both
        merged = VietstockCrawler._merge_periods(finf, bctt)
        # Should have 9 periods (BCTT dominates)
        assert len(merged) == 9
        # 2025Q2 should have BCTT metrics (CASH_EQUIV, etc.)
        q2_2025 = [p for p in merged if p["_fiscal_year"] == 2025 and p["_fiscal_quarter"] == 2]
        assert len(q2_2025) == 1
        assert "CASH_EQUIV" in q2_2025[0]
        assert "RECEIVABLES" in q2_2025[0]
        assert "INVENTORY" in q2_2025[0]
        assert "LONG_TERM_DEBT" in q2_2025[0]
        assert "COGS" in q2_2025[0]

    def test_financeinfo_only_periods_kept(self):
        """Periods only in financeinfo (not in BCTT) should still appear."""
        finf = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        # BCTT only has 2024Q2-2026Q2, financeinfo has 2025Q1-2026Q1
        # Overlap: 2025Q1, 2026Q1
        bctt_periods = _bctt_periods_desc()
        bctt_norm_rows = _bctt_norm_rows()
        bctt = VietstockCrawler.parse_bctt_detail_payload(bctt_norm_rows, bctt_periods)
        merged = VietstockCrawler._merge_periods(finf, bctt)
        # Should have 9 periods total (BCTT has 9, financeinfo has 4, overlap 2)
        # BCTT covers 2024Q2-2026Q2 = 9 periods
        # financeinfo has 2025Q1-2026Q1 = 4 periods (all within BCTT range)
        assert len(merged) == 9

    def test_merged_sorted_by_year_quarter(self):
        finf = VietstockCrawler.parse_financeinfo_payload(_real_payload())
        bctt_periods = _bctt_periods_desc()
        bctt_norm_rows = _bctt_norm_rows()
        bctt = VietstockCrawler.parse_bctt_detail_payload(bctt_norm_rows, bctt_periods)
        merged = VietstockCrawler._merge_periods(finf, bctt)
        for i in range(len(merged) - 1):
            a, b = merged[i], merged[i + 1]
            assert (a["_fiscal_year"], a["_fiscal_quarter"]) <= \
                   (b["_fiscal_year"], b["_fiscal_quarter"])
