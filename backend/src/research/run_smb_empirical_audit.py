"""
EMPIRICAL AUDIT RUNNER: SMB (SABECO MIEN TRUNG) VS 12-MONTH BANK DEPOSITS (2021-2026)
Quantitative Research Suite — PTCK_VNSTOCK (Pha 3 — NQ-06/NQ-HĐKT)

Tuân thủ nghiêm ngặt Hiến pháp PTCK_VNSTOCK:
1. Phục nguyên giá thô (unadjusted raw price) 100% bội số lưới tick HOSE 50 VND (Invariant RAW_PRICE_TICK_GRID_PROOF_REQUIRED).
2. Cổng kiểm định provenance: assert_raw_price_series(tick_size=50.0) trước khi nạp vào engine.
3. Provenance VSDC: 12 sự kiện cổ tức tiền mặt độc lập trong kỳ kiểm toán (2021-2026, tổng 20.500 đ/cp).
4. Vi cấu trúc thị trường: Lô 100 cp (HOSE), trượt giá 50 bps, trần thanh khoản 20% ADV20, chu kỳ T+2.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from src.research.dividend_audit_engine import (
        CashDividendEvent,
        DividendAuditEngineV2,
        EngineConfig,
        MarketBar,
        VietnamTradingCalendar,
    )
    from src.research.price_provenance_gate import (
        assert_raw_price_series,
        tick_compliance,
    )
    from src.research.raw_price_reconstruction import (
        CashEvent,
        reconstruct_raw_prices,
    )
except ImportError:
    from backend.src.research.dividend_audit_engine import (
        CashDividendEvent,
        DividendAuditEngineV2,
        EngineConfig,
        MarketBar,
        VietnamTradingCalendar,
    )
    from backend.src.research.price_provenance_gate import (
        assert_raw_price_series,
        tick_compliance,
    )
    from backend.src.research.raw_price_reconstruction import (
        CashEvent,
        reconstruct_raw_prices,
    )

# 13 sự kiện cổ tức tiền mặt VSDC của SMB (bao gồm 1 đợt Dec-2020 để phục nguyên giá thô chuẩn)
SMB_RECON_EVENTS = (
    CashEvent(date(2020, 12, 22), 1_000.0),
    CashEvent(date(2021, 10, 22), 2_500.0),
    CashEvent(date(2022, 2, 18), 1_000.0),
    CashEvent(date(2022, 9, 20), 2_500.0),
    CashEvent(date(2023, 2, 20), 1_000.0),
    CashEvent(date(2023, 7, 17), 2_500.0),
    CashEvent(date(2023, 10, 24), 1_000.0),
    CashEvent(date(2024, 5, 22), 1_500.0),
    CashEvent(date(2024, 9, 16), 2_000.0),
    CashEvent(date(2025, 1, 7), 1_500.0),
    CashEvent(date(2025, 8, 13), 2_000.0),
    CashEvent(date(2025, 11, 12), 2_000.0),
    CashEvent(date(2026, 5, 25), 1_000.0),
)


def load_smb_clean_bars() -> tuple[list[MarketBar], list[MarketBar], tuple[tuple[date, date, float], ...]]:
    """Tải chuỗi dữ liệu SMB và phục nguyên giá thô (unadjusted raw price)
    theo chuẩn tick grid HOSE 50 VND (Invariant RAW_PRICE_TICK_GRID_PROOF_REQUIRED).
    """
    json_path = BACKEND_DIR / "data" / "kbs_smb_2021_2026.json"
    if not json_path.exists():
        json_path = Path(r"C:\Users\Admin\AppData\Local\Temp\opencode\kbs_smb_2021_2026.json")
    if not json_path.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp dữ liệu KBS SMB: {json_path}")

    raw_data = json.loads(json_path.read_text(encoding="utf-8"))
    dates = [datetime.strptime(r["t"][:10], "%Y-%m-%d").date() for r in raw_data]
    closes = [float(r["c"]) for r in raw_data]
    volumes = [float(r["v"]) for r in raw_data]

    # Phục nguyên giá thô với tick 50 VND đặc thù khoảng giá 10k-50k trên HOSE
    recon_report = reconstruct_raw_prices("SMB", dates, closes, events=SMB_RECON_EVENTS, tick=50.0)

    # Cổng kiểm định vật lý: Kiểm tra 100% bar nằm trên lưới 50 VND (fail-closed)
    gate_rep = tick_compliance("SMB_RECONSTRUCTED", recon_report.raw_closes, tick_size=50.0)
    assert_raw_price_series(gate_rep)

    audit_bars = []
    warmup_bars = []
    for d, p, v in zip(dates, recon_report.raw_closes, volumes):
        if d < date(2021, 1, 4):
            warmup_bars.append(MarketBar(trade_date=d, raw_close=p, volume=v))
        elif date(2021, 1, 4) <= d <= date(2026, 10, 23):
            audit_bars.append(MarketBar(trade_date=d, raw_close=p, volume=v))

    # Cắt đúng 20 phiên cuối của Dec 2020 làm warm-up window ADV20
    warmup_bars = warmup_bars[-20:]
    return audit_bars, warmup_bars, recon_report.segments


def trace_initial_execution(
    bars: list[MarketBar],
    warmup_bars: list[MarketBar],
    initial_capital: float,
    lot_size: int,
    slippage_bps: float,
    fee_trade: float,
    max_part: float,
) -> list[dict]:
    """Truy vết chi tiết từng phiên giải ngân ban đầu cho 1 tỷ đồng."""
    vol_hist = [wb.volume for wb in warmup_bars]
    cash = initial_capital
    fills = []

    for bar in bars:
        vol_hist.append(bar.volume)
        adv20 = sum(vol_hist[-20:]) / 20.0
        max_sh = int((adv20 * max_part) // lot_size) * lot_size
        cost_per_sh = bar.raw_close * (1.0 + slippage_bps) * (1.0 + fee_trade)
        sh_afford = int(cash // cost_per_sh // lot_size) * lot_size
        sh_bought = min(sh_afford, max_sh)

        if sh_bought > 0:
            spent = sh_bought * cost_per_sh
            cash -= spent
            fills.append(
                {
                    "date": bar.trade_date,
                    "adv20": adv20,
                    "shares_bought": sh_bought,
                    "price": bar.raw_close,
                    "cost_spent": spent,
                    "cash_remaining": cash,
                }
            )
        if cash < cost_per_sh * lot_size:
            break

    return fills


def run_smb_audit():
    calendar = VietnamTradingCalendar()
    bars, warmup_bars, segments = load_smb_clean_bars()
    if not bars:
        raise RuntimeError("Không thể tải dữ liệu sạch của SMB sau khi phục nguyên giá thô.")

    # 12 sự kiện cổ tức độc lập VSDC của SMB trong giai đoạn kiểm toán (2021-2026)
    vsdc_cash_dividends = [
        CashDividendEvent(
            ex_date=date(2021, 10, 22),
            pay_date=date(2021, 11, 15),
            cash_per_share=2_500.0,
            action_id="VSDC/SMB/20211022/CASH_2500",
        ),
        CashDividendEvent(
            ex_date=date(2022, 2, 18),
            pay_date=date(2022, 3, 15),
            cash_per_share=1_000.0,
            action_id="VSDC/SMB/20220218/CASH_1000",
        ),
        CashDividendEvent(
            ex_date=date(2022, 9, 20),
            pay_date=date(2022, 10, 14),
            cash_per_share=2_500.0,
            action_id="VSDC/SMB/20220920/CASH_2500",
        ),
        CashDividendEvent(
            ex_date=date(2023, 2, 20),
            pay_date=date(2023, 3, 15),
            cash_per_share=1_000.0,
            action_id="VSDC/SMB/20230220/CASH_1000",
        ),
        CashDividendEvent(
            ex_date=date(2023, 7, 17),
            pay_date=date(2023, 8, 15),
            cash_per_share=2_500.0,
            action_id="VSDC/SMB/20230717/CASH_2500",
        ),
        CashDividendEvent(
            ex_date=date(2023, 10, 24),
            pay_date=date(2023, 11, 15),
            cash_per_share=1_000.0,
            action_id="VSDC/SMB/20231024/CASH_1000",
        ),
        CashDividendEvent(
            ex_date=date(2024, 5, 22),
            pay_date=date(2024, 6, 14),
            cash_per_share=1_500.0,
            action_id="VSDC/SMB/20240522/CASH_1500",
        ),
        CashDividendEvent(
            ex_date=date(2024, 9, 16),
            pay_date=date(2024, 10, 15),
            cash_per_share=2_000.0,
            action_id="VSDC/SMB/20240916/CASH_2000",
        ),
        CashDividendEvent(
            ex_date=date(2025, 1, 7),
            pay_date=date(2025, 1, 24),
            cash_per_share=1_500.0,
            action_id="VSDC/SMB/20250107/CASH_1500",
        ),
        CashDividendEvent(
            ex_date=date(2025, 8, 13),
            pay_date=date(2025, 9, 15),
            cash_per_share=2_000.0,
            action_id="VSDC/SMB/20250813/CASH_2000",
        ),
        CashDividendEvent(
            ex_date=date(2025, 11, 12),
            pay_date=date(2025, 12, 15),
            cash_per_share=2_000.0,
            action_id="VSDC/SMB/20251112/CASH_2000",
        ),
        CashDividendEvent(
            ex_date=date(2026, 5, 25),
            pay_date=date(2026, 6, 15),
            cash_per_share=1_000.0,
            action_id="VSDC/SMB/20260525/CASH_1000",
        ),
    ]
    independent_provenance_set = {d.action_id for d in vsdc_cash_dividends}

    # Lãi suất bình quân tiền gửi 12 tháng nhóm NHTM Nhà nước (Big 4) 2021-2026
    bank_rates_big4 = {
        2021: 0.0560,
        2022: 0.0740,
        2023: 0.0550,
        2024: 0.0520,
        2025: 0.0530,
        2026: 0.0540,
    }

    cfg = EngineConfig(
        symbol="SMB",
        initial_capital=1_000_000_000.0,
        is_unadjusted_price=True,
        lot_size=100,
        tax_dividend_cash=0.05,
        fee_trade=0.0015,
        tax_sell=0.0010,
        max_adv_participation=0.20,
        base_slippage_bps=0.0050,
        non_term_bank_rate=0.0020,
        settlement_trading_days=2,
        adv_window_size=20,
    )

    # 1. Truy vết giải ngân ban đầu
    entry_fills = trace_initial_execution(
        bars,
        warmup_bars,
        cfg.initial_capital,
        cfg.lot_size,
        cfg.base_slippage_bps,
        cfg.fee_trade,
        cfg.max_adv_participation,
    )

    # 2. Chạy cỗ máy kiểm toán V2.3
    engine = DividendAuditEngineV2(cfg, calendar=calendar)
    res = engine.run_audit(
        bars=bars,
        cash_divs=vsdc_cash_dividends,
        bank_rates=bank_rates_big4,
        provenance_source="VSDC_OFFICIAL",
        independent_action_ids=independent_provenance_set,
        warmup_bars=warmup_bars,
    )

    # =====================================================================
    # XUẤT BẢN BÁO CÁO KIỂM TOÁN 4 LỚP
    # =====================================================================
    print("=" * 86)
    print("🛡️ BÁO CÁO KIỂM TOÁN ĐỊNH LƯỢNG THỰC NGHIỆM: SMB VS TIỀN GỬI 12 THÁNG")
    print("Cơ quan chủ trì: Hội đồng Kiến trúc & Kiểm toán Định lượng PTCK_VNSTOCK (Pha 3)")
    print(f"Khoảng thời gian: {bars[0].trade_date} đến {bars[-1].trade_date} ({res['period_years']:.2f} năm)")
    print(f"Số phiên quan sát: {res['bars_observed']} phiên | Phục nguyên giá thô: 100% bội số 50 VND (HOSE)")
    print(f"Nguồn đối soát cổ tức: VSDC Official ({res['processed_cash_divs']} kỳ tiền mặt, tổng 20.500 đ/cp)")
    print("=" * 86)

    # Câu hỏi 1
    print("\n[CÂU HỎI 1] THỜI GIAN VÀ TIẾN TRÌNH GIẢI NGÂN BAN ĐẦU (1 TỶ ĐỒNG VÀO SMB):")
    print("  • Cơ chế khớp lệnh: Order Slicing theo lô 100, trần 20% ADV20, phạt trượt giá 50 bps.")
    print(f"  • Tổng số phiên cần để giải ngân hết: {len(entry_fills)} phiên giao dịch.")
    print("  • Nhật ký khớp lệnh từng phiên:")
    tot_sh = 0
    tot_sp = 0.0
    for f in entry_fills:
        tot_sh += f["shares_bought"]
        tot_sp += f["cost_spent"]
        print(
            f"    - Phiên {f['date']}: Khớp {f['shares_bought']:>5,d} cp @ {f['price']:>7,.0f} đ "
            f"| ADV20: {f['adv20']:>6,.0f} | Tiền mua: {f['cost_spent']:>11,.0f} đ "
            f"| Tiền dư: {f['cash_remaining']:>11,.0f} đ"
        )
    print(f"  • Tổng cổ phần khởi tạo: {tot_sh:,d} cp | Tổng vốn đã giải ngân: {tot_sp:,.0f} đ")
    print(f"  • Tiền mặt lẻ tồn dư (không đủ mua lô 100): {entry_fills[-1]['cash_remaining']:,.0f} đ")

    # Câu hỏi 2
    print("\n[CÂU HỎI 2] HIỆU SUẤT TỔNG TÀI SẢN DRIP VS TIỀN GỬI 12 THÁNG:")
    print("  A. BENCHMARK TIỀN GỬI NGÂN HÀNG 12 THÁNG (BIG 4 - TT 04/2022/TT-NHNN):")
    stub_str = f"{res['bank_stub_days']} ngày lẻ (lãi KKH 0.2%/năm)"
    print(f"     - Số chu kỳ 365 ngày trọn vẹn: {res['bank_full_rolls']} kỳ + {stub_str}")
    print(f"     - Tổng tài sản cuối kỳ:        {res['bank_final_nav']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {res['bank_cagr'] * 100:>15.2f}%/năm")

    print("\n  B. CHIẾN LƯỢC DRIP (TÁI ĐẦU TƯ TOÀN BỘ CỔ TỨC + PHÍ/THUẾ/TRƯỢT GIÁ THOÁI VỐN):")
    drip_sh = res["drip_settled_regular_shares"]
    diff_sh = drip_sh - tot_sh
    print(f"     - Tích lũy cổ phần:            {tot_sh:,d} cp  --->  {drip_sh:,.0f} cp (+{diff_sh:,.0f} cp)")
    print(f"     - Cổ phiếu kẹt T+2 cuối kỳ:    {res['drip_unsettled_shares']:>16,.0f} cp")
    print(f"     - Tổng tài sản thanh lý ròng:  {res['drip_final_wealth']:>16,.0f} VND")
    print(f"     - Lợi suất kép bình quân (CAGR): {res['drip_cagr'] * 100:>15.2f}%/năm")

    alpha_vnd = res["drip_final_wealth"] - res["bank_final_nav"]
    alpha_cagr = res["drip_cagr"] - res["bank_cagr"]
    if alpha_vnd > 0:
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: CHIẾN LƯỢC DRIP THẮNG ÁP ĐẢO TIỀN GỬI NGÂN HÀNG!")
        print(f"      + Chênh lệch tài sản tuyệt đối: +{alpha_vnd:,.0f} VND (+{alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      + Tỷ suất sinh lời vượt trội (Alpha): +{alpha_cagr * 100:.2f}%/năm")
    else:
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: TIỀN GỬI NGÂN HÀNG THẮNG DRIP!")
        print(f"      - Tiền gửi ngân hàng vượt trội: +{-alpha_vnd:,.0f} VND (+{-alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      - Alpha âm của chiến lược DRIP: {alpha_cagr * 100:.2f}%/năm")

    # Câu hỏi 3
    print("\n[CÂU HỎI 3] KỊCH BẢN RÚT CỔ TỨC (CASH-OUT) & RỦI RO SỤT GIẢM (MAX DRAWDOWN):")
    price_change = (bars[-1].raw_close - bars[0].raw_close) / bars[0].raw_close * 100
    print(f"  • (B1) Giá trị cổ phiếu còn lại cuối kỳ (sau thoái vốn): {res['cashout_terminal_stock_val']:>14,.0f} VND")
    print(
        f"        (Gốc cổ phần biến động {price_change:+.1f}% do thị giá từ "
        f"{bars[0].raw_close:,.0f} đ -> {bars[-1].raw_close:,.0f} đ)"
    )
    spent_pct = res["cashout_spent_dividends"] / cfg.initial_capital * 100
    print(f"  • (B2) Tổng tiền mặt cổ tức thực nhận đã rút (sau 5% thuế): {res['cashout_spent_dividends']:>14,.0f} VND")
    print(f"        (Đã thu hồi thuần bằng tiền mặt: {spent_pct:.2f}% vốn ban đầu)")
    saved_val = res["cashout_wealth_if_saved"]
    saved_cagr = res["cashout_cagr_if_saved"] * 100
    saved_alpha = saved_val - res["bank_final_nav"]
    print(f"  • (B3) Tổng tài sản nếu gửi tiết kiệm dòng cổ tức rút ra: {saved_val:>14,.0f} VND (CAGR: {saved_cagr:.2f}%/năm)")
    if saved_alpha > 0:
        print(f"        (Kịch bản B3 cũng vượt tiền gửi ngân hàng: +{saved_alpha:,.0f} VND thặng dư)")
    else:
        print(f"        (Kịch bản B3 kém tiền gửi ngân hàng: -{-saved_alpha:,.0f} VND)")
    print(f"  • Rủi ro sụt giảm danh mục tối đa (Max Drawdown):        {res['max_drawdown_observed'] * 100:>14.2f}%")
    print("=" * 86)


if __name__ == "__main__":
    run_smb_audit()
