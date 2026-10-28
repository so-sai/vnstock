"""
TEST CLI DIVIDEND SERVICE (PTCK_VNSTOCK)
========================================
[WHY] Đảm bảo dịch vụ CLI `dividend-audit` hoạt động tất định, chuẩn xác,
không văng ngoại lệ cho các mã benchmark (FOC, VEA, SMB) và fail-closed
đúng quy ước đối với các mã chưa hiệu chuẩn.
"""

from __future__ import annotations

import pytest

from src.research.cli_dividend_service import (
    SUPPORTED_BENCHMARKS,
    audit_single_symbol,
)


def test_audit_single_symbol_benchmarks():
    """Kiểm tra 3 mã benchmark trả về đầy đủ các chỉ số định lượng."""
    for sym in SUPPORTED_BENCHMARKS:
        res = audit_single_symbol(sym, initial_capital=1_000_000_000.0)
        assert res["symbol"] == sym
        assert res["bars_observed"] > 1400
        assert res["bank_final_nav"] > 1_300_000_000.0
        assert res["drip_final_wealth"] > 0
        assert "verdict" in res
        assert res["verdict"] in ("CASH_COW_COMPOUNDER", "DIVIDEND_YIELD_TRAP")


def test_audit_single_symbol_uncalibrated_fails():
    """Kiểm tra mã chưa hiệu chuẩn phải ném lỗi fail-closed."""
    with pytest.raises(ValueError, match="chưa hoàn tất đối soát provenance VSDC"):
        audit_single_symbol("UNKNOWN_SYM")


def test_audit_verdicts_consistency():
    """Xác nhận tính nhất quán của phán quyết: FOC là Bẫy cổ tức, VEA & SMB là Cỗ máy in tiền."""
    foc_res = audit_single_symbol("FOC")
    assert foc_res["verdict"] == "DIVIDEND_YIELD_TRAP"
    assert foc_res["alpha_vnd"] < 0

    vea_res = audit_single_symbol("VEA")
    assert vea_res["verdict"] == "CASH_COW_COMPOUNDER"
    assert vea_res["alpha_vnd"] > 0

    smb_res = audit_single_symbol("SMB")
    assert smb_res["verdict"] == "CASH_COW_COMPOUNDER"
    assert smb_res["alpha_vnd"] > 0
