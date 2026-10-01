"""test_archetype_securities_logistics.py — Regression tests cho archetype
SECURITIES_BROKER và LOGISTICS_OPERATOR.

WHY (bug audit 2026-10-01):
  - CTS (ICB "Môi giới chứng khoán", entity SECURITIES, D/E 3.15, RR 0.21)
    rơi ratio heuristic → REAL_ESTATE_DEVELOPER. Chain PRESALES vô nghĩa
    cho CTCK (metric đúng: MARGIN/BROKERAGE).
  - VTP (ICB "Vận tải / Chuyển phát nhanh", CFO/NI 0.63) rơi heuristic
    → RETAIL_PLATFORM. Chain SAME_STORE_SALES vô nghĩa cho logistics.
  - Guard tạm Hard Constraint 2b/2c (archetype.py) đang trả COMPOUNDER —
    chặn được 2 lỗi sai trên nhưng vẫn sai archetype (DNA/metric sai).
  - Fix: registry thêm SECURITIES_BROKER + LOGISTICS_OPERATOR; guard 2b/2c
    đổi target; BASELINE_MAP đóng băng CTS/VTP (audit-frozen contract).

Invariant:
  - Baseline (human-validated) thắng guard cấu trúc: SSI (SECURITIES_BROKER
    từ Sprint C2 PR4), GMD (CYCLICAL_HEAVY) KHÔNG đổi.
  - Guard cấu trúc áp cho mã cùng ngành CHƯA có baseline (VCI/VND/HCM,
    VSC/HAH/PCT).

Run:  python -m pytest tests/test_archetype_securities_logistics.py -q   (từ backend/)
"""

import pytest


@pytest.fixture
def engine():
    from src.business.archetype import ArchetypeEngine

    eng = ArchetypeEngine()
    yield eng
    eng.close()


class TestSecuritiesBroker:
    def test_cts_baseline_frozen_securities_broker(self, engine):
        """CTS → SECURITIES_BROKER (frozen contract; trước audit: REAL_ESTATE_DEVELOPER)."""
        assert engine.classify("CTS").archetype == "SECURITIES_BROKER"

    def test_cts_not_real_estate(self, engine):
        """Regression: CTCK không bao giờ là REAL_ESTATE_DEVELOPER."""
        assert engine.classify("CTS").archetype != "REAL_ESTATE_DEVELOPER"

    def test_securities_structural_guard(self, engine):
        """Cùng ngành CTCK chưa có baseline → guard cấu trúc (entity SECURITIES)."""
        for sym in ("VCI", "VND", "HCM"):
            assert engine.classify(sym).archetype == "SECURITIES_BROKER", sym

    def test_ssi_baseline_frozen_unchanged(self, engine):
        """SSI → SECURITIES_BROKER (Sprint C2 PR4; trước đó RETAIL_PLATFORM scaffold)."""
        assert engine.classify("SSI").archetype == "SECURITIES_BROKER"


class TestLogisticsOperator:
    def test_vtp_baseline_frozen_logistics(self, engine):
        """VTP → LOGISTICS_OPERATOR (frozen contract; trước audit: RETAIL_PLATFORM)."""
        assert engine.classify("VTP").archetype == "LOGISTICS_OPERATOR"

    def test_vtp_not_retail_platform(self, engine):
        """Regression: vận tải/chuyển phát không bao giờ là RETAIL_PLATFORM."""
        assert engine.classify("VTP").archetype != "RETAIL_PLATFORM"

    def test_transport_structural_guard(self, engine):
        """Cùng ngành vận tải (ICB) chưa có baseline → LOGISTICS_OPERATOR."""
        for sym in ("VSC", "HAH", "PCT"):
            assert engine.classify(sym).archetype == "LOGISTICS_OPERATOR", sym

    def test_gmd_baseline_frozen_unchanged(self, engine):
        """GMD giữ CYCLICAL_HEAVY — baseline thắng guard ICB Vận tải."""
        assert engine.classify("GMD").archetype == "CYCLICAL_HEAVY"


class TestRegistryAndMaps:
    def test_new_archetypes_registered(self):
        """Registry có đủ 2 archetype mới với DNA đúng loài."""
        from src.business.archetype import ARCHETYPE_REGISTRY, Cyclicality, RevenueModel

        broker = ARCHETYPE_REGISTRY["SECURITIES_BROKER"]
        assert broker.revenue_model is RevenueModel.FEE_BASED
        assert broker.cyclicality is Cyclicality.HIGH
        assert "Chứng khoán" in broker.label

        logi = ARCHETYPE_REGISTRY["LOGISTICS_OPERATOR"]
        assert logi.revenue_model is RevenueModel.TRANSACTIONAL
        assert logi.cyclicality is Cyclicality.HIGH
        assert logi.capex_intensity in ("MEDIUM", "HIGH")

    def test_baseline_contract_frozen(self):
        """BASELINE_MAP đóng băng CTS/VTP theo kết quả audit ICB."""
        from src.business.archetype import BASELINE_MAP

        assert BASELINE_MAP["CTS"] == "SECURITIES_BROKER"
        assert BASELINE_MAP["VTP"] == "LOGISTICS_OPERATOR"

    def test_csi_maps_archetype_coverage(self, engine):
        """CSI explain: BROKER (C2) + LOGISTICS (C3) đã có mapping;
        chỉ latent states (STEADY_EARNER/UNKNOWN) giữ None by-design."""
        from src.governor.csi_explain import ARCHETYPE_SOURCE_NODE, ARCHETYPE_TARGET_NODE

        assert ARCHETYPE_TARGET_NODE.get("SECURITIES_BROKER") == "MARGIN_INTEREST"
        assert ARCHETYPE_SOURCE_NODE.get("SECURITIES_BROKER") == "INTEREST_RATE"
        assert ARCHETYPE_TARGET_NODE.get("LOGISTICS_OPERATOR") == "OPERATING_PROFIT"
        assert ARCHETYPE_SOURCE_NODE.get("LOGISTICS_OPERATOR") == "GLOBAL_DEMAND"
        assert ARCHETYPE_TARGET_NODE.get("STEADY_EARNER") is None
        assert ARCHETYPE_TARGET_NODE.get("UNKNOWN") is None
        # sống: classifying đọc DNA từ registry — không ném lỗi, driver/sensitivities có dữ liệu
        for sym in ("CTS", "VTP"):
            arch = engine.classify(sym)
            assert arch.primary_driver
            assert arch.macro_sensitivities

    def test_securities_broker_causal_chain_exists(self):
        """Sprint C2 PR1: registry có ≥2 edge SECURITIES_BROKER, trace
        INTEREST_RATE → MARGIN_INTEREST thông suốt (không phantom node)."""
        from src.calibration.causal_edge import CausalGraph

        cg = CausalGraph()
        broker_edges = [e for e in cg.edges.values() if e.archetype == "SECURITIES_BROKER"]
        assert len(broker_edges) >= 2
        path = cg.trace_path("INTEREST_RATE", "MARGIN_INTEREST", "SECURITIES_BROKER")
        assert path is not None and len(path) >= 1

    def test_logistics_causal_chain_exists(self):
        """Sprint C3: registry có ≥3 edge LOGISTICS_OPERATOR, trace
        GLOBAL_DEMAND → OPERATING_PROFIT thông suốt (không phantom node)."""
        from src.calibration.causal_edge import CausalGraph

        cg = CausalGraph()
        logi_edges = [e for e in cg.edges.values() if e.archetype == "LOGISTICS_OPERATOR"]
        assert len(logi_edges) >= 3
        path = cg.trace_path("GLOBAL_DEMAND", "OPERATING_PROFIT", "LOGISTICS_OPERATOR")
        assert path is not None and len(path) >= 1
