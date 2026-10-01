"""TDD: _score_efficiency — BANK không dùng Asset Turnover (A3).

Regression 2026-10-01: Eff của ngân hàng bị kéo xuống bằng AT kiểu nhà máy
(VCB AT=0.017 → score 0.083) vì tham số `entity` bị bỏ trống trong
company_health_v2._score_efficiency. Cấm tiệt AT cho nhóm tài chính —
NIM/ROE là thước đo hiệu quả thật của ngân hàng.

Test dùng quan hệ cơ chế (relative), không hardcode điểm tuyệt đối —
vì z-score/trend trên series phẳng không ổn định số học.

Run:  python -m pytest tests/test_health_v2_bank_efficiency.py -q  (từ backend/)
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.financial.company_health_v2 import CompanyHealthV2

PERIODS = [f"{y}Q{q}" for y in range(2021, 2026) for q in range(1, 5)]


def _series(val: float) -> list:
    return [(p, val) for p in PERIODS]


def _ratios(with_at: bool, with_nim: bool = False) -> dict:
    r = {"ROE": _series(0.05)}
    if with_at:
        r["ASSET_TURNOVER"] = _series(0.0)
    if with_nim:
        r["NIM"] = _series(0.05)
    return r


class TestBankExcludesAssetTurnover:
    def test_bank_ignores_asset_turnover_key(self):
        engine = CompanyHealthV2()
        with_at = engine._score_efficiency(_ratios(with_at=True), "BANK", {})
        without = engine._score_efficiency(_ratios(with_at=False), "BANK", {})
        assert with_at == pytest.approx(without)

    def test_standard_averages_asset_turnover_in(self):
        """AT=0.0 → điểm 0 bị đưa vào trung bình: mean(R, 0) = R/2."""
        engine = CompanyHealthV2()
        with_at = engine._score_efficiency(_ratios(with_at=True), "STANDARD", {})
        without = engine._score_efficiency(_ratios(with_at=False), "STANDARD", {})
        assert with_at == pytest.approx(without / 2)

    def test_bank_keeps_nim_component(self):
        """NIM 5% → điểm 1.0 vào trung bình của BANK: mean(R, 1.0)."""
        engine = CompanyHealthV2()
        with_nim = engine._score_efficiency(_ratios(with_at=True, with_nim=True), "BANK", {})
        roe_only = engine._score_efficiency(_ratios(with_at=True, with_nim=False), "BANK", {})
        assert with_nim == pytest.approx((roe_only + 1.0) / 2)

    def test_bank_without_any_input_returns_zero(self):
        engine = CompanyHealthV2()
        assert engine._score_efficiency({}, "BANK", {}) == 0.0

    def test_live_vcb_efficiency_not_dragged_by_asset_turnover(self):
        """Smoke: VCB (BANK) Eff phải > thành phần AT đơn thuần (0.083)."""
        engine = CompanyHealthV2()
        state = engine.analyze("VCB")
        assert state is not None
        assert state.entity_type == "BANK"
        assert state.organs.efficiency > 0.083
