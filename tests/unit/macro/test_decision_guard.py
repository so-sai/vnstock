"""Unit tests for decision_guard.py — pure math + Emergency Recall orchestration.

Nhóm 3: Logic Phòng vệ & Vốn (7 tests)
  - 4 boundary tests for _he_so_tuoi_du_lieu()
  - 3 orchestration tests for Emergency Recall (Bước 4a in orchestrator.py)
"""

from unittest.mock import patch

import pandas as pd
import pytest

from src.engine.decision_guard import _he_so_tuoi_du_lieu


def _make_anh_chup(trang_thai_cau_truc="ĐỒNG THUẬN", entropy=2.5, so_tru=3,
                   early_warning=False, regime_status="RANGING", adx=30, delta_adx=5):
    """Ảnh chụp thị trường tối thiểu — kích hoạt THAM GIA ở Bước 2."""
    return {
        "regime": {"trang_thai": regime_status, "adx": adx, "delta_adx": delta_adx},
        "cau_truc": {"trang_thai": trang_thai_cau_truc, "so_tru": so_tru, "entropy": entropy},
        "canh_bao_som": {"co_canh_bao": early_warning},
    }


def _mock_read_sql(*args, **kwargs):
    """Vượt data quality guard (cnt>=50); retest queries trả về empty (không crash)."""
    sql = args[0] if args else kwargs.get("sql", "")
    if "COUNT(DISTINCT symbol)" in sql:
        return pd.DataFrame({"cnt": [200]})
    return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# Nhóm 3a: Pure Math — _he_so_tuoi_du_lieu() boundary tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStaleDataDecay:
    """4 boundary tests cho hàm suy giảm 2 pha."""

    def test_grace_period_24h(self):
        assert _he_so_tuoi_du_lieu(24) == 1.0
        assert _he_so_tuoi_du_lieu(0) == 1.0
        assert _he_so_tuoi_du_lieu(12) == 1.0

    def test_boundary_48h(self):
        assert _he_so_tuoi_du_lieu(48) == 0.3

    def test_decay_zone_100h(self):
        res = _he_so_tuoi_du_lieu(100)
        assert 0.1 < res < 0.3

    def test_floor_168h_plus(self):
        assert _he_so_tuoi_du_lieu(168) == 0.1
        assert _he_so_tuoi_du_lieu(200) == 0.1
        assert _he_so_tuoi_du_lieu(1000) == 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# Nhóm 3b: Emergency Recall orchestration (Bước 4a in orchestrator.py)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _backup_recall_state():
    """Cô lập recall_state.json — backup trước, restore sau."""
    import os
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent.parent \
        / "backend" / "data" / "probe_cache" / "recall_state.json"
    if f.exists():
        backup = f.read_text(encoding="utf-8")
        yield
        f.write_text(backup, encoding="utf-8")
        tmp = f.with_suffix(".json.tmp")
        if tmp.exists():
            os.remove(str(tmp))
    else:
        yield


class TestEmergencyRecallOrchestration:
    """3 tests: stale_override gates + SBV on-demand check.

    All tests mock pd.read_sql để vượt data quality guard (Bước 0) + chặn retest.
    """

    @patch("pandas.read_sql", side_effect=_mock_read_sql)
    @patch("src.services.macro.interbank_seeder.kiem_tra_sbv_theo_yeu_cau")
    @patch("src.services.macro.interbank_zscore.assess_interbank_risk")
    @patch("src.core.market_snapshot.tao_anh_chup")
    @patch("src.engine.recovery_engine.evaluate_recovery_status")
    @patch("src.engine.structural_healing.phan_tich_hoi_phuc")
    def test_high_rate_forces_stop(self, mock_healing, mock_recovery,
                                   mock_snapshot, mock_interbank, mock_sbv, mock_sql):
        """stale_override + THAM GIA + ON=12.5% → DỪNG NGOÀI, he_so=0.0"""
        mock_snapshot.return_value = _make_anh_chup()
        mock_interbank.return_value = {
            "veto": "NONE", "stale_override": True, "hours_stale": 72,
            "zscore": {"z_fast": 28.35},
        }
        mock_sbv.return_value = {"scraped": True, "ON": 12.5}
        mock_recovery.return_value = {"is_recovery": False, "status": "KHONG", "log": []}
        mock_healing.return_value = {"trang_thai_hoi_phuc": "KHONG", "chuyen_doi": False}

        from src.engine.orchestrator import quyet_dinh_cuoi
        result = quyet_dinh_cuoi()

        assert result["quyet_dinh"] == "DUNG NGOAI"
        assert result.get("he_so_giam_ty_trong") == 0.0
        assert "Emergency Recall" in " ".join(result.get("ly_do", []))

    @patch("pandas.read_sql", side_effect=_mock_read_sql)
    @patch("src.services.macro.interbank_zscore.assess_interbank_risk")
    @patch("src.core.market_snapshot.tao_anh_chup")
    @patch("src.engine.recovery_engine.evaluate_recovery_status")
    @patch("src.engine.structural_healing.phan_tich_hoi_phuc")
    def test_low_rate_passes_guard(self, mock_healing, mock_recovery,
                                   mock_snapshot, mock_interbank, mock_sql):
        """stale_override + ON=4.5% → guard runs, he_so=0.3 (hours_stale=48)."""
        mock_snapshot.return_value = _make_anh_chup()
        mock_interbank.return_value = {
            "veto": "NONE", "stale_override": True, "hours_stale": 48,
            "zscore": {"z_fast": 2.0},
        }
        mock_recovery.return_value = {"is_recovery": False, "status": "KHONG", "log": []}
        mock_healing.return_value = {"trang_thai_hoi_phuc": "KHONG", "chuyen_doi": False}

        from src.engine.orchestrator import quyet_dinh_cuoi
        result = quyet_dinh_cuoi()

        assert result.get("he_so_giam_ty_trong") == 0.3
        assert result["quyet_dinh"] != "DUNG NGOAI"

    @patch("pandas.read_sql", side_effect=_mock_read_sql)
    @patch("src.services.macro.interbank_zscore.assess_interbank_risk")
    @patch("src.core.market_snapshot.tao_anh_chup")
    @patch("src.engine.recovery_engine.evaluate_recovery_status")
    @patch("src.engine.structural_healing.phan_tich_hoi_phuc")
    def test_no_stale_override(self, mock_healing, mock_recovery,
                               mock_snapshot, mock_interbank, mock_sql):
        """stale_override=False → không recall, guard cho he_so=1.0, quyết định=THAM GIA."""
        mock_snapshot.return_value = _make_anh_chup()
        mock_interbank.return_value = {
            "veto": "NONE", "stale_override": False, "hours_stale": 0,
            "zscore": {"z_fast": 1.0},
        }
        mock_recovery.return_value = {"is_recovery": False, "status": "KHONG", "log": []}
        mock_healing.return_value = {"trang_thai_hoi_phuc": "KHONG", "chuyen_doi": False}

        from src.engine.orchestrator import quyet_dinh_cuoi
        result = quyet_dinh_cuoi()

        assert result.get("he_so_giam_ty_trong") == 1.0
        assert result["quyet_dinh"] == "THAM GIA"
