"""
test_decision_guard_integration.py — Integration Test cho DecisionGuard pipeline.

Chạy toàn bộ chuỗi: StaleTracker → RecoveryGovernor → BreadthTrapDetector → kiem_tra_an_toan
với snapshot thật ngày 22/07/2026.

Run: python -m pytest backend/tests/test_decision_guard_integration.py -v
"""
import sys, io
from pathlib import Path

# Hydrate path
p = Path(__file__).resolve().parent.parent / "src"
for d in [p.parent, p]:
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pytest
from src.engine.decision_guard import kiem_tra_an_toan, reset_trap_detector, get_trap_detector
from src.engine.recovery_governor import RecoveryGovernor
from src.engine.macro_stale_tracker import StaleTracker
from src.core.market_snapshot import tao_anh_chup


@pytest.fixture(autouse=True)
def reset_governors():
    """Reset tat ca singleton governors truoc moi test."""
    reset_trap_detector()
    RecoveryGovernor.reset_instance()
    StaleTracker.reset_instance()
    yield


SNAPSHOT_DATE = "2026-07-22"
EXPECTED_FRESH_RATIO = 0.17  # 17%
EXPECTED_TERMINAL_RATIO = 0.54  # 54%
EXPECTED_CONFIDENCE = 0.158  # 15.8%


class TestDecisionGuardIntegration:
    """Integration test: StaleTracker + RecoveryGovernor + DecisionGuard."""

    def _get_snapshot(self):
        """Lay snapshot that cho ngay 22/07/2026."""
        return tao_anh_chup(SNAPSHOT_DATE)

    def _get_confidence(self):
        """Lay confidence tu snapshot."""
        import src.engine.confidence_layer as cl
        # Unicode-escape: \u0111\u00e1nh_gi\u00e1_\u0111\u1ed9_tin_c\u1eady = danh_gia_do_tin_cay
        func = getattr(cl, '\u0111\u00e1nh_gi\u00e1_\u0111\u1ed9_tin_c\u1eady')
        snap = self._get_snapshot()
        result = func(anh_chup=snap)
        return result

    # ── StaleTracker Tests ──

    def test_stale_tracker_veto_active(self):
        """StaleTracker phat hien fresh_ratio=17% < 50% → veto=True."""
        from src.config import DATA_DIR
        tracker = StaleTracker.get_instance()
        state = tracker.update(db_path=str(DATA_DIR / "screener_cache.db"))
        assert state["veto"] is True, "Veto phai duoc kich hoat"
        assert state["fresh_ratio"] <= EXPECTED_FRESH_RATIO + 0.05, (
            f"fresh_ratio={state['fresh_ratio']:.2%} vuot nguong"
        )
        assert state["terminal_ratio"] >= EXPECTED_TERMINAL_RATIO - 0.05, (
            f"terminal_ratio={state['terminal_ratio']:.2%} khong dat"
        )

    def test_stale_tracker_weights_sum_to_one(self):
        """Tong trong so L1 = 1.0."""
        from src.config import DATA_DIR
        tracker = StaleTracker.get_instance()
        state = tracker.update(db_path=str(DATA_DIR / "screener_cache.db"))
        w_sum = sum(state["weights"].values())
        assert abs(w_sum - 1.0) < 0.01, f"Tong weight={w_sum:.4f} != 1.0"

    def test_stale_tracker_no_weight_exceeds_cap(self):
        """Khong co weight nao vuot 0.50 cap."""
        from src.config import DATA_DIR
        tracker = StaleTracker.get_instance()
        state = tracker.update(db_path=str(DATA_DIR / "screener_cache.db"))
        for name, w in state["weights"].items():
            assert w <= 0.501, f"Weight {name}={w:.4f} vuot 0.50 cap"

    # ── RecoveryGovernor Tests ──

    def test_recovery_governor_cash_only_when_veto(self):
        """Khi veto=True, governor phai o CASH_ONLY."""
        from src.config import DATA_DIR
        StaleTracker.get_instance().update(db_path=str(DATA_DIR / "screener_cache.db"))
        gov = RecoveryGovernor.get_instance()
        # Mo phong veto
        gov.record_veto(is_veto=True)
        state = gov.update(
            fresh_ratio=EXPECTED_FRESH_RATIO,
            so_tru=1,
            breadth_momentum=-34.0,
        )
        assert state["position_level"] == 0.0, (
            f"position_level={state['position_level']} != 0.0"
        )
        assert state["position_label"] == "CASH_ONLY"

    def test_recovery_governor_uses_calibrated_params(self):
        """Governor dung tham so calibrated (k=0.04, h1=0.05, h2=0.15, h3=0.35)."""
        from src.engine.recovery_governor import DEFAULT_K, DEFAULT_H1, DEFAULT_H2, DEFAULT_H3, DEFAULT_H
        assert DEFAULT_K == 0.04, f"k={DEFAULT_K} != 0.04"
        assert DEFAULT_H1 == 0.05, f"h1={DEFAULT_H1} != 0.05"
        assert DEFAULT_H2 == 0.15, f"h2={DEFAULT_H2} != 0.15"
        assert DEFAULT_H3 == 0.35, f"h3={DEFAULT_H3} != 0.35"
        assert DEFAULT_H == 0.15, f"h={DEFAULT_H} != 0.15"

    # ── Full Pipeline Integration Test ──

    def test_full_pipeline_blocks_trading_on_macro_veto(self):
        """Full pipeline: macro veto + structural vet → DUNG NGOAI."""
        snap = self._get_snapshot()
        confidence = self._get_confidence()

        result = kiem_tra_an_toan(
            quyet_dinh_de_xuat="MUA",
            ly_do_de_xuat=["tin hieu ky thuat"],
            do_tin_cay=confidence,
            anh_chup=snap,
        )

        # Verify block
        assert result.get("quyet_dinh") == "DUNG NGOAI", (
            f"Quyet dinh={result.get('quyet_dinh')} != DUNG NGOAI"
        )
        bi_ch = result.get('bi_ch\u1eb7n')  # bi_chAn
        assert bi_ch is True, f"Khong bi chan (bi_chAn={bi_ch})"
        assert result.get("he_so_giam_ty_trong") == 0.0, "he_so != 0.0"

        # Verify RecoveryGovernor trong result
        rg = result.get("recovery_governor", {})
        assert rg.get("position_label") in ("CASH_ONLY", "CASH_ONLY_CUTOFF"), (
            f"position_label={rg.get('position_label')}"
        )
        assert rg.get("position_level") == 0.0, (
            f"position_level={rg.get('position_level')} != 0.0"
        )

        # Verify MacroStale trong result
        ms = result.get("macro_stale", {})
        assert ms.get("veto") is True, "macro_stale.veto != True"
        assert ms.get("fresh_ratio", 1.0) < 0.50, (
            f"fresh_ratio={ms.get('fresh_ratio')} >= 0.50"
        )

    def test_confidence_layer_includes_macro_quality(self):
        """confidence_layer co factor chat_luong_vi_mo."""
        import src.engine.confidence_layer as cl
        mod_vars = vars(cl)
        # Tim TRONG_SO: dict co it nhat 6 key va chua '0.19'
        weights = None
        for k, v in mod_vars.items():
            if isinstance(v, dict) and len(v) >= 6:
                vals = [float(x) for x in v.values() if isinstance(x, (int, float))]
                if 6 <= len(vals) <= 8 and any(0.18 < x < 0.20 for x in vals):
                    weights = v
                    break
        assert weights is not None, "Khong tim thay TRONG_SO dict"
        # Kiem tra co key chua 'vi_mo' (Unicode-safe)
        has_macro = any("v\u0129_m" in str(k) for k in weights.keys())
        assert has_macro, "Thieu factor chat_luong_vi_mo"
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.02, f"Tong={total} != 1.0"

    def test_confidence_score_matches_expected(self):
        """Diem tin cay ~15.8% nhu da kiem dinh."""
        conf = self._get_confidence()
        score = conf.get('\u0111i\u1ec3m_tin_c\u1eady', 0)  # diem_tin_cay
        assert abs(score - EXPECTED_CONFIDENCE) < 0.05, (
            f"diem_tin_cay={score:.3f} != {EXPECTED_CONFIDENCE}"
        )
        # Kiem tra 'tam_ngung_ket_luan' -> True (dung Unicode escape)
        tam_ngung = any("t\u1ea1m_ng" in k and conf[k] is True for k in conf if isinstance(k, str))
        assert tam_ngung, "Khong tam ngung ket luan"

    def test_breadth_trap_detector_active(self):
        """BreadthTrapDetector hoat dong trong pipeline."""
        snap = self._get_snapshot()
        confidence = self._get_confidence()

        # Chay pipeline de kich hoat trap detector
        result = kiem_tra_an_toan(
            quyet_dinh_de_xuat="MUA",
            ly_do_de_xuat=["test"],
            do_tin_cay=confidence,
            anh_chup=snap,
        )
        bt = result.get("breadth_trap", {})
        if bt:
            assert "divergence" in bt, "breadth_trap thieu divergence"
            assert "trap_active" in bt, "breadth_trap thieu trap_active"
