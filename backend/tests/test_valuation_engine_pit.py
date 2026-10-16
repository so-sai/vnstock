"""Canary + unit test cho PIT/basis của `ValuationEngine` (BƯỚC P & Hạng mục E, 2026-10-16).

VÌ SAO CÓ FILE NÀY — hai lỗi đã làm nhiễm lịch sử `valuation_scores`:
  1. **Fallback giá tương lai**: khi không có bar trong 30 ngày quanh `period_end`, bản cũ chạy
     `SELECT date, close ... ORDER BY date DESC LIMIT 1` ⇒ lấy **giá mới nhất trong kho** rồi
     tính PE/PB cho kỳ quá khứ. Đo được **636/1.789 cặp (35,6%)** nhiễm; ACB 2018Q1→2019Q4 đều
     bị gán giá 21.100 của **năm 2025**.
  2. **Annualize ×4 mù quáng**: `ttm_eps = eps * 4` cho MỌI kỳ ⇒ **39/40** ca PE<2 có
     `PE_lưu = (giá/EPS)/4` chính xác, kỳ tập trung ở Q2+Q4 (65%) — đúng chỗ EPS luỹ kế YTD.
Chi tiết: `backend/src/research/financial_facts_ratio_audit.md` §2 và §8.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.financial.valuation_engine import PRICE_BASIS, ValuationEngine

SCREENER = ROOT / "backend" / "data" / "screener_cache.db"


def _engine(price=None, price_flag=None) -> ValuationEngine:
    """Engine KHÔNG cần DB: bỏ __init__ và thay 2 hàm lấy giá."""
    e = ValuationEngine.__new__(ValuationEngine)
    e._get_price_with_flag = lambda symbol, target: (price, price_flag)  # type: ignore[method-assign]
    e._get_price_at_date = lambda symbol, target: price  # type: ignore[method-assign]
    return e


def _metrics(**kw) -> dict:
    base = {
        "_fiscal_year": 2018,
        "_fiscal_quarter": 1,
        "EPS": 2000.0,
        "CHARTER_CAPITAL": 1.0e13,
        "TOTAL_EQUITY": 3.0e13,
        "REVENUE": 1.0e13,
    }
    base.update(kw)
    return base


# ── Lỗi 2: annualize theo chu kỳ ────────────────────────────────────────────────


def test_annualize_factor_theo_chu_ky():
    f = ValuationEngine._annualize_factor
    assert f(1) == 4.0
    assert f(2) == 2.0
    assert f(3) == pytest.approx(4 / 3)
    assert f(4) == 1.0


def test_pe_ytd_q2_chi_nhan_2_khong_nhan_4():
    """EPS luỹ kế H1 ⇒ ×2. Bản cũ ×4 làm PE nhỏ đi một nửa ở Q2."""
    e = _engine(price=10_000.0)
    r = e.compute_ratios_for_period(
        "HPG", "2018Q2", _metrics(_fiscal_quarter=2, EPS=2_000.0, _flow_kind="YTD"), "STANDARD", shares_now=6.4e9
    )
    assert r["PE"] == pytest.approx(10_000 / (2_000 * 2))


def test_pe_ytd_q4_khong_nhan_gi():
    """EPS cả năm ⇒ ×1. Bản cũ ×4 làm PE nhỏ đi 4 lần (đúng ca SHB 2024Q4: 3,37 → 0,84)."""
    e = _engine(price=7_760.0)
    r = e.compute_ratios_for_period(
        "SHB", "2024Q4", _metrics(_fiscal_quarter=4, EPS=2_306.0, _flow_kind="YTD"), "BANK", shares_now=5e9
    )
    assert r["PE"] == pytest.approx(7_760 / 2_306)


def test_pe_eps_rieng_quy_thi_van_nhan_4():
    e = _engine(price=10_000.0)
    r = e.compute_ratios_for_period(
        "HPG",
        "2018Q2",
        _metrics(_fiscal_quarter=2, EPS=500.0, _flow_kind="QUARTER"),
        "STANDARD",
        shares_now=6.4e9,
    )
    assert r["PE"] == pytest.approx(10_000 / (500 * 4))


# ── Lỗi 1: không bịa giá ───────────────────────────────────────────────────────


def test_khong_co_gia_thi_khong_bia_va_gan_co():
    e = _engine(price=None, price_flag="PRICE_FALLBACK_FUTURE")
    r = e.compute_ratios_for_period("ACB", "2018Q1", _metrics(), "BANK", shares_now=1e9)
    assert r["_price"] is None
    assert r["_is_clean"] is False
    assert "PRICE_FALLBACK_FUTURE" in r["_flags"]
    assert "PE" not in r and "PB" not in r, "KHÔNG được bịa ratio khi thiếu giá"


def test_price_basis_duoc_khai_bao():
    assert PRICE_BASIS in ("ADJUSTED_TODAY", "RAW")


# ── Cờ của Hạng mục E ─────────────────────────────────────────────────────────


def test_basis_mismatch_khi_bvps_dung_shares_pit():
    """Giá ADJUSTED_TODAY mà BVPS chia theo shares TẠI KỲ ⇒ BASIS_MISMATCH (HPG 2018Q1 = 0,450)."""
    e = _engine(price=10_220.0)
    m = _metrics(TOTAL_EQUITY=34.428e12, CHARTER_CAPITAL=15.171e12, EPS=None)
    r = e.compute_ratios_for_period("HPG", "2018Q1", m, "STANDARD", shares_now=None)
    assert "BASIS_MISMATCH" in r["_flags"]
    assert r["_is_clean"] is False


def test_eps_am_duoc_gan_co_chu_khong_bo_lang():
    e = _engine(price=10_000.0)
    r = e.compute_ratios_for_period("XXX", "2018Q1", _metrics(EPS=-100.0), "STANDARD", shares_now=1e9)
    assert "NEGATIVE_EARNINGS" in r["_flags"]
    assert "PE" not in r


def test_pe_cuc_thap_duoc_gan_co():
    e = _engine(price=10_000.0)
    r = e.compute_ratios_for_period(
        "XXX", "2018Q2", _metrics(_fiscal_quarter=2, EPS=3_000.0, _flow_kind="QUARTER"), "STANDARD", shares_now=1e9
    )
    assert "EXTREME_LOW_PE" in r["_flags"]
    assert "PERIODICITY_MISMATCH_EPS" in r["_flags"]


# ── Canary cần store thật (tự skip nếu thiếu DB / DB bị khoá) ──────────────────


def _skip_if_no_db():
    if not SCREENER.exists():
        pytest.skip("không có screener_cache.db")
    try:
        sqlite3.connect(f"file:{SCREENER.as_posix()}?mode=ro", uri=True).close()
    except sqlite3.Error as e:  # pragma: no cover - phụ thuộc môi trường
        pytest.skip(f"DB không đọc được: {e}")


def test_canary_hpg_2018q1_lay_dung_gia_cuoi_quy():
    """CANARY VĨNH VIỄN: HPG 2018Q1 = close ngày 2018-03-30 = 10.220 (không phải giá mới nhất)."""
    _skip_if_no_db()
    e = ValuationEngine.__new__(ValuationEngine)
    px, flag = e._get_price_with_flag("HPG", "2018-03-30")
    assert flag is None, f"phải là PIT hợp lệ, nhận flag={flag}"
    assert px == pytest.approx(10220.0)

    # và PB với shares-now phải ra 1,899 (không phải 0,450 như khi trộn basis)
    r = e.compute_ratios_for_period(
        "HPG",
        "2018Q1",
        {"_fiscal_year": 2018, "_fiscal_quarter": 1, "TOTAL_EQUITY": 34428192910709.0, "CHARTER_CAPITAL": 63963.0e9},
        "STANDARD",
        shares_now=63963.0e9 / 10000.0,
    )
    assert r["PB"] == pytest.approx(1.899, abs=0.01)


def test_canary_acb_2018q1_khong_con_gia_nam_2025():
    """ACB 2018Q1 không có bar quanh period_end ⇒ None + PRICE_FALLBACK_FUTURE, KHÔNG 21.100."""
    _skip_if_no_db()
    e = ValuationEngine.__new__(ValuationEngine)
    px, flag = e._get_price_with_flag("ACB", "2018-03-31")
    assert px is None, f"Missing > Fake — phải None, nhận {px}"
    assert flag == "PRICE_FALLBACK_FUTURE"


# ── Tự hiệu chuẩn chu kỳ EPS (phán quyết 2026-10-16) ───────────────────────────


def _facts_kind(kind: str) -> dict:
    """8 kỳ liền nhau, shares = 1.000 (CHARTER_CAPITAL/10.000); NI **biến thiên**.

    QUARTER ⇒ EPS = NI_kỳ/S · TTM ⇒ EPS = Σ4/S · YTD ⇒ EPS = Σ(đầu năm→kỳ)/S
    FLAT ⇒ NI và EPS đều phẳng (ca `AMBIGUOUS`) · SCALED ⇒ EPS = 0,891 × NI_kỳ/S (ca lệch MỨC
    kiểu VNM) · NOISY ⇒ NI dao động mạnh + EPS hằng (ca không quy ước nào ổn định, kiểu CII).
    """
    periods = [f"2024Q{q}" for q in (1, 2, 3, 4)] + [f"2025Q{q}" for q in (1, 2, 3, 4)]
    base = (1000.0, 1200.0, 900.0, 1500.0, 1100.0, 1300.0, 950.0, 1400.0)
    wild = (1000.0, 2000.0, 500.0, 1800.0, 400.0, 2200.0, 600.0, 1900.0)
    ni = {p_: v for p_, v in zip(periods, wild if kind == "NOISY" else base)}
    out = {}
    for idx, per in enumerate(periods):
        q = int(per[5])
        v = 1000.0 if kind == "FLAT" else ni[per]
        if kind in ("QUARTER", "SCALED", "NOISY"):
            eps = 1.0 if kind == "NOISY" else v / 1000.0
        elif kind == "TTM":
            eps = sum(ni[periods[idx - k]] for k in range(4) if idx - k >= 0) / 1000.0
        elif kind == "FLAT":
            eps = 1.0
        else:  # YTD
            eps = sum(ni[periods[idx - k]] for k in range(q) if idx - k >= 0) / 1000.0
        if kind == "SCALED":
            eps *= 0.891
        out[per] = {
            "EPS": eps,
            "NET_INCOME": v,
            "CHARTER_CAPITAL": 10_000_000.0,
            "_fiscal_year": int(per[:4]),
            "_fiscal_quarter": q,
        }
    return out


def test_calibrate_nhan_dien_quarter():
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, cv, level = e.calibrate_eps_periodicity(_facts_kind("QUARTER"))
    assert (kind, basis) == ("QUARTER", "CALIBRATED")
    assert cv is not None and cv <= 0.02  # BƯỚC 5: chu kỳ đo bằng CV của tỷ lệ
    assert level is not None and level <= 2.0


def test_calibrate_nhan_dien_ttm():
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, cv, level = e.calibrate_eps_periodicity(_facts_kind("TTM"))
    assert (kind, basis) == ("TTM", "CALIBRATED")
    assert cv is not None and cv <= 0.02
    assert level is not None and level <= 2.0


def test_calibrate_nhan_dien_ytd():
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, _cv, _level = e.calibrate_eps_periodicity(_facts_kind("YTD"))
    assert (kind, basis) == ("YTD", "CALIBRATED")


def test_calibrate_khong_khop_quy_uoc_nao_thi_unknown():
    """Fail-closed: EPS dao động KHÔNG tương quan với NI ⇒ không quy ước nào ổn định ⇒ UNKNOWN.

    WHY đổi ca test (Bước 5, 2026-10-16): EPS *hằng* so với ứng viên TTM vốn rất trơn vẫn "ổn
    định hình dạng" ⇒ rơi vào TTM kèm cờ lệch MỨC lớn — đúng giới hạn nội tại của phép thử hình
    dạng (mức bị hấp thụ vào độ lệch). Muốn ra UNKNOWN thì chuỗi EPS phải lệch pha với mọi ứng viên.
    """
    f = _facts_kind("TTM")
    wild = (1.0, 6.0, 2.0, 6.5, 1.2, 5.8, 2.4, 6.9)
    for per, val in zip(sorted(f), wild):
        f[per]["EPS"] = val
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, _cv, _level = e.calibrate_eps_periodicity(f)
    assert kind == "UNKNOWN" and basis == "CALIBRATION_FAILED"


def test_chua_du_du_lieu_thi_theo_nguon_va_ghi_nhan():
    """Thiếu NET_INCOME ⇒ mặc định theo nguồn, nhãn SOURCE_DEFAULT (không giả vờ đã hiệu chuẩn)."""
    f = {p: {k: v for k, v in m.items() if k != "NET_INCOME"} for p, m in _facts_kind("TTM").items()}
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, dev, level = e.calibrate_eps_periodicity(f)
    assert (kind, basis, dev, level) == ("QUARTER", "SOURCE_DEFAULT", None, None)


def test_flow_kind_unknown_gan_co_va_khong_nhan_4():
    e = _engine(price=10_000.0)
    r = e.compute_ratios_for_period(
        "XXX", "2018Q2", _metrics(_fiscal_quarter=2, EPS=1_000.0, _flow_kind="UNKNOWN"), "STANDARD", shares_now=1e9
    )
    assert "EPS_PERIODICITY_UNKNOWN" in r["_flags"]
    assert r["_is_clean"] is False
    assert r["PE"] == pytest.approx(10.0)  # af = 1 (không ×4, không ×2)


# ── Chọn basis số cổ phiếu có guard pha loãng (phán quyết 2) ───────────────────


def test_select_shares_uu_tien_tcbs_trong_nguong():
    """SHB 2026Q2: tcbs 5.343.748.241 vs charter 4.594.200.000 = +16,3% ≤ 25% ⇒ dùng tcbs."""
    f = {"2026Q2": {"CHARTER_CAPITAL": 45_942.0e9, "SHARES_OUT": 5_343_748_241.0}}
    e = ValuationEngine.__new__(ValuationEngine)
    sh, basis, flags = e.select_shares_now(f)
    assert sh == pytest.approx(5_343_748_241.0)
    assert basis == "TCBS_REPORTED"
    assert "SHARES_DILUTION_FLAGGED" in flags


def test_select_shares_tu_choi_khi_lech_bat_thuong():
    """SHB 2010Q1: tcbs 2.668.817.204 vs charter 200.000.000 = +1.234% > 25% ⇒ charter + cờ."""
    f = {"2010Q1": {"CHARTER_CAPITAL": 2.0e12, "SHARES_OUT": 2_668_817_204.0}}
    e = ValuationEngine.__new__(ValuationEngine)
    sh, basis, flags = e.select_shares_now(f)
    assert sh == pytest.approx(200_000_000.0)
    assert basis == "CHARTER_PIT"
    assert "SHARES_BASIS_UNEXPLAINED" in flags


def test_canary_shb_hieu_chuan_ra_ttm_tu_store_that():
    """SHB (nguồn tcbs) PHẢI hiệu chuẩn ra TTM — đo 2026-10-16: EPS == TTM4/shares ×1,00 ở 6/6 kỳ."""
    try:
        eng = ValuationEngine()
        facts = eng.facts_db.get_facts("SHB", periods=200)
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:  # pragma: no cover - môi trường
        pytest.skip(f"không đọc được financial_facts.db: {exc}")
    if len(facts) < 8:
        pytest.skip("SHB chưa đủ kỳ trong store")
    kind, basis, _cv, _level = eng.calibrate_eps_periodicity(facts)
    assert (kind, basis) == ("TTM", "CALIBRATED")


# ── Phạm vi cờ theo ratio (sửa sau DoD B: 11.375 dòng PE bị loại oan) ──────────


def test_co_untraceable_khong_lam_ban_hang_PE():
    """Cờ của EV/EBITDA (EBITDA proxy) KHÔNG được dính sang hàng PE của cùng kỳ."""
    got = ValuationEngine.flags_for_ratio(["UNTRACEABLE_DENOMINATOR", "EPS_PERIODICITY_UNKNOWN"], "PE")
    assert got == ["EPS_PERIODICITY_UNKNOWN"]
    assert ValuationEngine.flags_for_ratio(["UNTRACEABLE_DENOMINATOR"], "EV_EBITDA") == ["UNTRACEABLE_DENOMINATOR"]


def test_co_basis_va_gia_ap_cho_moi_ratio():
    """Cờ giá/basis số cp ảnh hưởng cả 5 ratio ⇒ phải áp cho mọi ratio."""
    for r in ("PE", "PB", "PS", "EV_EBITDA", "ROE"):
        assert "SHARES_DILUTION_FLAGGED" in ValuationEngine.flags_for_ratio(["SHARES_DILUTION_FLAGGED"], r)
        assert "PRICE_STALE_ONLY" in ValuationEngine.flags_for_ratio(["PRICE_STALE_ONLY"], r)


def test_flag_scope_phu_het_cac_co_dang_dung():
    """Canary: mọi cờ đã sinh trong code phải có mặt trong FLAG_SCOPE (hoặc cố ý áp cho tất cả)."""
    engine_src = (ROOT / "backend" / "src" / "financial" / "valuation_engine.py").read_text(encoding="utf-8")
    emitted = {
        "PRICE_FALLBACK_FUTURE",
        "PRICE_STALE_ONLY",
        "PRICE_MISSING",
        "EPS_PERIODICITY_UNKNOWN",
        "NEGATIVE_EARNINGS",
        "DISTRESSED_EQUITY",
        "EXTREME_LOW_PE",
        "PERIODICITY_MISMATCH_EPS",
        "OUTLIER_WINSORED",
        "NEAR_ZERO_DENOMINATOR",
        "UNTRACEABLE_DENOMINATOR",
        "BASIS_MISMATCH",
        "SHARES_DILUTION_FLAGGED",
        "SHARES_BASIS_UNEXPLAINED",
        "SHARES_BASIS_UNVERIFIED",
    }
    for fl in emitted:
        assert f'"{fl}"' in engine_src, f"cờ {fl} biến mất khỏi engine?"
    scoped_or_global = set(ValuationEngine.FLAG_SCOPE) | {
        "PRICE_FALLBACK_FUTURE",
        "PRICE_STALE_ONLY",
        "PRICE_MISSING",
        "SHARES_DILUTION_FLAGGED",
        "SHARES_BASIS_UNEXPLAINED",
        "SHARES_BASIS_UNVERIFIED",
    }
    assert emitted <= scoped_or_global, f"cờ chưa khai báo phạm vi: {emitted - scoped_or_global}"


def test_co_pha_loang_la_thong_tin_khong_chan_xep_hang():
    """SHARES_DILUTION_FLAGGED = canary thông tin (phán quyết 2), KHÔNG tước tư cách xếp hạng.

    WHY: nếu cho nó chặn thì SHB/MBB/TCB (đã pha loãng thật) bị loại khỏi bảng xếp hạng dù số
    liệu ĐÚNG. Cờ >25% (`SHARES_BASIS_UNEXPLAINED`) mới là cờ chặn.
    """
    e = ValuationEngine.__new__(ValuationEngine)
    _sh, basis, flags = e.select_shares_now({"2026Q2": {"CHARTER_CAPITAL": 45_942.0e9, "SHARES_OUT": 5_343_748_241.0}})
    assert basis == "TCBS_REPORTED" and "SHARES_DILUTION_FLAGGED" in flags
    assert ValuationEngine.is_blocking(["SHARES_DILUTION_FLAGGED"]) is False
    assert ValuationEngine.is_blocking(["SHARES_DILUTION_FLAGGED", "SHARES_BASIS_UNEXPLAINED"]) is True
    assert ValuationEngine.is_blocking([]) is False

    # và tác động lên hàng điểm: chỉ cờ thông tin ⇒ vẫn được coi là "sạch"
    r = e.compute_ratios_for_period(
        "SHB", "2026Q2", {"_fiscal_year": 2026, "_fiscal_quarter": 2, "EPS": 2_272.0, "_flow_kind": "TTM",
                          "_base_flags": ["SHARES_DILUTION_FLAGGED"]}, "BANK", shares_now=5_343_748_241.0
    )
    assert "SHARES_DILUTION_FLAGGED" in r["_flags"]
    assert r["_is_clean"] is True


def test_nguong_hieu_chuan_bi_khoa_khong_duoc_noi():
    """KHÓA CỨNG ngưỡng hiệu chuẩn (phán quyết 2026-10-16: "TUYỆT ĐỐI KHÔNG nới dung sai 2%").

    WHY: nới dung sai là cách tự lừa mình — nó biến "không chứng minh được chu kỳ EPS" thành
    "coi như đúng". Độ phủ phải tăng bằng DỮ LIỆU/PHƯƠNG PHÁP, không bằng hạ chuẩn. Test này
    fail nếu ai đó nới ngưỡng mà không có phán quyết mới kèm bằng chứng đo được.
    """
    assert ValuationEngine.CALIB_TOL <= 0.02, "nới CALIB_TOL cần phán quyết mới + bằng chứng"
    assert ValuationEngine.SHRES_DIV_TOL_PCT <= 25.0, "nới ngưỡng lệch số cp cần bằng chứng mới"


def test_co_pha_loang_khong_nam_trong_FLAG_SCOPE_chan():
    """Cờ thông tin không được lọt vào nhóm cờ chặn (chống hồi quy)."""
    assert "SHARES_DILUTION_FLAGGED" in ValuationEngine.INFO_FLAGS
    assert "SHARES_DILUTION_FLAGGED" not in ValuationEngine.FLAG_SCOPE


# ── BƯỚC 5: tách CHU KỲ (shape) khỏi MỨC (level) — phán quyết 2026-10-16 ────────


def test_calibrate_lech_muc_van_nhan_dung_chu_ky():
    """Ca VNM: tỷ lệ 0,891 nhưng CV chỉ ~1,2% ⇒ chu kỳ QUARTER + lệch MỨC ~10,9% (cờ thông tin).

    WHY: đây là điều kiện cũ `|median − 1| ≤ 2%` đã bác bỏ OAN — lệch mức là khác biệt định
    nghĩa/mẫu số (lợi nhuận mẹ vs toàn phần), không phải sai chu kỳ.
    """
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, cv, level = e.calibrate_eps_periodicity(_facts_kind("SCALED"))
    assert (kind, basis) == ("QUARTER", "CALIBRATED")
    assert cv is not None and cv <= 0.02
    assert level == pytest.approx(10.9, abs=0.2)  # |0,891 − 1| = 10,9%


def test_calibrate_ca_phang_la_AMBIGUOUS():
    """NI phẳng ⇒ cửa sổ TTM trùng YTD ⇒ ≥2 quy ước cùng ổn định ⇒ PHẢI fail-closed.

    WHY: ở ca này chọn sai làm PE lệch tới 4× — không được đoán.
    """
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, _cv, _level = e.calibrate_eps_periodicity(_facts_kind("FLAT"))
    assert (kind, basis) == ("AMBIGUOUS", "AMBIGUOUS")


def test_calibrate_dao_dong_that_la_UNKNOWN():
    """NI dao động mạnh + EPS hằng ⇒ không quy ước nào ổn định ⇒ UNKNOWN (như CII ngoài store)."""
    e = ValuationEngine.__new__(ValuationEngine)
    kind, basis, cv, _level = e.calibrate_eps_periodicity(_facts_kind("NOISY"))
    assert kind == "UNKNOWN" and basis == "CALIBRATION_FAILED"
    assert cv is not None and cv > 0.02


def test_ambiguous_chan_con_level_mismatch_khong_chan():
    """AMBIGUOUS là cờ CHẶN; EPS_LEVEL_MISMATCH chỉ là cờ THÔNG TIN (Bước 5)."""
    assert ValuationEngine.is_blocking(["EPS_PERIODICITY_AMBIGUOUS"]) is True
    assert ValuationEngine.is_blocking(["EPS_LEVEL_MISMATCH"]) is False
    assert "EPS_LEVEL_MISMATCH" in ValuationEngine.INFO_FLAGS
    assert "EPS_PERIODICITY_AMBIGUOUS" not in ValuationEngine.INFO_FLAGS


def test_canary_vnm_quarter_va_co_lech_muc_tu_store_that():
    """VNM trong store thật: đo 2026-10-16 có tỷ lệ 0,891 / CV 1,2% ⇒ QUARTER + lệch mức."""
    try:
        eng = ValuationEngine()
        facts = eng.facts_db.get_facts("VNM", periods=200)
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:  # pragma: no cover - môi trường
        pytest.skip(f"không đọc được financial_facts.db: {exc}")
    if len(facts) < 8:
        pytest.skip("VNM chưa đủ kỳ trong store")
    kind, basis, cv, level = eng.calibrate_eps_periodicity(facts)
    assert kind == "QUARTER", f"VNM phải là QUARTER, nhận {kind}/{basis} (cv={cv}, level={level})"
    assert cv is not None and cv <= 0.02
    assert level is not None and level > 2.0, "VNM phải mang cờ lệch MỨC (~10,9%)"


def test_canary_cii_khong_duoc_tu_tin_ve_chu_ky():
    """CII (NI đổi dấu/gần 0): KHÔNG được kết luận chu kỳ — UNKNOWN hoặc AMBIGUOUS."""
    try:
        eng = ValuationEngine()
        facts = eng.facts_db.get_facts("CII", periods=200)
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:  # pragma: no cover - môi trường
        pytest.skip(f"không đọc được financial_facts.db: {exc}")
    if len(facts) < 8:
        pytest.skip("CII chưa đủ kỳ trong store")
    kind, basis, _cv, _level = eng.calibrate_eps_periodicity(facts)
    assert basis in ("CALIBRATION_FAILED", "AMBIGUOUS"), f"CII phải fail-closed, nhận {kind}/{basis}"
