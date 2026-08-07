"""TDD tests for entity_registry_audit (Batch Audit ICB → entity_type).

Kiến trúc 2 tầng (2026-08-07):
  TẦNG 1 — Khung Kế toán: BANK / SECURITIES / INSURANCE / STANDARD.
  TẦNG 2 — Tham số Ngành ICB: policy rules (Thép 15%, BĐS D/E...) bên trong
           nhóm STANDARD — không đổi cấu trúc BCTC.

Key regressions khóa tại đây:
  1. map_icb_to_entity_type: keyword "môi giới" KHÔNG được match SECURITIES
     (tránh lẫn "Môi giới Bất động sản" BVL/DCH/DXS vào nhóm chứng khoán).
  2. Ngành sản xuất (Thép HPG), BĐS (VHM), Bán lẻ (MWG) → STANDARD, KHÔNG bao giờ
     bị đổi nhãn kế toán.
  3. audit_registry áp dụng toàn bộ universe qua INSERT OR REPLACE (idempotent).
"""

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))

from entity_registry_audit import audit_registry, map_icb_to_entity_type

FINANCIAL_DB = PROJECT_ROOT / "backend" / "data" / "financial_facts.db"
SCREENER_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"


# ── Pure function: ICB → entity_type ────────────────────────────────────────


class TestMapIcbToEntityType:
    def test_bank_sector_maps_bank(self):
        assert map_icb_to_entity_type("Ngân hàng", "Ngân hàng", "Ngân hàng") == "BANK"

    def test_insurance_sector_maps_insurance(self):
        assert map_icb_to_entity_type("Bảo hiểm", "Bảo hiểm phi nhân thọ", "Bảo hiểm phi nhân thọ") == "INSURANCE"

    def test_securities_broker_maps_securities(self):
        assert map_icb_to_entity_type("Dịch vụ tài chính", "Dịch vụ tài chính", "Môi giới chứng khoán") == "SECURITIES"

    def test_real_estate_broker_NOT_securities(self):
        """Regression 2026-08-07: "Môi giới" đơn lẻ lẫn BĐS vào chứng khoán."""
        assert map_icb_to_entity_type("Bất động sản", "Bất động sản", "Tư Vấn, Định giá, Môi giới Bất động sản") == "STANDARD"

    def test_steel_maps_standard(self):
        """Thép (HPG) thuộc STANDARD — BCTC sản xuất chuẩn, không đổi nhãn."""
        assert map_icb_to_entity_type("Tài nguyên Cơ bản", "Kim loại", "Thép và sản phẩm thép") == "STANDARD"

    def test_real_estate_dev_maps_standard(self):
        assert map_icb_to_entity_type("Bất động sản", "Bất động sản", "Bất động sản") == "STANDARD"

    def test_retail_maps_standard(self):
        assert map_icb_to_entity_type("Bán lẻ", "Bán lẻ", "Bán lẻ phức hợp") == "STANDARD"

    def test_empty_names_default_standard(self):
        assert map_icb_to_entity_type("", "", "") == "STANDARD"


# ── Thực thi audit trên DB thật ─────────────────────────────────────────────


@pytest.mark.skipif(
    not SCREENER_DB.exists() or not FINANCIAL_DB.exists(),
    reason="Cần screener_cache.db + financial_facts.db thật",
)
class TestAuditRegistryLive:
    def test_live_registry_has_no_mixed_financial_sectors(self):
        """Mọi symbol gán nhãn tài chính phải thuộc đúng 3 ngành ICB tài chính."""
        sc = sqlite3.connect(str(SCREENER_DB))
        sc.row_factory = sqlite3.Row
        fin = sqlite3.connect(str(FINANCIAL_DB))
        fin.row_factory = sqlite3.Row

        sectors = {}
        for r in sc.execute("SELECT symbol, icb_name2 FROM symbol_industry").fetchall():
            sectors[r["symbol"].upper()] = r["icb_name2"]

        for r in fin.execute(
            "SELECT symbol, entity_type FROM entity_registry WHERE entity_type IN ('BANK','SECURITIES','INSURANCE')"
        ).fetchall():
            assert r["symbol"] in sectors, f"{r['symbol']} không có trong symbol_industry"
            assert sectors[r["symbol"]] in ("Ngân hàng", "Dịch vụ tài chính", "Bảo hiểm"), (
                f"{r['symbol']} nhãn {r['entity_type']} nhưng thuộc ngành {sectors[r['symbol']]}"
            )
        sc.close()
        fin.close()

    def test_live_all_banks_are_bank(self):
        fin = sqlite3.connect(str(FINANCIAL_DB))
        fin.row_factory = sqlite3.Row
        banks = [r["symbol"] for r in fin.execute("SELECT symbol FROM entity_registry WHERE entity_type='BANK'").fetchall()]
        assert "VCB" in banks and "ACB" in banks and "BID" in banks
        fin.close()

    def test_live_ssi_vnd_are_securities(self):
        fin = sqlite3.connect(str(FINANCIAL_DB))
        fin.row_factory = sqlite3.Row
        sec = {
            r["symbol"] for r in fin.execute("SELECT symbol FROM entity_registry WHERE entity_type='SECURITIES'").fetchall()
        }
        assert {"SSI", "VND", "HCM", "VCI"} <= sec
        fin.close()

    def test_audit_is_idempotent(self):
        """Chạy audit_registry (dry) trên DB thật không đổi kết quả phân bố."""
        before = audit_registry(apply=False)
        again = audit_registry(apply=False)
        assert before.updated == {} or again.updated == {}
