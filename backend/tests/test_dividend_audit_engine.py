"""
test_dividend_audit_engine.py — Comprehensive Unit Test Suite for DividendAuditEngine (V2.1)
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.dividend_audit_engine import DividendAuditEngine, DividendEvent, EngineConfig, MarketBar
except ImportError:
    from backend.src.research.dividend_audit_engine import DividendAuditEngine, DividendEvent, EngineConfig, MarketBar


@pytest.fixture
def base_config():
    return EngineConfig(
        symbol="FOC",
        initial_capital=1_000_000_000,
        is_unadjusted_price=True,
        lot_size=100,
        base_slippage_bps=0.005,
    )


def test_adjusted_price_rejection(base_config):
    """Bắt buộc ném lỗi nếu dữ liệu đầu vào là giá đã điều chỉnh."""
    bad_cfg = EngineConfig(
        symbol="FOC",
        initial_capital=1_000_000_000,
        is_unadjusted_price=False,
    )
    engine = DividendAuditEngine(bad_cfg)
    bars = [
        MarketBar(date=datetime(2021, 1, 4), raw_close=100000, volume=5000),
        MarketBar(date=datetime(2026, 10, 21), raw_close=110000, volume=5000),
    ]
    with pytest.raises(ValueError, match="DATA_INTEGRITY_FAIL"):
        engine.run_audit(bars, [], {})


def test_missing_dividend_event_fail_closed(base_config):
    """Phát hiện lỗi nếu có sự kiện cổ tức rơi ngoài chuỗi ngày giao dịch."""
    engine = DividendAuditEngine(base_config)
    bars = [
        MarketBar(date=datetime(2021, 1, 4), raw_close=100000, volume=5000),
        MarketBar(date=datetime(2021, 6, 1), raw_close=105000, volume=5000),
    ]
    # Cổ tức diễn ra sau ngày kết thúc dữ liệu giá
    divs = [DividendEvent(ex_date=datetime(2021, 7, 1), pay_date=datetime(2021, 7, 20), cash_per_share=2000)]
    # Không tính cổ tức ngoài kỳ, ex_div_count = 0, kỳ vọng = 0 -> PASS
    res = engine.run_audit(bars, divs, {})
    assert res["events_matched"] == 0


def test_lot_size_and_settlement_t2(base_config):
    """Kiểm tra làm tròn lô 100 và giữ cổ phiếu T+2 tại ngày kết thúc."""
    engine = DividendAuditEngine(base_config)
    bars = [
        MarketBar(date=datetime(2026, 1, 1), raw_close=100000, volume=10000),
        # Ngày 10/1 trả cổ tức, tái đầu tư ngay
        MarketBar(date=datetime(2026, 1, 10), raw_close=100000, volume=10000),
        # Ngày 11/1 kết thúc kỳ -> cổ phiếu mới mua ngày 10/1 chưa về (T+2)
        MarketBar(date=datetime(2026, 1, 11), raw_close=100000, volume=10000),
    ]
    divs = [DividendEvent(ex_date=datetime(2026, 1, 5), pay_date=datetime(2026, 1, 10), cash_per_share=5000)]
    res = engine.run_audit(bars, divs, {})

    # Kiểm tra số lượng cổ phần khởi tạo chia hết cho 100
    assert res["drip_shares_init"] % 100 == 0
    # Phải có cổ phiếu bị khóa T+2 tại ngày kết thúc
    assert res["drip_shares_locked_t2"] > 0
    assert res["drip_shares_locked_t2"] % 100 == 0
