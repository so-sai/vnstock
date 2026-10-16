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
    r = e.compute_ratios_for_period("HPG", "2018Q2", _metrics(_fiscal_quarter=2, EPS=2_000.0), "STANDARD", shares_now=6.4e9)
    assert r["PE"] == pytest.approx(10_000 / (2_000 * 2))


def test_pe_ytd_q4_khong_nhan_gi():
    """EPS cả năm ⇒ ×1. Bản cũ ×4 làm PE nhỏ đi 4 lần (đúng ca SHB 2024Q4: 3,37 → 0,84)."""
    e = _engine(price=7_760.0)
    r = e.compute_ratios_for_period("SHB", "2024Q4", _metrics(_fiscal_quarter=4, EPS=2_306.0), "BANK", shares_now=5e9)
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
    r = e.compute_ratios_for_period("XXX", "2018Q2", _metrics(_fiscal_quarter=2, EPS=3_000.0), "STANDARD", shares_now=1e9)
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
