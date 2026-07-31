"""test_reit_commercial.py — Regression tests cho archetype REIT_COMMERCIAL (VRE).

WHY (lỗi lệch vệt nguyên nhân cần sửa):
  - VRE (Vincom Retail, ~95.5% doanh thu từ cho thuê TTTM) bị ICB hard
    constraint (icb_name2 == "Bất động sản") gán REAL_ESTATE_DEVELOPER →
    chain PRESALES (30-120D) → CSI ~0.23 VETO sai.
  - Fix: archetype REIT_COMMERCIAL (cho thuê BĐS thương mại) + BASELINE_MAP
    override + chain INTEREST_RATE → RENTAL_YIELD (15-40D) + cạnh
    CONSUMER_SPENDING → OCCUPANCY (10-30D).
  - QUAN TRỌNG: KHÔNG được đổi ARCHETYPE_TARGET_NODE[RETAIL_PLATFORM],
    vì sẽ contaminate MWG/PNJ/SSI (shared archetype, trace_path filter theo archetype).

Run:  python -m pytest tests/test_reit_commercial.py -q   (từ backend/)
"""

import pytest


@pytest.fixture
def engine():
    from src.business.archetype import ArchetypeEngine
    eng = ArchetypeEngine()
    yield eng
    eng.close()


class TestVREOverride:
    def test_vre_classifies_reit_commercial(self, engine):
        """VRE → REIT_COMMERCIAL (BASELINE_MAP override ICB Bất động sản)."""
        assert engine.classify("VRE").archetype == "REIT_COMMERCIAL"

    def test_vre_lowercase(self, engine):
        assert engine.classify("vre").archetype == "REIT_COMMERCIAL"

    def test_retail_platform_unchanged(self, engine):
        """MWG/PNJ/SSI giữ RETAIL_PLATFORM — không bị REIT_COMMERCIAL đụng tới."""
        assert engine.classify("MWG").archetype == "RETAIL_PLATFORM"
        assert engine.classify("SSI").archetype == "RETAIL_PLATFORM"

    def test_real_estate_developers_unchanged(self, engine):
        """BCM/VHM/SIP vẫn REAL_ESTATE_DEVELOPER."""
        assert engine.classify("BCM").archetype == "REAL_ESTATE_DEVELOPER"
        assert engine.classify("VHM").archetype == "REAL_ESTATE_DEVELOPER"
        assert engine.classify("SIP").archetype == "REAL_ESTATE_DEVELOPER"


class TestREITChain:
    def test_propagation_chain_registered(self):
        from src.business.economic_engine import PROPAGATION_CHAINS
        chain = PROPAGATION_CHAINS["REIT_COMMERCIAL"]
        assert [c.name for c in chain.chain] == ["OCCUPANCY", "RENTAL_YIELD", "LEASE_REVENUE"]
        assert chain.macro_links["INTEREST_RATE"] == "RENTAL_YIELD"
        assert chain.macro_links["CONSUMER_SPENDING"] == "OCCUPANCY"

    def test_components_registered(self):
        from src.business.economic_engine import C
        assert "RENTAL_YIELD" in C
        assert "OCCUPANCY" in C
        assert "LEASE_REVENUE" in C
        assert C["RENTAL_YIELD"].is_leading
        assert C["OCCUPANCY"].is_leading


class TestCausalEdges:
    def test_interest_rate_to_rental_yield(self):
        """INTEREST_RATE → RENTAL_YIELD (lag 15-40D, conf 0.70) cho REIT_COMMERCIAL."""
        from src.calibration.causal_edge import CausalGraph
        cg = CausalGraph()
        path = cg.trace_path("INTEREST_RATE", "RENTAL_YIELD", "REIT_COMMERCIAL")
        assert path is not None
        hop = path[-1]
        assert hop["lag_min"] == 15
        assert hop["lag_max"] == 40
        assert hop["confidence"] == pytest.approx(0.70)

    def test_consumer_spending_to_occupancy(self):
        """CONSUMER_SPENDING → OCCUPANCY (lag 10-30D, conf 0.65)."""
        from src.calibration.causal_edge import CausalGraph
        cg = CausalGraph()
        path = cg.trace_path("CONSUMER_SPENDING", "OCCUPANCY", "REIT_COMMERCIAL")
        assert path is not None
        hop = path[-1]
        assert hop["lag_min"] == 10
        assert hop["lag_max"] == 30
        assert hop["confidence"] == pytest.approx(0.65)

    def test_retail_platform_still_sss(self):
        """RETAIL_PLATFORM KHÔNG bị đổi sang chain rental (không contaminate)."""
        from src.calibration.causal_edge import CausalGraph
        cg = CausalGraph()
        assert cg.trace_path("CONSUMER_SPENDING", "SAME_STORE_SALES", "RETAIL_PLATFORM") is not None
        assert cg.trace_path("INTEREST_RATE", "RENTAL_YIELD", "RETAIL_PLATFORM") is None


class TestCSIMapping:
    def test_archetype_target_node(self):
        from src.governor.csi_explain import ARCHETYPE_TARGET_NODE
        assert ARCHETYPE_TARGET_NODE["REIT_COMMERCIAL"] == "RENTAL_YIELD"
        # RETAIL_PLATFORM giữ nguyên SAME_STORE_SALES — không contaminate.
        assert ARCHETYPE_TARGET_NODE["RETAIL_PLATFORM"] == "SAME_STORE_SALES"

    def test_archetype_source_node(self):
        from src.governor.csi_explain import ARCHETYPE_SOURCE_NODE
        assert ARCHETYPE_SOURCE_NODE["REIT_COMMERCIAL"] == "INTEREST_RATE"


class TestGovernorConfig:
    def test_prior_by_archetype(self):
        from src.governor.company_state import PRIOR_BY_ARCHETYPE
        assert PRIOR_BY_ARCHETYPE["REIT_COMMERCIAL"] == pytest.approx(0.48)

    def test_payout_by_archetype(self):
        from src.governor.fair_multiple_engine import PAYOUT_BY_ARCHETYPE
        assert PAYOUT_BY_ARCHETYPE["REIT_COMMERCIAL"] == pytest.approx(0.30)

    def test_factor_exposure_registered(self):
        from src.business.factor_exposure import ARCHETYPE_EXPOSURE_BASE
        assert "REIT_COMMERCIAL" in ARCHETYPE_EXPOSURE_BASE
        exp = ARCHETYPE_EXPOSURE_BASE["REIT_COMMERCIAL"]
        assert exp["INTEREST_RATE"][0] == pytest.approx(0.50)
        assert exp["CONSUMER_SPENDING"][0] == pytest.approx(0.65)
