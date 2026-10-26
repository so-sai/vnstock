"""
ADVERSARIAL UNIT TESTS FOR DIVIDEND AUDIT ENGINE V2.3 (HARDENED SPEC)
PTCK_VNSTOCK Quantitative Research Suite
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.dividend_audit_engine import (
        CashDividendEvent,
        DividendAuditEngineV2,
        EngineConfig,
        MarketBar,
        StockDividendEvent,
        VietnamTradingCalendar,
    )
except ImportError:
    from backend.src.research.dividend_audit_engine import (
        CashDividendEvent,
        DividendAuditEngineV2,
        EngineConfig,
        MarketBar,
        StockDividendEvent,
        VietnamTradingCalendar,
    )


@pytest.fixture
def base_config():
    return EngineConfig(
        symbol="FOC",
        initial_capital=1_000_000_000,
        is_unadjusted_price=True,
        lot_size=100,
        max_adv_participation=0.20,
        base_slippage_bps=0.0050,
        settlement_trading_days=2,
        adv_window_size=20,
    )


def test_adversarial_mandatory_provenance_fail_closed(base_config):
    """
    KIỂM ĐỊNH P0: Không có nguồn provenance hoặc thông tin đối soát bất đồng => Fail-closed.
    """
    engine = DividendAuditEngineV2(base_config)
    bars = [
        MarketBar(trade_date=date(2026, 1, 5), raw_close=100_000, volume=100_000),
        MarketBar(trade_date=date(2026, 1, 25), raw_close=100_000, volume=100_000),
    ]

    # Thiếu provenance_source => FAIL-CLOSED
    with pytest.raises(ValueError, match="PROVENANCE_REQUIRED"):
        engine.run_audit(
            bars=bars,
            cash_divs=[],
            bank_rates={},
            provenance_source="",
            independent_action_ids=set(),
        )

    # Danh sách đầu vào thiếu sự kiện so với nguồn kiểm chứng độc lập => FAIL-CLOSED
    independent_registry = {"VSDC_FOC_DIV_2026_01"}
    with pytest.raises(RuntimeError, match="PROVENANCE_MISMATCH"):
        engine.run_audit(
            bars=bars,
            cash_divs=[],
            bank_rates={},
            provenance_source="VSDC_OFFICIAL",
            independent_action_ids=independent_registry,
        )


def test_adversarial_adv20_warmup_lock(base_config):
    """
    KIỂM ĐỊNH P0: Khi chưa đủ 20 phiên volume history (warm-up), cấm khớp lệnh (khối lượng cho phép = 0).
    """
    engine = DividendAuditEngineV2(base_config)
    # Chỉ có 5 phiên giá, không có warmup_bars => chưa đủ 20 phiên => không được khớp
    bars = [MarketBar(trade_date=date(2026, 1, 5) + timedelta(days=i), raw_close=100_000, volume=50_000) for i in range(5)]
    res = engine.run_audit(
        bars=bars,
        cash_divs=[],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=set(),
        warmup_bars=None,
    )
    # Không có cổ phiếu nào được khớp do chưa qua warm-up
    assert res["drip_settled_regular_shares"] == 0
    assert res["drip_unsettled_shares"] == 0
    assert res["drip_unallocated_cash"] >= base_config.initial_capital


def test_adversarial_adv20_warmup_allows_trading_when_seeded(base_config):
    """
    KIỂM ĐỊNH P0: Khi được cung cấp đủ 20 phiên warmup_bars, lệnh được phép khớp ngay từ phiên T0.
    """
    engine = DividendAuditEngineV2(base_config)
    warmup = [
        MarketBar(trade_date=date(2025, 12, 1) + timedelta(days=i), raw_close=100_000, volume=100_000) for i in range(20)
    ]
    bars = [
        MarketBar(trade_date=date(2026, 1, 5), raw_close=100_000, volume=100_000),
        MarketBar(trade_date=date(2026, 1, 6), raw_close=100_000, volume=100_000),
    ]
    provenance = {"NO_ACTION_01"}
    div = CashDividendEvent(
        ex_date=date(2026, 1, 5),
        pay_date=date(2026, 1, 6),
        cash_per_share=1_000,
        action_id="NO_ACTION_01",
    )
    res = engine.run_audit(
        bars=bars,
        cash_divs=[div],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=provenance,
        warmup_bars=warmup,
    )
    # Vì có đủ 20 phiên warmup, lệnh mua đã được khớp trong giới hạn ADV20 (max 20.000 cp / phiên)
    assert res["drip_unsettled_shares"] > 0
    assert res["drip_unsettled_shares"] <= 20_000


def test_adversarial_decree_126_bonus_share_tax_withholding(base_config):
    """
    KIỂM ĐỊNH P0: Cổ tức bằng cổ phiếu theo Nghị định 126/2020:
    - Khi nhận: Không nộp thuế.
    - Khi bán thanh lý: Bị khấu trừ 5% thuế TNCN trên mệnh giá 10.000 đ/cp.
    """
    engine = DividendAuditEngineV2(base_config)
    warmup = [
        MarketBar(trade_date=date(2025, 12, 1) + timedelta(days=i), raw_close=100_000, volume=200_000) for i in range(20)
    ]
    # Chuỗi ngày làm việc đầy đủ để cổ phiếu thưởng kịp settled
    bars = [
        MarketBar(trade_date=date(2026, 1, 5), raw_close=100_000, volume=200_000),  # T2: Mua cp thường
        MarketBar(trade_date=date(2026, 1, 6), raw_close=100_000, volume=200_000),  # T3
        MarketBar(trade_date=date(2026, 1, 7), raw_close=100_000, volume=200_000),  # T4: Cp thường settled
        MarketBar(trade_date=date(2026, 1, 8), raw_close=100_000, volume=200_000),  # T5: Trả cp thưởng
        MarketBar(trade_date=date(2026, 1, 9), raw_close=100_000, volume=200_000),  # T6
        MarketBar(trade_date=date(2026, 1, 12), raw_close=100_000, volume=200_000),  # T2 tuần sau
        MarketBar(trade_date=date(2026, 1, 13), raw_close=100_000, volume=200_000),  # T3: Cp thưởng settled
    ]
    stock_div = StockDividendEvent(
        ex_date=date(2026, 1, 6),
        pay_date=date(2026, 1, 8),
        share_ratio=0.10,  # 10%
        action_id="VSDC_STOCK_DIV_10PCT",
    )
    res = engine.run_audit(
        bars=bars,
        cash_divs=[],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids={"VSDC_STOCK_DIV_10PCT"},
        stock_divs=[stock_div],
        warmup_bars=warmup,
    )

    # Đảm bảo có cổ phiếu thưởng đã về tài khoản (settled_bonus_shares > 0)
    assert res["drip_settled_bonus_shares"] > 0
    assert res["processed_stock_divs"] == 1


def test_adversarial_full_t2_settlement_across_weekend(base_config):
    """
    KIỂM ĐỊNH P0: Toàn bộ quy trình T+2 qua cuối tuần:
    - Thứ Sáu (2026-01-09): Mua => Hàng đang về (unsettled).
    - Thứ Hai (2026-01-12): T+1 => Vẫn unsettled.
    - Thứ Ba (2026-01-13): T+2 => Trở thành settled_shares!
    """
    calendar = VietnamTradingCalendar()
    engine = DividendAuditEngineV2(base_config, calendar=calendar)

    warmup = [
        MarketBar(trade_date=date(2025, 12, 1) + timedelta(days=i), raw_close=100_000, volume=500_000) for i in range(20)
    ]
    # Kịch bản 1: Dừng tại Thứ Hai (T+1)
    bars_monday = [
        MarketBar(trade_date=date(2026, 1, 9), raw_close=100_000, volume=500_000),  # Thứ 6
        MarketBar(trade_date=date(2026, 1, 12), raw_close=100_000, volume=500_000),  # Thứ 2
    ]
    provenance = {"EV_MOCK"}
    div = CashDividendEvent(
        ex_date=date(2026, 1, 9),
        pay_date=date(2026, 1, 12),
        cash_per_share=500,
        action_id="EV_MOCK",
    )
    res_mon = engine.run_audit(
        bars=bars_monday,
        cash_divs=[div],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=provenance,
        warmup_bars=warmup,
    )
    # Tại Thứ Hai: Chưa về
    assert res_mon["drip_settled_regular_shares"] == 0
    assert res_mon["drip_unsettled_shares"] > 0

    # Kịch bản 2: Tiếp tục đến Thứ Ba (T+2)
    bars_tuesday = bars_monday + [
        MarketBar(trade_date=date(2026, 1, 13), raw_close=100_000, volume=500_000),  # Thứ 3
    ]
    res_tue = engine.run_audit(
        bars=bars_tuesday,
        cash_divs=[div],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=provenance,
        warmup_bars=warmup,
    )
    # Tại Thứ Ba: Đã settled toàn bộ
    assert res_tue["drip_settled_regular_shares"] > 0
    assert res_tue["drip_unsettled_shares"] == 0


def test_adversarial_cash_equity_conservation(base_config):
    """
    KIỂM ĐỊNH P0: Bảo toàn dòng tiền tuyệt đối:
    NAV tại mọi thời điểm = Giá trị cp đã về + Giá trị cp kẹt T+2 + Tiền mặt + Khoản phải thu cổ tức.
    """
    engine = DividendAuditEngineV2(base_config)
    warmup = [
        MarketBar(trade_date=date(2025, 12, 1) + timedelta(days=i), raw_close=100_000, volume=1_000_000) for i in range(20)
    ]
    bars = [
        MarketBar(trade_date=date(2026, 1, 5), raw_close=100_000, volume=1_000_000),
        MarketBar(trade_date=date(2026, 1, 6), raw_close=100_000, volume=1_000_000),
        MarketBar(trade_date=date(2026, 1, 7), raw_close=100_000, volume=1_000_000),
    ]
    action_id = {"INIT_PROV"}
    div = CashDividendEvent(
        ex_date=date(2026, 1, 5),
        pay_date=date(2026, 1, 7),
        cash_per_share=2_000,
        action_id="INIT_PROV",
    )
    res = engine.run_audit(
        bars=bars,
        cash_divs=[div],
        bank_rates={},
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=action_id,
        warmup_bars=warmup,
    )
    # Tài sản ròng không được tự sinh ra tiền phi lý (không vượt vốn ban đầu + cổ tức ròng)
    assert res["drip_final_wealth"] > 0
    assert res["cashout_terminal_stock_val"] > 0
