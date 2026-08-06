"""test_decision_guard_emergency.py — TDD for EmergencyExitEngine wiring in decision_guard.

WHY: EmergencyExitEngine v2 (41 tests, TWAP slicing, ADV-aware) exists but was
not called from kiem_tra_an_toan() when LRI < 0.30 (DEFENSIVE). This gap meant
the Governor had no managed liquidation protocol — only a hard he_so=0 floor.

Run:  python -m pytest tests/test_decision_guard_emergency.py -v   (from backend/)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "libs"))

from src.engine.decision_guard import kiem_tra_an_toan, reset_trap_detector
from src.engine.macro_stale_tracker import StaleTracker
from src.engine.recovery_governor import RecoveryGovernor


@pytest.fixture(autouse=True)
def reset_governors():
    """Reset singleton governors before each test."""
    reset_trap_detector()
    RecoveryGovernor.reset_instance()
    StaleTracker.reset_instance()


def _base_do_tin_cay():
    """Minimal do_tin_cay dict that won't trigger confidence veto."""
    return {"tạm_ngưng_kết_luận": False}


def _base_anh_chup():
    """Minimal anh_chup with so_tru=3 to avoid structural veto."""
    return {"cau_truc": {"so_tru_ok": 3, "so_tru": 3}}


def _fake_lri(lri_score, regime, max_alloc=100.0):
    """Create a fake LRI result object."""
    fake = MagicMock()
    fake.lri = lri_score
    fake.regime = regime
    fake.max_allocation_pct = max_alloc
    fake.components_raw = {
        "S_Interbank": 0.1,
        "S_USDVND": 0.2,
        "S_OMO": 0.3,
        "S_FII": 0.4,
        "S_Breadth": 0.5,
    }
    return fake


SAMPLE_POSITIONS = {
    "HPG": {
        "shares": 1000,
        "entry_price": 25000,
        "beta": 1.6,
        "mos_pct": 15.0,
        "volume_avg_20d": 500000,
    },
    "FPT": {
        "shares": 500,
        "entry_price": 120000,
        "beta": 0.9,
        "mos_pct": 40.0,
        "volume_avg_20d": 800000,
    },
    "VCB": {
        "shares": 200,
        "entry_price": 95000,
        "beta": 0.6,
        "mos_pct": 60.0,
        "volume_avg_20d": 1200000,
    },
}


# ── Test: LRI DEFENSIVE triggers EmergencyExitEngine ──────────
class TestEmergencyExitDEFENSIVE:
    def test_buy_locked_true(self):
        """LRI < 0.30 → buy_locked = True."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
            )
        assert result["buy_locked"] is True
        assert result["he_so_giam_ty_trong"] == 0.0

    def test_emergency_exit_result_not_none(self):
        """LRI < 0.30 → emergency_exit result is present."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=SAMPLE_POSITIONS,
            )
        ee = result["emergency_exit"]
        assert ee is not None
        assert ee["is_defensive"] is True
        assert ee["lri_score"] == 0.28
        assert ee["buy_locked"] is True

    def test_exit_orders_ranked_by_beta_mos(self):
        """Exit orders: HPG (high beta=1.6, low MoS=15%) before FPT/VCB."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=SAMPLE_POSITIONS,
            )
        ee = result["emergency_exit"]
        assert len(ee["exit_orders"]) == 3
        # HPG: beta=1.6>1.5, MoS=15%<20% → CRITICAL (sell first)
        assert ee["exit_orders"][0]["symbol"] == "HPG"
        assert ee["exit_orders"][0]["priority"] == "CRITICAL"
        # FPT: beta=0.9, MoS=40% → MEDIUM
        assert ee["exit_orders"][1]["symbol"] == "FPT"
        # VCB: beta=0.6, MoS=60% → LOW (bluechip, hold last)
        assert ee["exit_orders"][2]["symbol"] == "VCB"
        assert ee["exit_orders"][2]["priority"] == "LOW"

    def test_tightened_stops(self):
        """All positions get tightened stops (-2%)."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=SAMPLE_POSITIONS,
            )
        ee = result["emergency_exit"]
        for sym in ["HPG", "FPT", "VCB"]:
            assert ee["tightened_stops"][sym] == -0.02

    def test_no_positions_still_buy_locked(self):
        """LRI DEFENSIVE + no positions → buy_locked=True, exit_orders empty."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
            )
        ee = result["emergency_exit"]
        assert ee is not None
        assert ee["buy_locked"] is True
        assert ee["exit_orders"] == []
        assert result["buy_locked"] is True

    def test_adv_sliced_order(self):
        """Position > 15% ADV → is_sliced=True."""
        positions = {
            "ILLIQ": {
                "shares": 100000,
                "entry_price": 10000,
                "beta": 1.8,
                "mos_pct": 5.0,
                "volume_avg_20d": 200000,  # 100000/200000 = 50% ADV > 15%
            },
        }
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.28, "DEFENSIVE", 0.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=positions,
            )
        ee = result["emergency_exit"]
        order = ee["exit_orders"][0]
        assert order["is_sliced"] is True
        assert order["position_pct_of_adv"] == 0.5
        assert order["estimated_days"] > 1


# ── Test: LRI PROBE → no emergency exit ──────────────────────
class TestEmergencyExitPROBE:
    def test_buy_locked_false(self):
        """LRI 0.3-0.8 (PROBE) → buy_locked=False, emergency_exit=None."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.55, "PROBE", 55.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=SAMPLE_POSITIONS,
            )
        assert result["buy_locked"] is False
        assert result["emergency_exit"] is None
        assert result["he_so_giam_ty_trong"] > 0

    def test_he_so_scaled_by_lri(self):
        """LRI PROBE → he_so_giam_ty_trong *= lri_score."""
        with (
            patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.55, "PROBE", 55.0)),
            patch(
                "src.engine.decision_guard.compute_policy_cap_boost",
                return_value={"cap_boost": 0.0, "active_events": [], "beneficiary_cluster": "NONE"},
            ),
        ):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
            )
        # he_so starts at 1.0, multiplied by LRI=0.55
        assert result["he_so_giam_ty_trong"] == pytest.approx(0.55, abs=0.01)


# ── Test: LRI AGGRESSIVE → no emergency exit ─────────────────
class TestEmergencyExitAGGRESSIVE:
    def test_buy_locked_false(self):
        """LRI >= 0.8 (AGGRESSIVE) → buy_locked=False, emergency_exit=None."""
        with patch("src.governor.liquidity_recovery_index.compute_lri", return_value=_fake_lri(0.85, "AGGRESSIVE", 100.0)):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
                open_positions=SAMPLE_POSITIONS,
            )
        assert result["buy_locked"] is False
        assert result["emergency_exit"] is None
        assert result["he_so_giam_ty_trong"] == 1.0


# ── Test: LRI error is non-blocking ──────────────────────────
class TestEmergencyExitErrorResilient:
    def test_lri_error_no_crash(self):
        """LRI computation error → non-blocking, no emergency exit."""

        def broken_compute_lri():
            raise RuntimeError("LRI module broken")

        with patch("src.governor.liquidity_recovery_index.compute_lri", side_effect=broken_compute_lri):
            result = kiem_tra_an_toan(
                quyet_dinh_de_xuat="MUA",
                ly_do_de_xuat=["test"],
                do_tin_cay=_base_do_tin_cay(),
                anh_chup=_base_anh_chup(),
            )
        # Should not crash, just skip LRI
        assert result["he_so_giam_ty_trong"] == 1.0
        assert result["emergency_exit"] is None
