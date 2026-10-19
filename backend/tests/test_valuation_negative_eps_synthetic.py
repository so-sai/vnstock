"""LUẬT NGHIỆP VỤ: EPS ≤ 0 ⇒ KHÔNG có PE, PHẢI gắn cờ `NEGATIVE_EARNINGS`, dòng KHÔNG sạch.

WHY dùng fixture TỔNG HỢP (quyết định 2026-10-19, user phê chuẩn): toàn bộ VN30 kỳ 2026Q2 đang lãi
(EPS > 0) ⇒ không còn ca âm thật trong store. Đi săn một mã lỗ bên ngoài sẽ biến test thành **flaky**
(khi doanh nghiệp đó có lãi/tái cấu trúc, test gãy vô lý). Quy tắc bất biến phải kiểm **logic code**,
không phụ thuộc tình hình kinh doanh của một công ty cụ thể.

ĐIỀU KIỆN KHÔNG ĐƯỢC PHÁ: phải có **ca đối chứng dương** (EPS > 0 ⇒ PE tồn tại, không có cờ âm)
để chứng minh test phân biệt được — nếu không, một engine "luôn không tạo PE" cũng sẽ pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.financial.valuation_engine import ValuationEngine

PRICE = 30_000.0


def _metrics(eps: float) -> dict:
    """Bản ghi fact tổng hợp: BVPS dương (để PB vẫn tính được) + các trường bắt buộc."""
    return {
        "_fiscal_year": 2026,
        "_fiscal_quarter": 2,
        "_flow_kind": "TTM",
        "EPS": eps,
        "SHARES_OUT": 100_000_000.0,
        "BOOK_VALUE_PS": 15_000.0,
        "NET_INCOME": -50e9 if eps <= 0 else 120e9,
        "TOTAL_EQUITY": 1_500e9,
        "REVENUE": 400e9,
    }


@pytest.fixture()
def eng(monkeypatch):
    e = ValuationEngine()
    # Không chạm store để lấy giá: giá được tiêm cố định ⇒ test thuần logic.
    monkeypatch.setattr(e, "_get_price_with_flag", lambda symbol, d: (PRICE, None))
    return e


def test_eps_am_khong_tao_PE_va_gan_co(eng):
    r = eng.compute_ratios_for_period("FAKE", "2026Q2", _metrics(-1_000.0), "STD")
    assert "PE" not in r, f"EPS âm KHÔNG được tạo PE: {r}"
    assert "NEGATIVE_EARNINGS" in r["_flags"], r["_flags"]
    assert r["_is_clean"] is False, "dòng EPS âm phải bị coi là KHÔNG sạch"


def test_eps_bang_0_cung_khong_tao_PE(eng):
    """Biên: EPS = 0 (không phải âm) vẫn không có PE và vẫn phải mang cờ."""
    r = eng.compute_ratios_for_period("FAKE", "2026Q2", _metrics(0.0), "STD")
    assert "PE" not in r
    assert "NEGATIVE_EARNINGS" in r["_flags"]


def test_dong_doi_chung_eps_duong_thi_CO_PE(eng):
    """CA ĐỐI CHỨNG: EPS > 0 ⇒ PE tồn tại (=30.000/4.772 ≈ 6,29) và KHÔNG có cờ âm."""
    r = eng.compute_ratios_for_period("FAKE", "2026Q2", _metrics(4_772.0), "STD")
    assert "PE" in r, r
    assert r["PE"] == pytest.approx(PRICE / 4_772.0, rel=1e-9)
    assert "NEGATIVE_EARNINGS" not in r["_flags"]


def test_cac_ratio_khac_khong_bi_anh_huong_boi_eps_am(eng):
    """EPS âm chỉ chặn PE — PB (từ BVPS) vẫn phải được tính để không mất thông tin định giá."""
    r = eng.compute_ratios_for_period("FAKE", "2026Q2", _metrics(-1_000.0), "STD")
    assert "PE" not in r
    assert r["PB"] == pytest.approx(PRICE / 15_000.0, rel=1e-9)
