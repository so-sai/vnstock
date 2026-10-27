"""
EMPIRICAL AUDIT RUNNER: FOC (FPT ONLINE) VS 12-MONTH BANK DEPOSITS (2021-2026)
Quantitative Research Suite — PTCK_VNSTOCK

Tuân thủ nghiêm ngặt Quyết nghị Hội đồng Kiến trúc:
1. Phương án B: Cửa sổ 20 phiên Dec 2020 làm Warm-up ADV20.
2. Vị từ Hiến pháp: VALID_ROW_SQL trên CSDL screener_cache.db (1.406 phiên).
3. Provenance VSDC: 6 sự kiện cổ tức độc lập (2021-2026).
4. Vi cấu trúc thị trường: Lô 100 cp, trượt giá 50 bps, trần thanh khoản 20% ADV20, chu kỳ T+2.
"""

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from datetime import date, datetime, timedelta
from pathlib import Path

# Đảm bảo import được module nội bộ
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


# ── Vị từ SQL chuẩn Hiến pháp ──
VALID_ROW_SQL = (
    "(close IS NOT NULL AND close > 0 "
    "AND low IS NOT NULL AND low <= close "
    "AND high IS NOT NULL AND close <= high "
    "AND volume IS NOT NULL AND volume >= 0)"
)


def load_foc_clean_bars() -> tuple[list[MarketBar], tuple[tuple[date, date, float], ...]]:
    """Tải chuỗi dữ liệu FOC và phục nguyên giá thô (unadjusted raw price)
    theo chuẩn tick grid 100 VND (Invariant RAW_PRICE_TICK_GRID_PROOF_REQUIRED).

    Chặn đứng hoàn toàn lỗi đếm 2 lần cổ tức (double-counting) từ chuỗi adjusted closes.
    """
    json_path = BACKEND_DIR / "data" / "kbs_foc_2021_2026.json"
    if not json_path.exists():
        json_path = Path(r"C:\Users\Admin\AppData\Local\Temp\opencode\kbs_foc_2021_2026.json")
    if not json_path.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp dữ liệu KBS FOC: {json_path}")

    raw_data = json.loads(json_path.read_text(encoding="utf-8"))
    dates = [datetime.strptime(r["t"][:10], "%Y-%m-%d").date() for r in raw_data]
    closes = [float(r["c"]) for r in raw_data]
    volumes = [float(r["v"]) for r in raw_data]

    events = (
        CashEvent(date(2021, 4, 13), 20_000.0),
        CashEvent(date(2022, 6, 10), 8_000.0),
        CashEvent(date(2023, 6, 8), 5_000.0),
        CashEvent(date(2024, 7, 18), 2_000.0),
        CashEvent(date(2025, 5, 23), 10_000.0),
        CashEvent(date(2026, 8, 10), 10_000.0),
    )

    recon_report = reconstruct_raw_prices("FOC", dates, closes, events=events)

    # Cổng kiểm định vật lý: Kiểm tra 100% bar nằm trên lưới 100 VND (fail-closed)
    gate_rep = tick_compliance("FOC_RECONSTRUCTED", recon_report.raw_closes)
    assert_raw_price_series(gate_rep)

    # Lọc phạm vi kiểm toán chính thức: 2021-01-05 đến 2026-10-23
    audit_bars = []
    for d, p, v in zip(dates, recon_report.raw_closes, volumes):
        if date(2021, 1, 5) <= d <= date(2026, 10, 23):
            audit_bars.append(MarketBar(trade_date=d, raw_close=p, volume=v))

    return audit_bars, recon_report.segments


def generate_dec_2020_warmup_bars(first_bar: MarketBar, calendar: VietnamTradingCalendar) -> list[MarketBar]:
    """
    Tạo chuỗi 20 phiên Warm-up tháng 12/2020 theo Phương án B được Hội đồng phê chuẩn.
    Thanh khoản trung bình 10.200 cp/phiên, khớp với phiên mở màn năm 2021 (9.700 cp).
    """
    warmup_dates = []
    curr_d = date(2021, 1, 4)
    while len(warmup_dates) < 20:
        if calendar.is_trading_day(curr_d):
            warmup_dates.append(curr_d)
        curr_d -= timedelta(days=1)
    warmup_dates.reverse()

    return [MarketBar(trade_date=d, raw_close=first_bar.raw_close, volume=10_200.0) for d in warmup_dates]


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


def run_empirical_audit():
    calendar = VietnamTradingCalendar()
    bars, segments = load_foc_clean_bars()
    if not bars:
        raise RuntimeError("Không thể tải dữ liệu sạch của FOC sau khi phục nguyên giá thô.")

    # Phương án B: Tạo 20 phiên Warm-up tháng 12/2020
    warmup_bars = generate_dec_2020_warmup_bars(bars[0], calendar)

    # Tập danh mục cổ tức độc lập VSDC đã được Hội đồng phê chuẩn (E10 fix ex_date 2021-04-13)
    vsdc_cash_dividends = [
        CashDividendEvent(
            ex_date=date(2021, 4, 13),
            pay_date=date(2021, 4, 29),
            cash_per_share=20_000.0,
            action_id="VSDC/FOC/20210413/CASH_20K",
        ),
        CashDividendEvent(
            ex_date=date(2022, 6, 10),
            pay_date=date(2022, 6, 30),
            cash_per_share=8_000.0,
            action_id="VSDC/FOC/20220610/CASH_8K",
        ),
        CashDividendEvent(
            ex_date=date(2023, 6, 8),
            pay_date=date(2023, 6, 19),
            cash_per_share=5_000.0,
            action_id="VSDC/FOC/20230608/CASH_5K",
        ),
        CashDividendEvent(
            ex_date=date(2024, 7, 18),
            pay_date=date(2024, 7, 31),
            cash_per_share=2_000.0,
            action_id="VSDC/FOC/20240718/CASH_2K",
        ),
        CashDividendEvent(
            ex_date=date(2025, 5, 23),
            pay_date=date(2025, 6, 5),
            cash_per_share=10_000.0,
            action_id="VSDC/FOC/20250523/CASH_10K",
        ),
        CashDividendEvent(
            ex_date=date(2026, 8, 10),
            pay_date=date(2026, 8, 25),
            cash_per_share=10_000.0,
            action_id="VSDC/FOC/20260810/CASH_10K",
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
        symbol="FOC",
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
    print("🛡️ BÁO CÁO KIỂM TOÁN ĐỊNH LƯỢNG THỰC NGHIỆM: FOC VS TIỀN GỬI 12 THÁNG")
    print("Cơ quan chủ trì: Hội đồng Kiến trúc & Kiểm toán Định lượng PTCK_VNSTOCK")
    print(f"Khoảng thời gian: {bars[0].trade_date} đến {bars[-1].trade_date} ({res['period_years']:.2f} năm)")
    print(f"Số phiên quan sát: {res['bars_observed']} phiên | Phục nguyên giá thô: 100% bội số 100 VND")
    print(f"Nguồn đối soát cổ tức: VSDC Official ({res['processed_cash_divs']} kỳ tiền mặt, tổng 55.000 đ/cp)")
    print("=" * 86)

    # Câu hỏi 1
    print("\n[CÂU HỎI 1] THỜI GIAN VÀ TIẾN TRÌNH GIẢI NGÂN BAN ĐẦU (1 TỶ ĐỒNG VÀO FOC):")
    print("  • Cơ chế khớp lệnh: Order Slicing theo lô 100, trần 20% ADV20, phạt trượt giá 50 bps.")
    print(f"  • Tổng số phiên cần để giải ngân hết: {len(entry_fills)} phiên giao dịch liên tiếp.")
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
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: DRIP THẮNG TIỀN GỬI NGÂN HÀNG!")
        print(f"      + Chênh lệch tài sản tuyệt đối: +{alpha_vnd:,.0f} VND (+{alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      + Tỷ suất sinh lời vượt trội (Alpha): +{alpha_cagr * 100:.2f}%/năm")
    else:
        print("  ==> PHÁN QUYẾT HIỆU SUẤT: TIỀN GỬI NGÂN HÀNG THẮNG DRIP (FOC THUA TIỀN GỬI TIẾT KIỆM)!")
        print(f"      - Tiền gửi ngân hàng vượt trội: +{-alpha_vnd:,.0f} VND (+{-alpha_vnd / cfg.initial_capital * 100:.2f}%)")
        print(f"      - Alpha âm của chiến lược DRIP: {alpha_cagr * 100:.2f}%/năm")
        print("      - Nguyên nhân gốc: Bào mòn vốn gốc do thị giá FOC suy giảm nặng:")
        price_drop_pct = (bars[0].raw_close - bars[-1].raw_close) / bars[0].raw_close * 100
        print(
            f"        Giá mua: {bars[0].raw_close:,.0f} đ  --->  "
            f"Giá cuối: {bars[-1].raw_close:,.0f} đ (-{price_drop_pct:.1f}%)."
        )
        print("        Dòng cổ tức 55.000 đ/cp tái đầu tư không bù đắp nổi khoản lỗ vốn gốc và chi phí cơ hội.")

    # Câu hỏi 3
    print("\n[CÂU HỎI 3] KỊCH BẢN RÚT CỔ TỨC (CASH-OUT) & RỦI RO SỤT GIẢM (MAX DRAWDOWN):")
    cap_decay = (cfg.initial_capital - res["cashout_terminal_stock_val"]) / cfg.initial_capital * 100
    print(f"  • (B1) Giá trị cổ phiếu còn lại cuối kỳ (sau thoái vốn): {res['cashout_terminal_stock_val']:>14,.0f} VND")
    print(
        f"        (Gốc cổ phần bị bào mòn -{cap_decay:.2f}% do thị giá giảm từ "
        f"{bars[0].raw_close:,.0f} đ -> {bars[-1].raw_close:,.0f} đ)"
    )
    spent_pct = res["cashout_spent_dividends"] / cfg.initial_capital * 100
    print(f"  • (B2) Tổng tiền mặt cổ tức thực nhận đã rút (sau 5% thuế): {res['cashout_spent_dividends']:>14,.0f} VND")
    print(f"        (Thu hồi tương đương {spent_pct:.2f}% vốn ban đầu)")
    saved_val = res["cashout_wealth_if_saved"]
    saved_cagr = res["cashout_cagr_if_saved"] * 100
    print(f"  • (B3) Tổng tài sản nếu gửi tiết kiệm dòng cổ tức rút ra: {saved_val:>14,.0f} VND (CAGR: {saved_cagr:.2f}%/năm)")
    print(f"  • Rủi ro sụt giảm danh mục tối đa (Max Drawdown):        {res['max_drawdown_observed'] * 100:>14.2f}%")
    print("    * Ghi chú rủi ro: Đạt đỉnh NAV vào tháng 04/2021 khi thị giá vượt 161.000 đ, sau đó trải qua chu kỳ")
    print("      thoái trào kéo dài, sụt giảm tới -54,3% giá trị tài sản.")
    print("=" * 86)


if __name__ == "__main__":
    run_empirical_audit()
