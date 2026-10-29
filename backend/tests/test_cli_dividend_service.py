"""
TEST CLI DIVIDEND SERVICE (PTCK_VNSTOCK)
========================================
[WHY] Đảm bảo dịch vụ CLI `dividend-audit` hoạt động tất định, chuẩn xác,
không văng ngoại lệ cho các mã benchmark (FOC, VEA, SMB, PAT, DSN) và
fail-closed đúng quy ước đối với các mã chưa hiệu chuẩn.

[WHY] Bộ test này còn là chốt chặn CHỐNG TRÔI SSOT: CLI từng có BA bản sao danh
sách cổ tức inline (FOC/VEA/SMB) song song với `cash_event_registry`, và bản sao
FOC ghi pay_date 2023-06-30 trong khi SSOT là 2023-06-19 — CLI và runner báo hai
con số lãi tiền gửi khác nhau cho cùng một mã. Test dưới đây khoá: (a) CLI không
được chứa literal `CashDividendEvent(` (bản sao thứ sáu), (b) danh sách mã suy ra
từ registry, (c) nhãn sàn lấy từ registry (VEA là HOSE, không phải UPCoM).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.research.cash_event_registry import CASH_EVENTS, EXCHANGES, TICK_SIZES
from src.research.cli_dividend_service import (
    SUPPORTED_BENCHMARKS,
    audit_single_symbol,
)

CLI_SOURCE = Path(__file__).resolve().parent.parent / "src" / "research" / "cli_dividend_service.py"

# [WHY] Số bar tối thiểu theo LỊCH SỬ NIÊM YẾT, không dùng một ngưỡng chung:
# PAT lên UPCoM 06/2022 nên chuỗi chỉ ~1.049 phiên; đòi >1.400 như trước là
# chặn oan chính mã mà NQ-08 yêu cầu. Ngưỡng vẫn đủ chặt để phát hiện mất bar.
MIN_BARS = {"FOC": 1400, "VEA": 1400, "SMB": 1400, "PAT": 1000, "DSN": 1400}


def test_audit_single_symbol_benchmarks():
    """[WHY] 5/5 mã benchmark phải chạy được và trả đủ chỉ số; nếu một mã rơi khỏi
    danh sách, bảng so sánh của Hội đồng chỉ còn 4 dòng mà không ai báo lỗi."""
    for sym in SUPPORTED_BENCHMARKS:
        res = audit_single_symbol(sym, initial_capital=1_000_000_000.0)
        assert res["symbol"] == sym
        assert res["bars_observed"] >= MIN_BARS[sym]
        # Tiền gửi 12 tháng luôn sinh lời dương ⇒ NAV cuối kỳ phải > vốn gốc.
        assert res["bank_final_nav"] > res["initial_capital"]
        assert res["drip_final_wealth"] > 0
        assert "verdict" in res
        assert res["verdict"] in ("CASH_COW_COMPOUNDER", "DIVIDEND_YIELD_TRAP")


def test_supported_benchmarks_are_registry_driven():
    """[WHY] Thêm/bớt mã trong `cash_event_registry` phải tự động lan sang CLI.
    Khoá hai chiều để không ai quay lại khai báo cứng tuple 5 phần tử."""
    assert set(SUPPORTED_BENCHMARKS) == set(CASH_EVENTS)
    assert len(SUPPORTED_BENCHMARKS) == 5


def test_cli_has_no_inline_dividend_copy():
    """[WHY] Chốt chặn vá quá tay: nếu ai đó "cho nhanh" thêm lại danh sách cổ
    tức inline (đúng lỗi vừa xóa ở Pha 2B), test này lộ ngay thay vì đợi tới khi
    báo cáo CLI lệch số với runner vài tháng sau."""
    src = CLI_SOURCE.read_text(encoding="utf-8")
    assert "CashDividendEvent(" not in src, "CLI không được định nghĩa cổ tức inline — dùng build_vsdc_dividends"
    assert "build_vsdc_dividends" in src


def test_exchange_and_tick_follow_registry():
    """[WHY] Nhãn sàn từng in SAI "VEA — UPCoM" (VEA là HOSE) vì viết inline tại
    chỗ dispatch; lỗi này không làm hỏng số học nên rất khó phát hiện, nhưng nó
    khiến người đọc hiểu sai cả biên trần/sàn lẫn cơ chế giá của báo cáo."""
    assert audit_single_symbol("VEA")["exchange"] == "HOSE"
    for sym in SUPPORTED_BENCHMARKS:
        res = audit_single_symbol(sym, initial_capital=1_000_000_000.0)
        assert res["exchange"] == EXCHANGES[sym]
        assert res["tick_size"] == TICK_SIZES[sym]


def test_foc_numbers_match_empirical_runner_anchors():
    """[WHY] Số neo CLI-vs-runner: runner Pha 1 (đã nghiệm thu) công bố DRIP
    Alpha −4,98%/năm và B3 = 1.166.176.990 đ cho FOC. CLI dùng chung SSOT cổ tức
    nên PHẢI ra đúng hai con số đó; lệch ⇒ hoặc cổ tức trôi, hoặc chuỗi giá trôi.
    Đây chính là cặp số đã lệch trước khi xóa bản sao inline (pay_date sai 11 ngày).
    """
    res = audit_single_symbol("FOC", initial_capital=1_000_000_000.0)
    assert res["alpha_cagr"] == pytest.approx(-0.0498, abs=5e-5)
    assert res["cashout_wealth_if_saved"] == 1_166_176_990


def test_audit_single_symbol_uncalibrated_fails():
    """[WHY] Mã ngoài registry phải FAIL-CLOSED (Missing > Fake), không được trả
    báo cáo rỗng hay rơi vào nhánh mặc định."""
    with pytest.raises(ValueError, match="chưa hoàn tất đối soát provenance VSDC"):
        audit_single_symbol("UNKNOWN_SYM")


def test_audit_verdicts_consistency():
    """[WHY] Xác nhận tính nhất quán của phán quyết trên các mã đã nghiệm thu:
    FOC/PAT/DSN là bẫy cổ tức, VEA/SMB là cỗ máy in tiền. Đảo một dấu ⇒ hoặc
    engine sai, hoặc dữ liệu đầu vào đã bị chỉnh để "cho đẹp"."""
    foc_res = audit_single_symbol("FOC")
    assert foc_res["verdict"] == "DIVIDEND_YIELD_TRAP"
    assert foc_res["alpha_vnd"] < 0

    vea_res = audit_single_symbol("VEA")
    assert vea_res["verdict"] == "CASH_COW_COMPOUNDER"
    assert vea_res["alpha_vnd"] > 0

    smb_res = audit_single_symbol("SMB")
    assert smb_res["verdict"] == "CASH_COW_COMPOUNDER"
    assert smb_res["alpha_vnd"] > 0

    pat_res = audit_single_symbol("PAT")
    assert pat_res["verdict"] == "DIVIDEND_YIELD_TRAP"
    assert pat_res["alpha_vnd"] < 0

    dsn_res = audit_single_symbol("DSN")
    assert dsn_res["verdict"] == "DIVIDEND_YIELD_TRAP"
    assert dsn_res["alpha_vnd"] < 0
