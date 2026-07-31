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
