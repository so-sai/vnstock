"""test_executive_report.py — TDD: 5-Model Consolidated Executive Report.

Rule: Zero-Hallucination. Builder phải render từ dữ liệu THẬT truyền vào
(kq / vn20 / volume_profile / rs). Mọi section thiếu nguồn phải trả
status="NO_DATA" kèm giải thích — TUYỆT ĐỐI KHÔNG TỰ BỊA con số.
"""

import pandas as pd
from src.core.executive_report import xay_dung_bao_cao_5_mo_hinh


# ============================================================
# Fixtures — số liệu fixture là DỮ LIỆU GIẢ LẬP cho unit test
# (không phải tuyên bố về thị trường thật).
# ============================================================
def _kq_mau():
    return {
        "ngay": "2026-08-06",
        "quyet_dinh": "QUAN SAT",
        "ly_do": ["DDI Gate: Δ_SA dương kéo dài", "chờ xác nhận"],
        "lri": {
            "score": 0.4765,
            "regime": "PROBE",
            "max_allocation_pct": 47.65,
            "components": {
                "interbank_on": 6.32,
                "interbank_regime": "NORMAL",
                "usd_vnd": 26249.0,
                "usdvnd_deviation_pct": 0.1648,
                "omo_proxy": -33200.0,
                "fii_flow_10d": -108.09,
                "breadth_pct": 71.1,
            },
        },
        "delta_divergence": {
            "delta_sa": 0.3289,
            "dS_dt": 0.4313,
            "ac_latency": 0.2049,
            "alpha_regime": 0.5,
            "regime": "RANGING",
            "healing_illusion": True,
            "action_filter": "caution",
        },
        "độ_tin_cậy_sau_hiệu_chỉnh": {
            "điểm_số": 0.528,
            "mức": "TRUNG_BINH",
            "tạm_ngưng": False,
        },
        "he_so_giam_ty_trong": 0.5074,
        "bi_chặn_bởi_bảo_vệ": False,
        "lý_do_chặn": None,
        "recovery_status": None,
        "healing_status": None,
    }


def _vn20_mau():
    return {
        "period": "2026Q2",
        "stage_counts": {"T1_pass": 7, "T2_pass": 6, "T3_pass": 6, "T4_pass": 6, "total_universe": 54},
        "qualified": [
            {"symbol": "TPB", "sector": "Ngân hàng", "phase": "RECOVERY", "mos": 0.7943, "score": 42.64},
            {"symbol": "VIB", "sector": "Ngân hàng", "phase": "RECOVERY", "mos": 0.6959, "score": 41.19},
            {"symbol": "CTG", "sector": "Ngân hàng", "phase": "RECOVERY", "mos": 0.6959, "score": 41.17},
        ],
    }


def _volume_profile_mau():
    return {
        "poc_price": 1280.5,
        "val": 1242.0,
        "vah": 1318.0,
        "price_current": 1285.0,
        "price_in_value_area": True,
        "volume_ratio": 0.72,
        "volume_converged": True,
        "vol_value_area": 812345.0,
        "vol_total_5d": 1120000.0,
    }


def _rs_mau():
    return pd.DataFrame(
        [
            {"symbol": "VVS", "rs_rating": 99.0, "price": 100.6, "avg_vol_20d": 206920.0},
            {"symbol": "STB", "rs_rating": 97.0, "price": 72.0, "avg_vol_20d": 3848000.0},
            {"symbol": "VIC", "rs_rating": 98.0, "price": 219.0, "avg_vol_20d": 3781800.0},
        ]
    )


# ============================================================
# Cấu trúc tổng thể
# ============================================================
class TestReportStructure:
    def test_tra_ve_5_model(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        for key in ("M1_MACRO", "M2_FUNDAMENTAL", "M3_BEHAVIORAL", "M4_ALPHA", "M5_GOVERNOR"):
            assert key in r, f"thiếu model {key}"

    def test_moi_model_co_status(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        for key, section in r.items():
            assert "status" in section, f"{key} thiếu trường status"


# ============================================================
# M1 — MACRO & LRI
# ============================================================
class TestM1Macro:
    def test_lri_score_that(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M1_MACRO"]["lri_score"] == 0.4765
        assert r["M1_MACRO"]["lri_regime"] == "PROBE"

    def test_max_allocation(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M1_MACRO"]["max_allocation_pct"] == 47.65

    def test_fii_flow_that(self):
        """FII 10D phải lấy từ lri.components — không tự chế số."""
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M1_MACRO"]["fii_flow_10d"] == -108.09

    def test_interbank_on(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M1_MACRO"]["interbank_on"] == 6.32
        assert r["M1_MACRO"]["interbank_regime"] == "NORMAL"

    def test_thieu_lri_la_no_data(self):
        kq = _kq_mau()
        kq["lri"] = None
        r = xay_dung_bao_cao_5_mo_hinh(kq)
        assert r["M1_MACRO"]["status"] == "NO_DATA"


# ============================================================
# M2 — FUNDAMENTAL (VN20 Gate)
# ============================================================
class TestM2Fundamental:
    def test_qualified_count(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), vn20=_vn20_mau())
        assert r["M2_FUNDAMENTAL"]["n_qualified"] == 3
        assert r["M2_FUNDAMENTAL"]["status"] == "OK"

    def test_top_mos_sorted(self):
        """Top mã theo MoS phải lấy từ vn20.qualified thật."""
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), vn20=_vn20_mau())
        tops = r["M2_FUNDAMENTAL"]["top_mos"]
        assert tops[0]["symbol"] == "TPB"
        assert tops[0]["mos"] == 0.7943

    def test_thieu_vn20_la_no_data(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), vn20=None)
        assert r["M2_FUNDAMENTAL"]["status"] == "NO_DATA"


# ============================================================
# M3 — BEHAVIORAL (DDI + FII + Volume Profile)
# ============================================================
class TestM3Behavioral:
    def test_delta_sa_that(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M3_BEHAVIORAL"]["delta_sa"] == 0.3289
        assert r["M3_BEHAVIORAL"]["action_filter"] == "caution"

    def test_healing_illusion(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M3_BEHAVIORAL"]["healing_illusion"] is True

    def test_fii_flow_tu_lri(self):
        """FII trong M3 phải tái sử dụng lri.components.fii_flow_10d thật."""
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M3_BEHAVIORAL"]["fii_flow_10d"] == -108.09

    def test_volume_profile_khi_co(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), volume_profile=_volume_profile_mau())
        assert r["M3_BEHAVIORAL"]["volume_profile"]["poc_price"] == 1280.5
        assert r["M3_BEHAVIORAL"]["volume_profile"]["volume_converged"] is True

    def test_thieu_delta_divergence_la_no_data(self):
        kq = _kq_mau()
        kq["delta_divergence"] = None
        r = xay_dung_bao_cao_5_mo_hinh(kq)
        assert r["M3_BEHAVIORAL"]["status"] == "NO_DATA"


# ============================================================
# M4 — ALPHA / RS
# ============================================================
class TestM4Alpha:
    def test_top_rs_tu_df(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), rs=_rs_mau())
        assert r["M4_ALPHA"]["status"] == "OK"
        assert r["M4_ALPHA"]["top_rs"][0]["symbol"] == "VVS"
        assert r["M4_ALPHA"]["top_rs"][0]["rs_rating"] == 99.0

    def test_thieu_rs_la_no_data(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau(), rs=None)
        assert r["M4_ALPHA"]["status"] == "NO_DATA"


# ============================================================
# M5 — GOVERNOR & GUARD
# ============================================================
class TestM5Governor:
    def test_he_so_giam_ty_trong(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M5_GOVERNOR"]["he_so_giam_ty_trong"] == 0.5074

    def test_confidence(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M5_GOVERNOR"]["confidence"] == 0.528
        assert r["M5_GOVERNOR"]["confidence_level"] == "TRUNG_BINH"

    def test_blocked(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M5_GOVERNOR"]["blocked"] is False
        assert r["M5_GOVERNOR"]["block_reason"] is None

    def test_recovery_healing_null(self):
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M5_GOVERNOR"]["recovery_status"] is None
        assert r["M5_GOVERNOR"]["healing_status"] is None


# ============================================================
# Zero-Hallucination
# ============================================================
class TestZeroHallucination:
    def test_khong_bea_so_khi_thieu(self):
        """Không section nào được tự sinh số khi thiếu nguồn."""
        r = xay_dung_bao_cao_5_mo_hinh(_kq_mau())
        assert r["M2_FUNDAMENTAL"]["status"] == "NO_DATA"
        assert r["M4_ALPHA"]["status"] == "NO_DATA"
        # M1/M3/M5 có nguồn thật nên OK
        assert r["M1_MACRO"]["status"] == "OK"
        assert r["M3_BEHAVIORAL"]["status"] == "OK"
        assert r["M5_GOVERNOR"]["status"] == "OK"

    def test_ke_thua_tuyet_doi_khong_sua_so(self):
        """Builder không được transform làm sai lệch số gốc (chỉ format lại)."""
        kq = _kq_mau()
        r = xay_dung_bao_cao_5_mo_hinh(kq)
        assert r["M1_MACRO"]["lri_score"] == kq["lri"]["score"]
        assert r["M3_BEHAVIORAL"]["delta_sa"] == kq["delta_divergence"]["delta_sa"]
        assert r["M5_GOVERNOR"]["confidence"] == kq["độ_tin_cậy_sau_hiệu_chỉnh"]["điểm_số"]
