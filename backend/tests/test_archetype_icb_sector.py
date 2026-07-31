"""test_archetype_icb_sector.py — Regression tests cho ICB sector constraint
trong ArchetypeEngine._classify_by_ratios().

WHY (bug từng xảy ra):
  - SIP, IDC, KBC (KCN điển hình) chưa có health_ratios rows trong
    financial_facts.db → _classify_by_ratios rơi vào toàn bộ defaults
    (rev=0.5, de=0.5, cfo=0.5) → cfo_val > 0.4 → gán RETAIL_PLATFORM sai.
  - Hệ quả: hci-explain dùng chain SAME_STORE_SALES (5-30D) thay vì PRESALES
    (30-120D) → HCI 0.35 REDUCE thay vì VETO như BCM.
  - Fix: hard constraint đọc ICB sector (icb_name2 == "Bất động sản") từ
    screener_cache.db → REAL_ESTATE_DEVELOPER, giống _is_bank_symbol.

Run:  python -m pytest tests/test_archetype_icb_sector.py -q   (từ backend/)
"""

import pytest


@pytest.fixture
def engine():
    from src.business.archetype import ArchetypeEngine
    eng = ArchetypeEngine()
    yield eng
    eng.close()


class TestICBSectorConstraint:
    def test_kcn_symbols_classify_real_estate_developer(self, engine):
        """KCN điển hình (ICB = Bất động sản, không có ratios) → REAL_ESTATE_DEVELOPER."""
        for sym in ("SIP", "IDC", "KBC"):
            assert engine.classify(sym).archetype == "REAL_ESTATE_DEVELOPER", sym

    def test_large_real_estate_developers_stable(self, engine):
        """BCM/VHM giữ nguyên dù đã có trong BASELINE_MAP."""
        assert engine.classify("BCM").archetype == "REAL_ESTATE_DEVELOPER"
        assert engine.classify("VHM").archetype == "REAL_ESTATE_DEVELOPER"

    def test_non_real_estate_unchanged(self, engine):
        """Non-BĐS symbols KHÔNG bị đổi archetype."""
        assert engine.classify("MWG").archetype == "RETAIL_PLATFORM"
        assert engine.classify("FPT").archetype == "COMPOUNDER"
        assert engine.classify("SSI").archetype == "RETAIL_PLATFORM"

    def test_banks_still_franchise_or_asset(self, engine):
        """Ngân hàng vẫn ra nhánh bank (không bị REAL_ESTATE_DEVELOPER chặn)."""
        assert engine.classify("VCB").archetype in ("FRANCHISE_BANK", "ASSET_BANK")
        assert engine.classify("ACB").archetype in ("FRANCHISE_BANK", "ASSET_BANK")


class TestICBSectorHelper:
    def test_icb_sector_resolves(self, engine):
        assert engine._icb_sector("SIP") == "Bất động sản"
        assert engine._icb_sector("BCM") == "Bất động sản"
        assert engine._icb_sector("MWG") == "Bán lẻ"

    def test_icb_sector_unknown_returns_none(self, engine):
        assert engine._icb_sector("ZZZZ") is None
