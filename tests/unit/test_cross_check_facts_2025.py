"""TDD tests - cross_check_facts_2025 (đối chiếu BCTC năm giữa financial_facts.db vs vnfinancialdata).

WHY: vnfinancialdata phủ dữ liệu NĂM (2025). financial_facts.db lưu theo quý.
Cross-check: sum 4 quý (REVENUE/NET_INCOME) hoặc Q4 (TOTAL_EQUITY) rồi so với
annual. Zero-Hallucination: thiếu quý / thiếu item / mã vắng mặt -> NO_DATA,
KHÔNG bịa số. Item map theo normalized item_name (không dựa item_code — thay đổi
giữa các entity cùng khái niệm).
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.tools.cross_check_facts_2025 import (
    ENTITY_METRIC_MAP,
    KNOWN_DATA_GAPS,
    METRIC_SEMANTICS,
    _normalize_item_name,
    build_cross_check,
    load_ff_annual,
    load_vnf_annual,
)


# ===================================================================
# TEST 1: normalize item name (bỏ hoa, dấu, khoảng trắng, ký tự đặc biệt)
# ===================================================================
class TestNormalize:
    def test_same_concept_different_case(self):
        assert _normalize_item_name("VỐN CHỦ SỞ HỮU") == _normalize_item_name(
            "Vốn chủ sở hữu"
        )

    def test_strips_punctuation(self):
        assert _normalize_item_name("Lãi/(lỗ) thuần sau thuế") == _normalize_item_name(
            "Lãi lỗ thuần sau thuế"
        )

    def test_basic(self):
        assert _normalize_item_name("Doanh số thuần") == "doanh so thuan"


# ===================================================================
# TEST 2: entity metric map (không đổi nhãn theo ngành)
# ===================================================================
class TestEntityMap:
    def test_four_entities(self):
        assert set(ENTITY_METRIC_MAP) == {"BANK", "SECURITIES", "INSURANCE", "STANDARD"}

    def test_standard_has_revenue(self):
        assert "REVENUE" in ENTITY_METRIC_MAP["STANDARD"]

    def test_bank_no_revenue(self):
        # Ngân hàng không có "Doanh số thuần" chuẩn — không map revenue
        assert "REVENUE" not in ENTITY_METRIC_MAP["BANK"]

    def test_semantics_flow_vs_stock(self):
        assert METRIC_SEMANTICS["REVENUE"] == "flow"
        assert METRIC_SEMANTICS["NET_INCOME"] == "flow"
        assert METRIC_SEMANTICS["TOTAL_EQUITY"] == "stock"


# ===================================================================
# TEST 3: load_vnf_annual từ parquet staging (mock pandas để deterministic)
# ===================================================================
class TestLoadVnfAnnual:
    def _make_df(self):
        rows = [
            ("HPG", 2025, "Doanh số thuần", 100_000_000_000.0),
            ("HPG", 2025, "Lãi/(lỗ) thuần sau thuế", 10_000_000_000.0),
            ("HPG", 2025, "VỐN CHỦ SỞ HỮU", 90_000_000_000.0),
            ("ACB", 2025, "Lợi nhuận sau thuế", 15_000_000_000.0),
            ("ACB", 2025, "Vốn chủ sở hữu", 80_000_000_000.0),
            ("SSI", 2025, "Lợi nhuận kế toán sau thuế", 2_000_000_000.0),
            ("SSI", 2024, "Lợi nhuận kế toán sau thuế", 1_000_000_000.0),
        ]
        return pd.DataFrame(rows, columns=["ticker", "year", "item_name", "value"])

    def test_maps_items_by_name(self, tmp_path):
        inc = self._make_df().copy()
        bs = self._make_df().copy()
        (tmp_path / "data" / "income_statement").mkdir(parents=True)
        (tmp_path / "data" / "balance_sheet").mkdir(parents=True)
        inc.to_parquet(tmp_path / "data" / "income_statement" / "HSX.parquet")
        bs.to_parquet(tmp_path / "data" / "balance_sheet" / "HSX.parquet")

        with patch(
            "pandas.read_parquet", return_value=pd.concat([inc, bs], ignore_index=True)
        ):
            data = load_vnf_annual(tmp_path, year=2025)

        # STANDARD entity HPG
        assert data["HPG"]["REVENUE"] == pytest.approx(100_000_000_000.0)
        assert data["HPG"]["NET_INCOME"] == pytest.approx(10_000_000_000.0)
        assert data["HPG"]["TOTAL_EQUITY"] == pytest.approx(90_000_000_000.0)
        # BANK entity ACB
        assert data["ACB"]["NET_INCOME"] == pytest.approx(15_000_000_000.0)
        assert data["ACB"]["TOTAL_EQUITY"] == pytest.approx(80_000_000_000.0)
        assert "REVENUE" not in data["ACB"]
        # SECURITIES entity SSI: map NET_INCOME, không có revenue/equity trong seed
        assert data["SSI"]["NET_INCOME"] == pytest.approx(2_000_000_000.0)
        assert set(data["SSI"]) == {"NET_INCOME"}

    def test_missing_parquet_returns_empty(self, tmp_path):
        with patch("pandas.read_parquet", side_effect=FileNotFoundError):
            assert load_vnf_annual(tmp_path, year=2025) == {}


# ===================================================================
# TEST 4: load_ff_annual từ financial_facts.db (temp sqlite)
# ===================================================================
class TestLoadFFAnnual:
    def _make_db(self, path: Path):
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE financial_facts (symbol TEXT, period TEXT, fiscal_year INTEGER, "
            "fiscal_quarter INTEGER, statement_type TEXT, metric TEXT, value REAL, "
            "unit TEXT, source TEXT, reported_at TEXT, ingested_at TEXT, "
            "integrity_flags TEXT, is_synthetic INTEGER)"
        )
        ff = conn.execute  # alias
        # HPG: đủ 4 quý REVENUE + NET_INCOME + TOTAL_EQUITY
        for q, rv, ni, eq in [
            (1, 10.0, 1.0, 80.0),
            (2, 20.0, 2.0, 85.0),
            (3, 30.0, 3.0, 88.0),
            (4, 40.0, 4.0, 90.0),
        ]:
            ff(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "HPG",
                    f"2025Q{q}",
                    2025,
                    q,
                    "IS",
                    "REVENUE",
                    rv,
                    "VND",
                    "vnstock",
                    None,
                    None,
                    None,
                    0,
                ),
            )
            ff(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "HPG",
                    f"2025Q{q}",
                    2025,
                    q,
                    "IS",
                    "NET_INCOME",
                    ni,
                    "VND",
                    "vnstock",
                    None,
                    None,
                    None,
                    0,
                ),
            )
            ff(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "HPG",
                    f"2025Q{q}",
                    2025,
                    q,
                    "BS",
                    "TOTAL_EQUITY",
                    eq,
                    "VND",
                    "vnstock",
                    None,
                    None,
                    None,
                    0,
                ),
            )
        # ACB: chỉ 2 quý NET_INCOME -> flow không sum được
        for q, ni, eq in [(1, 4.0, 70.0), (2, 5.0, 75.0)]:
            ff(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "ACB",
                    f"2025Q{q}",
                    2025,
                    q,
                    "IS",
                    "NET_INCOME",
                    ni,
                    "VND",
                    "vnstock",
                    None,
                    None,
                    None,
                    0,
                ),
            )
            ff(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "ACB",
                    f"2025Q{q}",
                    2025,
                    q,
                    "BS",
                    "TOTAL_EQUITY",
                    eq,
                    "VND",
                    "vnstock",
                    None,
                    None,
                    None,
                    0,
                ),
            )
        conn.commit()
        conn.close()

    def test_sums_flow_and_takes_q4_stock(self, tmp_path):
        db = tmp_path / "ff.db"
        self._make_db(db)
        data = load_ff_annual(db, year=2025)
        # HPG đủ 4 quý
        assert data["HPG"]["REVENUE"]["value"] == pytest.approx(100.0)
        assert data["HPG"]["REVENUE"]["quarters"] == 4
        assert data["HPG"]["NET_INCOME"]["value"] == pytest.approx(10.0)
        assert data["HPG"]["TOTAL_EQUITY"]["value"] == pytest.approx(90.0)
        assert data["HPG"]["TOTAL_EQUITY"]["quarters"] == 4
        # ACB thiếu quý -> flow NO_DATA, stock lấy quý cao nhất có
        assert data["ACB"]["NET_INCOME"]["quarters"] == 2
        assert data["ACB"]["NET_INCOME"]["value"] is None
        assert data["ACB"]["TOTAL_EQUITY"]["quarters"] == 2
        assert data["ACB"]["TOTAL_EQUITY"]["value"] == pytest.approx(75.0)

    def test_missing_db_returns_empty(self, tmp_path):
        assert load_ff_annual(tmp_path / "nope.db", year=2025) == {}


# ===================================================================
# TEST 5: build_cross_check (fail-closed, không bịa số)
# ===================================================================
class TestBuildCrossCheck:
    def _seed(self):
        ff = {
            "HPG": {
                "REVENUE": {"value": 100.0, "quarters": 4},
                "NET_INCOME": {"value": 10.0, "quarters": 4},
                "TOTAL_EQUITY": {"value": 90.0, "quarters": 4},
            },
            "ACB": {
                "NET_INCOME": {"value": None, "quarters": 2},
                "TOTAL_EQUITY": {"value": 75.0, "quarters": 2},
            },
            "SSI": {"NET_INCOME": {"value": 2.0, "quarters": 4}},
        }
        vnf = {
            "HPG": {"REVENUE": 101.0, "NET_INCOME": 10.5, "TOTAL_EQUITY": 89.0},
            "ACB": {"NET_INCOME": 12.0, "TOTAL_EQUITY": 80.0},
        }
        entities = {"HPG": "STANDARD", "ACB": "BANK", "SSI": "SECURITIES"}
        return ff, vnf, entities

    def test_match_within_threshold(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        hpg = [r for r in rows if r["symbol"] == "HPG"]
        assert {r["metric"] for r in hpg} == {"REVENUE", "NET_INCOME", "TOTAL_EQUITY"}
        for r in hpg:
            assert r["status"] == "MATCH"
            assert r["delta_pct"] <= 5.0

    def test_mismatch_when_delta_over_threshold(self):
        ff, vnf, ent = self._seed()
        vnf["HPG"]["REVENUE"] = 120.0  # +20%
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        rev = [r for r in rows if r["symbol"] == "HPG" and r["metric"] == "REVENUE"][0]
        assert rev["status"] == "MISMATCH"
        assert rev["delta_pct"] > 5.0

    def test_no_data_when_ff_quarters_incomplete(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        acb_ni = [
            r for r in rows if r["symbol"] == "ACB" and r["metric"] == "NET_INCOME"
        ][0]
        assert acb_ni["status"] == "NO_DATA"
        assert acb_ni["reason"] == "ff_quarters_incomplete"

    def test_no_data_when_entity_metric_not_mapped(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        acb_rev = [r for r in rows if r["symbol"] == "ACB" and r["metric"] == "REVENUE"]
        assert acb_rev == []  # BANK không map REVENUE

    def test_no_data_when_vnf_missing_symbol(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        ssi = [r for r in rows if r["symbol"] == "SSI"]
        assert ssi[0]["status"] == "NO_DATA"
        assert ssi[0]["reason"] == "vnf_missing_symbol"

    def test_no_data_when_vnf_missing_metric(self):
        ff, vnf, ent = self._seed()
        del vnf["HPG"]["REVENUE"]
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        hpg_rev = [
            r for r in rows if r["symbol"] == "HPG" and r["metric"] == "REVENUE"
        ][0]
        assert hpg_rev["status"] == "NO_DATA"
        assert hpg_rev["reason"] == "vnf_missing_metric"

    def test_zero_ff_denominator_no_div_zero(self):
        ff, vnf, ent = self._seed()
        ff["HPG"]["REVENUE"]["value"] = 0.0
        vnf["HPG"]["REVENUE"] = 0.0
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        hpg_rev = [
            r for r in rows if r["symbol"] == "HPG" and r["metric"] == "REVENUE"
        ][0]
        assert hpg_rev["delta_pct"] is None
        assert hpg_rev["status"] == "MATCH"


# ===================================================================
# TEST 6: KNOWN_DATA_GAPS — MISMATCH đã chứng minh được phân loại riêng
# ===================================================================
class TestKnownDataGaps:
    """MISMATCH thuộc KNOWN_DATA_GAPS phải chuyển thành KNOWN_GAP_EXPLAINED.

    WHY: Zero-Hallucination — MISMATCH còn lại (sau khi đã loại các ca đã chứng
    minh nguyên nhân) phải phản ánh đúng lỗi thật cần audit. Các ca đã chứng minh:
      - MBB NET_INCOME: vnf lấy LNST Cổ đông mẹ (27.38T) vs DB dùng LNST Hợp nhất
        (28.96T) — định nghĩa kế toán khác nhau, không phải lỗi số liệu.
    Ghi chú thêm về nguồn dữ liệu: MBB crawl từ vnstock (VCI hợp nhất) + cafef.
    """

    def _seed(self):
        ff = {
            "MBB": {"NET_INCOME": {"value": 28_957_668_000_000.0, "quarters": 4}},
            "HPG": {"REVENUE": {"value": 100.0, "quarters": 4}},
        }
        vnf = {
            "MBB": {"NET_INCOME": 27_382_978_000_000.0},
            "HPG": {"REVENUE": 120.0},  # +20% -> MISMATCH thật
        }
        entities = {"MBB": "BANK", "HPG": "STANDARD"}
        return ff, vnf, entities

    def test_gap_in_registry_is_known_gap_explained(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        mbb = [r for r in rows if r["symbol"] == "MBB"][0]
        assert mbb["status"] == "KNOWN_GAP_EXPLAINED"
        assert mbb["reason"].startswith("ACCOUNTING_DEFINITION_VARIANCE")

    def test_registry_contains_only_verified_gaps(self):
        assert KNOWN_DATA_GAPS
        for (symbol, metric), (code, note) in KNOWN_DATA_GAPS.items():
            assert code in (
                "GROUND_TRUTH_INCOMPLETE",
                "ACCOUNTING_DEFINITION_VARIANCE",
            )
            assert note

    def test_mismatch_without_gap_stays_mismatch(self):
        ff, vnf, ent = self._seed()
        rows = build_cross_check(ff, vnf, ent, year=2025, threshold=0.05)
        hpg = [
            r
            for r in rows
            if r["symbol"] == "HPG" and r["metric"] == "REVENUE"
        ][0]
        assert hpg["status"] == "MISMATCH"
