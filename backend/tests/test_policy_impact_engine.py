"""TDD tests for PolicyImpactEngine — He Che Ban Chuan Hoa Tac Dong Chinh Sach Vi Mo.

Tests follow TDD Red->Green:
  - Written BEFORE implementing the engine to lock in requirements.
  - Run: python -m pytest backend/tests/test_policy_impact_engine.py -v
"""

import json
import sys

import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from src.governor.policy_impact_engine import (
    EVENT_TYPE_KBNN_LDR,
    EVENT_TYPE_NPL_EXTENSION,
    PolicyEvent,
    PolicyImpactEngine,
    PolicyImpactResult,
)


@pytest.fixture
def qd1743_event():
    return PolicyEvent(
        id="QD_1743_2026",
        title="QĐ 1743/QĐ-NHNN",
        effective_date="2026-08-01",
        expiry_date="2028-07-31",
        event_type=EVENT_TYPE_KBNN_LDR,
        affected_variables=["LDR", "INTERBANK", "COF", "NIM"],
        transmission_lag_days=2,
        half_life_days=15.0,
        clusters={"SOCB_BIG4": 1.0, "TMCP_LARGE": 0.2},
        delta_params={
            "ldr_relief_bps": 500.0,
            "cof_relief_bps": 20.0,
            "interbank_shock_pct": -0.6,
            "nim_boost_bps": 10.0,
            "gate_relaxation": {"CAPITAL_RATIO": 0.08},
        },
    )


@pytest.fixture
def engine(tmp_path):
    """Engine with isolated events file (no pollution of real DB)."""
    events_path = tmp_path / "policy_events.json"
    return PolicyImpactEngine(events_path=events_path)


class TestPolicyEventLifecycle:
    """Event must filter by effective/expiry date."""

    def test_is_active_on_inside_window(self, qd1743_event):
        assert qd1743_event.is_active_on("2026-08-05") is True

    def test_is_active_on_before_effective(self, qd1743_event):
        assert qd1743_event.is_active_on("2026-07-30") is False

    def test_is_active_on_after_expiry(self, qd1743_event):
        assert qd1743_event.is_active_on("2028-08-01") is False

    def test_is_active_boundary_effective_day(self, qd1743_event):
        assert qd1743_event.is_active_on("2026-08-01") is True


class TestClusterClassification:
    """Beneficiary clustering phải bất đối xứng (SOCB_BIG4 -> toàn phần)."""

    def test_big4_gets_full_benefit(self, qd1743_event):
        assert qd1743_event.get_benefit_ratio("VCB") == 1.0
        assert qd1743_event.get_benefit_ratio("CTG") == 1.0
        assert qd1743_event.get_benefit_ratio("BID") == 1.0
        # AGR thuộc BIG4 vĩ mô → hưởng lợi đầy đủ ở tầng chính sách
        assert qd1743_event.get_benefit_ratio("AGR") == 1.0

    def test_large_private_gets_partial(self, qd1743_event):
        assert qd1743_event.get_benefit_ratio("TCB") == 0.2
        assert qd1743_event.get_benefit_ratio("MBB") == 0.2

    def test_small_bank_gets_zero(self, qd1743_event):
        assert qd1743_event.get_benefit_ratio("SOME_SMALL_BANK") == 0.0

    def test_cluster_for_big4(self, qd1743_event):
        assert qd1743_event.cluster_for("VCB") == "SOCB_BIG4"
        assert qd1743_event.cluster_for("TCB") == "TMCP_LARGE"

    def test_agribank_macro_only_flag(self):
        """AGR thuộc BIG4 vĩ mô nhưng không thuộc vũ trụ giao dịch niêm yết."""
        from src.governor.policy_impact_engine import UNLISTED_MACRO_ONLY

        assert "AGR" in UNLISTED_MACRO_ONLY
        # VCB/BID/CTG niêm yết — không nằm trong cờ macro-only
        assert not (UNLISTED_MACRO_ONLY & {"VCB", "BID", "CTG"})


class TestLDRReliefCalculation:
    """QĐ 1743: LDR relief must scale with benefit ratio & transmission."""

    def test_big3_ldr_relief(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        # After 5 days, half_life=15 -> transmission ~0.206
        result = engine.compute_impact("VCB", "2026-08-06")
        assert result.ldr_relief_bps > 0
        assert result.cluster == "SOCB_BIG4"
        # Full benefit would be 500 bps; with trans~0.206 -> ~103 bps
        assert 50 < result.ldr_relief_bps < 500

    def test_ldr_relief_increases_with_time(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        early = engine.compute_impact("VCB", "2026-08-02").ldr_relief_bps
        late = engine.compute_impact("VCB", "2026-08-20").ldr_relief_bps
        assert late > early  # Transmission decays upward toward full impact

    def test_zero_relief_before_effective(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        result = engine.compute_impact("VCB", "2026-07-30")
        assert result.active_events == []
        assert result.total_impact_score == 0.0

    def test_cluster_c_gets_no_relief(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        result = engine.compute_impact("VTO", "2026-08-06")  # shipping co, not bank
        assert result.total_impact_score == 0.0
        assert result.ldr_relief_bps == 0.0


class TestTransmissionLag:
    """LAW-009: liquidity policy propagates fast; credit policy slow."""

    def test_transmission_factor_grows_with_elapsed(self, engine):
        f0 = PolicyImpactEngine._transmission_factor(0, 15.0)
        f1 = PolicyImpactEngine._transmission_factor(15, 15.0)
        f3 = PolicyImpactEngine._transmission_factor(45, 15.0)
        assert f0 == 0.0
        assert f1 == pytest.approx(0.5, abs=0.01)
        assert f3 == pytest.approx(0.875, abs=0.01)

    def test_credit_policy_slows_in_impact(self, engine, tmp_path):
        """NPL extension with half_life=90d should have small transmission early."""
        evt = PolicyEvent(
            id="TT02_EXT",
            title="Gia han Thong tu 02",
            effective_date="2026-08-01",
            expiry_date="2027-12-31",
            event_type=EVENT_TYPE_NPL_EXTENSION,
            half_life_days=90.0,
            clusters={"SOCB_BIG4": 1.0, "TMCP_LARGE": 0.5},
            delta_params={},
        )
        engine.register_event(evt)
        result = engine.compute_impact("VCB", "2026-08-06")
        # 5 days elapsed, HL=90 -> trans ~0.038 — impact very small
        assert result.transmission_factor < 0.2
        assert result.total_impact_score < 0.1


class TestGateApplication:
    """Cluster A gets relaxed gate; Cluster C stays isolated."""

    def test_cluster_a_gate_relaxed(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        assert engine.apply_to_gate("VCB", base_gate_result=False) is True

    def test_cluster_c_not_relaxed(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        assert engine.apply_to_gate("VTO", base_gate_result=False) is False

    def test_gate_pass_stays_pass(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        assert engine.apply_to_gate("VCB", base_gate_result=True) is True

    def test_expired_event_no_override(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        # After expiry -> no active events -> no relaxation
        assert engine.apply_to_gate("VCB", base_gate_result=False, target_date="2028-09-01") is False


class TestPersistence:
    """Events must persist to JSON and reload."""

    def test_register_persists_to_file(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        assert engine.events_path.exists()
        raw = json.loads(engine.events_path.read_text(encoding="utf-8"))
        assert len(raw) == 1
        assert raw[0]["id"] == "QD_1743_2026"

    def test_reload_from_file(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        engine2 = PolicyImpactEngine(events_path=engine.events_path)
        assert engine2.get_event("QD_1743_2026") is not None
        assert engine2.compute_impact("VCB", "2026-08-06").ldr_relief_bps > 0

    def test_remove_event(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        engine.remove_event("QD_1743_2026")
        assert engine.get_event("QD_1743_2026") is None


class TestImpactResultShape:
    """PolicyImpactResult must carry structured context for CLI display."""

    def test_result_has_structured_context(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        result = engine.compute_impact("VCB", "2026-08-06")
        assert isinstance(result, PolicyImpactResult)
        assert result.symbol == "VCB"
        assert result.active_events  # non-empty
        evt_info = result.active_events[0]
        assert evt_info["id"] == "QD_1743_2026"
        assert evt_info["benefit_ratio"] == 1.0
        assert "transmission_factor" in evt_info

    def test_result_dict_serializable(self, engine, qd1743_event):
        engine.register_event(qd1743_event)
        result = engine.compute_impact("VCB", "2026-08-06")
        d = result.to_dict()
        assert json.dumps(d)  # must not raise
        assert d["cluster"] == "SOCB_BIG4"


class TestBayesianMandatePolicyContext:
    """BayesianMandate must carry policy_context + policy_cap_boost."""

    def test_mandate_has_policy_fields(self):
        from src.governor.company_state import BayesianMandate

        m = BayesianMandate(
            symbol="VCB",
            action="OPEN",
            action_vn="Mở vị thế",
            expected_utility=0.5,
            p_gain=0.6,
            calibration_penalty=0.0,
            allocation_pct=10.0,
            conviction=0.6,
            macro_state="STABLE",
            transmission_phase="NORMAL",
            sector_phase="RECOVERY",
            health_archetype="BANK_HEALTHY",
            valuation_zone="FAIR",
            valuation_zone_peer="FAIR",
            valuation_zone_ts="FAIR",
            behavior_position="UNKNOWN",
        )
        assert m.policy_context == {}
        assert m.policy_cap_boost == 0.0

    def test_mandate_policy_context_serializable(self):
        from src.governor.company_state import BayesianMandate

        m = BayesianMandate(
            symbol="VCB",
            action="HOLD",
            action_vn="Nắm giữ",
            expected_utility=0.0,
            p_gain=0.0,
            calibration_penalty=0.0,
            allocation_pct=0.0,
            conviction=0.0,
            macro_state="STABLE",
            transmission_phase="NORMAL",
            sector_phase="RECOVERY",
            health_archetype="BANK",
            valuation_zone="FAIR",
            valuation_zone_peer="FAIR",
            valuation_zone_ts="FAIR",
            behavior_position="UNKNOWN",
            policy_context={"cluster": "SOCB_BIG4", "ldr_relief_bps": 84.0},
            policy_cap_boost=0.0252,
        )
        assert m.policy_context["ldr_relief_bps"] == 84.0
        assert m.policy_cap_boost == 0.0252


class TestDecisionGuardPolicyBoost:
    """decision_guard must boost max allocation for beneficiary cluster."""

    def test_policy_cap_boost_function(self, qd1743_event):
        from src.engine.decision_guard import compute_policy_cap_boost

        # Need QD 1743 active in real DB — use injected param path instead
        boost = compute_policy_cap_boost()
        assert isinstance(boost, dict)
        assert "cap_boost" in boost
        assert "active_events" in boost
        assert "beneficiary_cluster" in boost

    def test_guard_applies_cap_boost_when_positive(self, monkeypatch):
        from src.engine.decision_guard import kiem_tra_an_toan

        class _LRI:
            lri = 0.9
            regime = "AGGRESSIVE"
            max_allocation_pct = 100.0
            components_raw = {}

        monkeypatch.setattr(
            "src.governor.liquidity_recovery_index.compute_lri",
            lambda *a, **k: _LRI(),
        )
        policy = {"cap_boost": 0.05, "beneficiary_cluster": "SOCB_BIG4", "active_events": []}
        anh_chup = {
            "cau_truc": {"so_tru_ok": 3, "so_tru": 3},
            "phan_tich_chi_so": {"diem_thi_truong_that": 0.7, "do_lech_pha": "NONE"},
        }
        result = kiem_tra_an_toan(
            quyet_dinh_de_xuat="MUA",
            ly_do_de_xuat=["tín hiệu tích cực"],
            do_tin_cay={"tạm_ngưng_kết_luận": False},
            anh_chup=anh_chup,
            chinh_sach=policy,
        )
        # No interbank data -> he_so starts at 1.0; AGGRESSIVE LRI keeps it.
        # Boost pushes toward 1.0 (capped).
        assert result["he_so_giam_ty_trong"] == pytest.approx(1.0, abs=0.01)
        assert result["policy_impact"]["cap_boost"] == 0.05

    def test_guard_no_boost_when_blocked(self):
        from src.engine.decision_guard import kiem_tra_an_toan

        policy = {"cap_boost": 0.05, "beneficiary_cluster": "SOCB_BIG4", "active_events": []}
        # Force veto via structure breakdown (so_tru <= 1) -> he_so forced 0.0
        anh_chup = {
            "cau_truc": {"so_tru_ok": 0, "so_tru": 0},
            "phan_tich_chi_so": {"diem_thi_truong_that": 0.5, "do_lech_pha": "NONE"},
        }
        result = kiem_tra_an_toan(
            quyet_dinh_de_xuat="MUA",
            ly_do_de_xuat=[],
            do_tin_cay={"tạm_ngưng_kết_luận": False},
            anh_chup=anh_chup,
            chinh_sach=policy,
        )
        assert result["bi_chặn"] is True
        assert result["he_so_giam_ty_trong"] == 0.0
        assert result["policy_impact"]["cap_boost"] == 0.05  # still reported, not applied

    def test_guard_default_policy_impact_block(self):
        from src.engine.decision_guard import kiem_tra_an_toan

        anh_chup = {
            "cau_truc": {"so_tru_ok": 3, "so_tru": 3},
            "phan_tich_chi_so": {"diem_thi_truong_that": 0.7, "do_lech_pha": "NONE"},
        }
        result = kiem_tra_an_toan(
            quyet_dinh_de_xuat="DUNG NGOAI",
            ly_do_de_xuat=[],
            do_tin_cay={"tạm_ngưng_kết_luận": False},
            anh_chup=anh_chup,
        )
        assert "policy_impact" in result
        assert "cap_boost" in result["policy_impact"]


class TestPolicyDecay:
    """Policy Cliff Audit — f_decay ngăn cú sốc vách đá khi hết hạn."""

    def test_decay_factor_full_in_plateau(self):
        from src.governor.policy_impact_engine import PolicyImpactEngine

        # 200 days remaining, window=90 -> plateau (1.0)
        assert PolicyImpactEngine._decay_factor(200, 90) == 1.0

    def test_decay_factor_linear_in_window(self):
        from src.governor.policy_impact_engine import PolicyImpactEngine

        # 45 days remaining, window=90 -> 0.5 (half strength)
        assert PolicyImpactEngine._decay_factor(45, 90) == pytest.approx(0.5)

    def test_decay_factor_zero_at_expiry(self):
        from src.governor.policy_impact_engine import PolicyImpactEngine

        assert PolicyImpactEngine._decay_factor(0, 90) == 0.0
        assert PolicyImpactEngine._decay_factor(-5, 90) == 0.0

    def test_decay_factor_boundary_window(self):
        from src.governor.policy_impact_engine import PolicyImpactEngine

        # exactly at window boundary -> 1.0
        assert PolicyImpactEngine._decay_factor(90, 90) == 1.0

    def test_lifecycle_factors_in_compute(self, engine, qd1743_event):
        """compute_impact must include decay_factor in active_events."""
        engine.register_event(qd1743_event)
        result = engine.compute_impact("VCB", "2026-08-06")
        assert result.active_events
        assert "decay_factor" in result.active_events[0]
        # Far from expiry (Aug 2026 vs Jul 2028) -> decay = 1.0
        assert result.active_events[0]["decay_factor"] == 1.0

    def test_no_cliff_impact_in_decay_window(self, engine):
        """Impact tapers linearly as expiry approaches (no cliff)."""
        from src.governor.policy_impact_engine import PolicyEvent

        # Event effective 2026-01-01, expiry 2026-06-30, window 90d
        evt = PolicyEvent(
            id="SHORT_WINDOW",
            title="Short window test",
            effective_date="2026-01-01",
            expiry_date="2026-06-30",
            event_type=EVENT_TYPE_KBNN_LDR,
            half_life_days=15.0,
            decay_window_days=90,
            clusters={"SOCB_BIG4": 1.0},
            delta_params={"ldr_relief_bps": 500.0},
        )
        engine.register_event(evt)
        # 2026-06-20: 10 days before expiry, inside window -> decay ~0.11
        result = engine.compute_impact("VCB", "2026-06-20")
        assert result.active_events[0]["decay_factor"] == pytest.approx(10 / 90, abs=0.01)
        # 2026-07-01: past expiry -> no active events
        result_expired = engine.compute_impact("VCB", "2026-07-01")
        assert result_expired.active_events == []

    def test_lifecycle_transition_stable_to_decay(self, engine):
        """Boost in plateau year (2027) > boost near expiry (2028-05)."""
        from src.governor.policy_impact_engine import PolicyEvent

        evt = PolicyEvent(
            id="FULL_CYCLE",
            title="Full cycle",
            effective_date="2026-08-01",
            expiry_date="2028-07-31",
            event_type=EVENT_TYPE_KBNN_LDR,
            half_life_days=15.0,
            decay_window_days=90,
            clusters={"SOCB_BIG4": 1.0},
            delta_params={"ldr_relief_bps": 500.0},
        )
        engine.register_event(evt)
        stable = engine.compute_impact("VCB", "2027-06-01").ldr_relief_bps
        near_expiry = engine.compute_impact("VCB", "2028-05-15").ldr_relief_bps
        # 2028-05-15: ~77 days to expiry -> decay ~0.86 vs stable 1.0
        assert stable > near_expiry
        assert near_expiry > 0  # still positive, not cliff-to-zero

    def test_guard_cap_boost_uses_lifecycle(self, qd1743_event):
        """compute_policy_cap_boost uses lifecycle factor (not raw)."""
        from src.engine.decision_guard import compute_policy_cap_boost

        result = compute_policy_cap_boost()
        for evt in result["active_events"]:
            assert "lifecycle_factor" in evt
            assert 0.0 <= evt["lifecycle_factor"] <= 1.0
